"""Entry points of the forecast layer for the orchestrator, simulator, API and AI tools.

* :func:`run_backtests` — backtests → ``model_registry`` + ``model_scores`` (+ champions)
* :func:`fit_all` — FitContext at an origin, demand groups written to ``skus``, champion
  model fitted for every SKU
* :func:`forecast_skus` / :func:`forecast_sku` — :class:`ForecastResult` with label, written
  to ``forecasts``
* :func:`scan_drift` — CUSUM of actual hours vs a forecast (03 §9), ``DriftAlarm``s
* :func:`set_demand_group` — user override of the demand group (FC-08)

All times are tz-aware UTC. Randomness is seeded (``seed``, default 20261111).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from app.core.logging import get_logger
from app.core.timeutil import utcnow
from app.forecast.backtest import (
    BACKTEST_ORDER,
    BACKTEST_SAMPLES,
    BacktestResult,
    BacktestSpec,
    run_backtest,
)
from app.forecast.base import (
    DEFAULT_SAMPLES,
    DEFAULT_SEED,
    DEMAND_GROUPS,
    FittedState,
    ForecastRequest,
    ForecastResult,
    ValidatedScope,
)
from app.forecast.context import FitContext, build_fit_context
from app.forecast.data import PanelMatrix, panel_matrix
from app.forecast.drift import DriftAlarm, merge_alarms, scan, scan_product
from app.forecast.labels import decide_label
from app.forecast.metrics import ScoreRow
from app.forecast.models._common import BaseForecastModel, infer_context
from app.forecast.registry import (
    NOWCAST,
    ChampionInfo,
    champion_for,
    current_champions,
    get_model,
    latest_rows,
    register_models,
    write_scores,
)
from app.panel.build import load_panel

log = get_logger(__name__)
PREDICT_WORKERS = 8


# ---------------------------------------------------------------------- loading
def load_matrix(
    conn: duckdb.DuckDBPyConnection,
    *,
    sku_ids: Sequence[str] | None = None,
    end: datetime | None = None,
) -> PanelMatrix:
    """Panel (validated by ``load_panel``) + catalogue names → dense arrays."""
    panel = load_panel(conn, list(sku_ids) if sku_ids is not None else None, None, end)
    rows = conn.execute(
        "SELECT s.sku_id, s.product_id, p.name FROM skus s LEFT JOIN products p USING (product_id)"
    ).fetchall()
    product_id = {str(r[0]): str(r[1]) for r in rows if r[1] is not None}
    names = {str(r[0]): str(r[2]) for r in rows if r[2] is not None}
    return panel_matrix(panel, product_id=product_id, names=names)


def user_groups(conn: duckdb.DuckDBPyConnection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT sku_id, demand_group FROM skus WHERE demand_group_source = 'user'"
        " AND demand_group IS NOT NULL"
    ).fetchall()
    return {str(s): str(g) for s, g in rows}


def default_origin(m: PanelMatrix) -> datetime:
    end = m.end if m.end is not None else m.start + pd.Timedelta(hours=m.n_hours)
    return pd.Timestamp(end).floor("h").to_pydatetime()


def build_context(
    conn: duckdb.DuckDBPyConnection,
    origin: datetime | None = None,
    *,
    matrix: PanelMatrix | None = None,
    censoring: bool = True,
    seed: int = DEFAULT_SEED,
    n_samples: int = DEFAULT_SAMPLES,
) -> FitContext:
    m = matrix or load_matrix(conn)
    return build_fit_context(
        m,
        origin or default_origin(m),
        censoring=censoring,
        user_groups=user_groups(conn),
        seed=seed,
        n_samples=n_samples,
    )


def write_demand_groups(conn: duckdb.DuckDBPyConnection, ctx: FitContext) -> int:
    """Write auto groups to ``skus`` (03 §6); rows set by the user are left untouched."""
    now = utcnow()
    n = 0
    for sku, g in zip(ctx.sku_ids, ctx.groups, strict=True):
        if ctx.group_source.get(sku) == "user":
            continue
        conn.execute(
            "UPDATE skus SET demand_group = ?, demand_group_source = 'auto', demand_group_at = ?"
            " WHERE sku_id = ? AND coalesce(demand_group_source, '') <> 'user'",
            [g, now, sku],
        )
        n += 1
    return n


def set_demand_group(conn: duckdb.DuckDBPyConnection, sku_id: str, group: str | None) -> None:
    """User override (``source='user'``, kept until the user changes it). ``None`` gives
    the SKU back to automatic classification."""
    if group is not None and group not in DEMAND_GROUPS:
        raise ValueError(f"Nhóm nhu cầu không hợp lệ: {group}")
    src = None if group is None else "user"
    conn.execute(
        "UPDATE skus SET demand_group = ?, demand_group_source = ?, demand_group_at = ?"
        " WHERE sku_id = ?",
        [group, src, utcnow(), sku_id],
    )


def price_plan_for(
    conn: duckdb.DuckDBPyConnection, sku_id: str, hours: Sequence[pd.Timestamp]
) -> dict[datetime, int] | None:
    """Committed prices (``sale_commitments.committed_price``) over the horizon hours."""
    if not len(hours):
        return None
    local_days = {(h + pd.Timedelta(hours=7)).date() for h in hours}
    rows = conn.execute(
        "SELECT day_local, value_int FROM sale_commitments WHERE kind = 'committed_price'"
        " AND sku_id = ? AND value_int IS NOT NULL",
        [sku_id],
    ).fetchall()
    by_day = {d: int(v) for d, v in rows if d in local_days}
    if not by_day:
        return None
    plan = {}
    for h in hours:
        d = (h + pd.Timedelta(hours=7)).date()
        if d in by_day:
            plan[h.to_pydatetime()] = by_day[d]
    return plan or None


# ---------------------------------------------------------------------- backtests
def run_backtests(
    conn: duckdb.DuckDBPyConnection,
    *,
    specs: Sequence[BacktestSpec] | None = None,
    matrix: PanelMatrix | None = None,
    persist: bool = True,
    n_samples: int = BACKTEST_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> BacktestResult:
    """Run the rolling-origin backtests and (``persist``) write ``model_registry`` and
    ``model_scores`` with ``is_champion`` (03 §8)."""
    m = matrix or load_matrix(conn)
    incumbents = current_champions(conn) if persist else {}
    res = run_backtest(m, specs, n_samples=n_samples, seed=seed, incumbents=incumbents)
    if persist:
        now = utcnow()
        register_models(conn, now)
        write_scores(conn, res.scores, m.panel_version, now)
    return res


# ---------------------------------------------------------------------- forecasts
@dataclass
class FitAll:
    ctx: FitContext
    states: dict[str, FittedState]
    champions: dict[str, ChampionInfo]
    score_rows: list[ScoreRow] = field(default_factory=list)


def _champion(
    ctx: FitContext, i: int, rows: list[ScoreRow], horizon: int, drift: bool
) -> tuple[ChampionInfo, str]:
    cal = ctx.horizon_calendar(horizon)
    context = infer_context(ctx, i, cal)
    info = champion_for(rows, ctx.groups[i], context, BACKTEST_ORDER)
    if drift:
        info = ChampionInfo(
            get_model(NOWCAST).model_id, info.scope, info.group_scored, info.from_backtest
        )
    return info, context


def fit_all(
    conn: duckdb.DuckDBPyConnection,
    origin: datetime | None = None,
    *,
    sku_ids: Iterable[str] | None = None,
    horizon_hours: int = 72,
    drift_active: Iterable[str] = (),
    matrix: PanelMatrix | None = None,
    ctx: FitContext | None = None,
    write_groups: bool = True,
    seed: int = DEFAULT_SEED,
    n_samples: int = DEFAULT_SAMPLES,
) -> FitAll:
    """FitContext at ``origin`` (default: end of the panel) and the champion of every SKU
    fitted. SKUs in ``drift_active`` use ``nowcast``."""
    ctx = ctx or build_context(conn, origin, matrix=matrix, seed=seed, n_samples=n_samples)
    if write_groups:
        write_demand_groups(conn, ctx)
    rows = latest_rows(conn)
    drift = set(drift_active)
    wanted = list(sku_ids) if sku_ids is not None else list(ctx.sku_ids)
    states: dict[str, FittedState] = {}
    champs: dict[str, ChampionInfo] = {}
    for sku in wanted:
        i = ctx.idx(sku)
        info, _ = _champion(ctx, i, rows, horizon_hours, sku in drift)
        model = get_model(info.model_id)
        states[sku] = model.fit(None, sku, ctx.origin_dt, ctx)
        champs[sku] = info
    return FitAll(ctx=ctx, states=states, champions=champs, score_rows=rows)


def _scope(info: ChampionInfo, group: str, context: str) -> ValidatedScope:
    s = info.scope
    if s is None:
        return ValidatedScope(model_id=info.model_id, demand_group=group, context=context)
    return ValidatedScope(
        model_id=s.model_id,
        demand_group=s.demand_group,
        context=s.context,
        backtest=s.backtest,
        wape=s.wape,
        bias=s.bias,
        coverage=s.coverage,
        wape_floor=s.wape_floor,
        pinball_50=s.pinball_50,
        n_skus=s.n_skus,
        n_sku_days=s.n_sku_days,
    )


def input_hash(res: ForecastResult, price_plan: dict[datetime, int] | None) -> str:
    payload = {
        "sku": res.sku_id,
        "model": res.model_id,
        "origin": res.origin.isoformat(),
        "h": len(res.hours),
        "panel": res.panel_version,
        "price": sorted((k.isoformat(), v) for k, v in (price_plan or {}).items()),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]


def persist_forecast(
    conn: duckdb.DuckDBPyConnection, res: ForecastResult, ihash: str | None = None
) -> None:
    conn.execute(
        "INSERT INTO forecasts (forecast_id, sku_id, model_id, origin, horizon_hours, payload,"
        " confidence, reasons, data_as_of, panel_version, created_at, input_hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            res.forecast_id,
            res.sku_id,
            res.model_id,
            res.origin,
            len(res.hours),
            res.model_dump_json(),
            res.confidence,
            list(res.reasons),
            res.data_as_of,
            res.panel_version,
            utcnow(),
            ihash,
        ],
    )


def forecast_skus(
    conn: duckdb.DuckDBPyConnection,
    sku_ids: Iterable[str] | None = None,
    origin: datetime | None = None,
    horizon_hours: int = 72,
    *,
    drift_active: Iterable[str] = (),
    persist: bool = True,
    use_price_plan: bool = True,
    fitted: FitAll | None = None,
    matrix: PanelMatrix | None = None,
    seed: int = DEFAULT_SEED,
    n_samples: int = DEFAULT_SAMPLES,
) -> list[ForecastResult]:
    """Champion forecast (label + reasons) for each SKU; written to ``forecasts``."""
    drift = set(drift_active)
    fa = fitted or fit_all(
        conn,
        origin,
        sku_ids=sku_ids,
        horizon_hours=horizon_hours,
        drift_active=drift,
        matrix=matrix,
        seed=seed,
        n_samples=n_samples,
    )
    ctx = fa.ctx
    out: list[ForecastResult] = []
    hours = ctx.horizon_calendar(horizon_hours).hours
    jobs: list[tuple[BaseForecastModel, FittedState, ForecastRequest]] = []
    plans: dict[str, dict[datetime, int] | None] = {}
    for sku, state in fa.states.items():
        plan = price_plan_for(conn, sku, list(hours)) if use_price_plan else None
        plans[sku] = plan
        req = ForecastRequest(
            sku_id=sku, origin=ctx.origin_dt, horizon_hours=horizon_hours, price_plan=plan
        )
        jobs.append((get_model(fa.champions[sku].model_id), state, req))
    for (model, state, _req), res in zip(jobs, predict_many(jobs), strict=True):
        sku = state.sku_id
        info = fa.champions[sku]
        plan = plans[sku]
        is_now = model.name == NOWCAST
        dec = decide_label(
            info.scope,
            state.history_days,
            sku in drift,
            group_scored=info.group_scored,
            nowcast=is_now,
        )
        res = res.model_copy(
            update={
                "validated_scope": _scope(info, state.demand_group, res.context),
                "confidence": dec.label,
                "reasons": list(res.reasons) + dec.reasons,
            }
        )
        if persist:
            persist_forecast(conn, res, input_hash(res, plan))
        out.append(res)
    log.info("forecasts_done", n=len(out), origin=ctx.origin.isoformat(), persist=persist)
    return out


def predict_many(
    jobs: Sequence[tuple[BaseForecastModel, FittedState, ForecastRequest]],
    workers: int = PREDICT_WORKERS,
) -> list[ForecastResult]:
    """``model.predict`` for many SKUs in a thread pool: sampling is numpy code that
    releases the GIL, so 300 SKUs × 72 h run in ~1 s instead of ~4 s (03 §10)."""
    if len(jobs) < 8 or workers <= 1:
        return [m.predict(s, r) for m, s, r in jobs]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda j: j[0].predict(j[1], j[2]), jobs))


def forecast_sku(
    conn: duckdb.DuckDBPyConnection,
    sku_id: str,
    origin: datetime | None = None,
    horizon_hours: int = 72,
    **kw: Any,
) -> ForecastResult:
    return forecast_skus(conn, [sku_id], origin, horizon_hours, **kw)[0]


# ---------------------------------------------------------------------- drift
def scan_drift(
    matrix: PanelMatrix,
    results: Iterable[ForecastResult],
    until: datetime,
    *,
    pooled: bool = True,
) -> list[DriftAlarm]:
    """CUSUM of the actual hours in ``[origin, until)`` against each forecast's mean, on
    in-stock hours (03 §9). The orchestrator publishes ``alarm.event()`` on the bus.

    [DEV] ``pooled`` (default): for a product with ≥ 2 variants sharing the forecast hours,
    ``up`` comes from the product shock detector (``drift.scan_product``: NegBin LLR CUSUM
    of "demand 2× forecast" summed over the variants in stock); ``down`` stays per SKU.
    Single-variant products keep the per-SKU CUSUM both ways. M2 "Hoàn thiện M2"."""
    idx = matrix.index
    stop = pd.Timestamp(until).tz_convert("UTC")
    rows: list[tuple[str, str, pd.Timestamp, int, int, ForecastResult]] = []
    for res in results:
        i = idx.get(res.sku_id)
        if i is None:
            continue
        hours = [pd.Timestamp(h) for h in res.hours]
        n = sum(1 for h in hours if h < stop)
        if n == 0:
            continue
        t0 = matrix.hour_index(hours[0])
        t1 = min(t0 + n, matrix.n_hours)
        if t1 <= t0:
            continue
        pid = matrix.product_id.get(res.sku_id) or res.sku_id.split("-", 1)[0]
        rows.append((res.sku_id, pid, hours[0], i, t1 - t0, res))
    groups: dict[tuple[str, pd.Timestamp, int], list[int]] = {}
    for j, (_, pid, h0, _, n, _) in enumerate(rows):
        groups.setdefault((pid, h0, n), []).append(j)
    multi = {j for g in groups.values() if pooled and len(g) >= 2 for j in g}
    own: list[DriftAlarm] = []
    for j, (sku, _, h0, i, n, res) in enumerate(rows):
        t0 = matrix.hour_index(h0)
        ins = matrix.present[i, t0 : t0 + n] & ~matrix.stockout[i, t0 : t0 + n]
        hrs = [(h0 + pd.Timedelta(hours=q)).to_pydatetime() for q in range(n)]
        lam = np.asarray(res.mean[:n], dtype=np.float64)
        found = scan(sku, hrs, matrix.y[i, t0 : t0 + n], lam, res.dispersion_k, ins)
        own += [a for a in found if not (j in multi and a.direction == "up")]
    product: list[DriftAlarm] = []
    for (_, h0, n), members in groups.items():
        if not pooled or len(members) < 2:
            continue
        rws = [rows[j][3] for j in members]
        t0 = matrix.hour_index(h0)
        ins = matrix.present[rws, t0 : t0 + n] & ~matrix.stockout[rws, t0 : t0 + n]
        lam = np.vstack([np.asarray(rows[j][5].mean[:n], dtype=np.float64) for j in members])
        product += scan_product(
            [rows[j][0] for j in members],
            [(h0 + pd.Timedelta(hours=q)).to_pydatetime() for q in range(n)],
            matrix.y[rws, t0 : t0 + n],
            lam,
            [rows[j][5].dispersion_k for j in members],
            ins,
        )
    return merge_alarms(own, product)
