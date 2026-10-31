"""Rolling-origin backtest (FC-09, plan 03 §7.1).

Never lets data after ``origin`` into a fit: each origin builds its own FitContext from
``data < origin`` (``context.build_fit_context``). Scoring **inside the app** compares with
what the app has, i.e. observed sales; stockout hours are left out of the metrics (both
actual and forecast). Results feed ``model_scores``, champion selection and labels.

The external harness (``backend/eval/eval_forecast.py``) reuses :func:`run_backtest` with
``hourly=True`` and scores the same forecasts against true demand.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import numpy.typing as npt
import pandas as pd

from app.core.logging import get_logger
from app.forecast.base import Context, ForecastRequest, nb_samples
from app.forecast.context import FitContext, build_fit_context
from app.forecast.data import PanelMatrix, vn_midnight_utc
from app.forecast.metrics import (
    MIN_WAPE_RATE,
    ScoreRow,
    floor_stats,
    pinball_sum,
    randomized_pit,
    score_table,
)
from app.forecast.models._common import BaseForecastModel, stable_seed
from app.forecast.registry import BACKTEST_MODELS, MODELS, select_champions

log = get_logger(__name__)
FloatArr = npt.NDArray[np.float64]
BACKTEST_SAMPLES = 500


@dataclass(frozen=True)
class BacktestSpec:
    name: str
    origins: tuple[datetime, ...]  # UTC
    horizon_hours: int
    context: Context


def default_specs(year: int = 2026) -> list[BacktestSpec]:
    """The six windows of 03 §7.1 (origins at VN midnight)."""

    def o(m: int, d: int) -> datetime:
        return vn_midnight_utc(date(year, m, d))

    roll = tuple(
        vn_midnight_utc(d.date()) for d in pd.date_range(f"{year}-10-28", f"{year}-11-10", freq="D")
    )
    return [
        BacktestSpec("dd_0808", (o(8, 7),), 72, "double_day"),
        BacktestSpec("dd_0909", (o(9, 8),), 72, "double_day"),
        BacktestSpec("dd_1010", (o(10, 9),), 72, "double_day"),
        BacktestSpec("gift_1020", (o(10, 15),), 168, "gift_season"),
        BacktestSpec("normal_roll", roll, 24, "normal"),
        BacktestSpec("live_1111", (o(11, 11),), 24, "double_day"),
    ]


BACKTEST_ORDER: dict[str, int] = {s.name: i for i, s in enumerate(default_specs())}


def available_specs(m: PanelMatrix, specs: Sequence[BacktestSpec]) -> list[BacktestSpec]:
    """Keep origins whose whole horizon is inside the panel (and has history before)."""
    out = []
    end = m.end if m.end is not None else m.start + pd.Timedelta(hours=m.n_hours)
    for s in specs:
        ok = tuple(
            o
            for o in s.origins
            if pd.Timestamp(o) > m.start + pd.Timedelta(days=7)
            and pd.Timestamp(o) + pd.Timedelta(hours=s.horizon_hours) <= end
        )
        if ok:
            out.append(BacktestSpec(s.name, ok, s.horizon_hours, s.context))
    return out


@dataclass
class BacktestResult:
    days: pd.DataFrame  # per (model, backtest, origin, sku, day) records
    scores: list[ScoreRow]
    champions: dict[tuple[str, str], str]
    hours: pd.DataFrame | None = None  # per hour records (``hourly=True``)
    groups: dict[str, dict[str, str]] = field(default_factory=dict)  # backtest → sku → group
    seconds: float = 0.0
    changes: list[object] = field(default_factory=list)


def _day_groups(day: npt.NDArray[np.datetime64]) -> tuple[npt.NDArray[np.datetime64], FloatArr]:
    uniq, inv = np.unique(day, return_inverse=True)
    g = np.zeros((len(day), len(uniq)))
    g[np.arange(len(day)), inv] = 1.0
    return uniq, g


def forecast_origin(
    m: PanelMatrix,
    origin: datetime,
    horizon: int,
    context: Context,
    *,
    backtest: str,
    models: Sequence[str] = BACKTEST_MODELS,
    censoring: bool = True,
    n_samples: int = BACKTEST_SAMPLES,
    seed: int = 20261111,
    hourly: bool = False,
    keep_samples: bool = False,
    ctx: FitContext | None = None,
    groups: dict[str, str] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], FitContext]:
    """Fit + predict every applicable model for every SKU at one origin and score the
    horizon against observed sales (in-stock hours). Returns (day rows, hour rows, ctx)."""
    if ctx is None:
        ctx = build_fit_context(
            m, origin, censoring=censoring, groups=groups, seed=seed, n_samples=n_samples
        )
    t0 = ctx.t0
    cal = ctx.horizon_calendar(horizon)
    t1 = min(t0 + horizon, m.n_hours)
    n_obs = t1 - t0
    y_act = m.y[:, t0:t1]
    instock = m.present[:, t0:t1] & ~m.stockout[:, t0:t1]
    days, g_full = _day_groups(cal.day[:n_obs])
    day_rows: list[dict[str, object]] = []
    hour_rows: list[dict[str, object]] = []
    lo28 = max(0, ctx.n_days - 28)
    rate28 = ctx.demand_adj[:, lo28 : ctx.n_days].mean(axis=1) if ctx.n_days else np.zeros(1)
    for i, sku in enumerate(ctx.sku_ids):
        if ctx.history_days(i) <= 0:
            continue
        g = ctx.groups[i]
        eligible = bool(rate28[i] >= MIN_WAPE_RATE)
        mask = instock[i].astype(np.float64)
        g_mask = g_full * mask[:, None]
        y_day = y_act[i] @ g_mask
        for name in models:
            model: BaseForecastModel = MODELS[name]
            if g not in model.applicable_groups:
                continue
            state = model.fit(None, sku, ctx.origin_dt, ctx)
            req = ForecastRequest(
                sku_id=sku, origin=ctx.origin_dt, horizon_hours=horizon, context=context
            )
            mf = model.mean_forecast(state, req)
            rng = np.random.default_rng(stable_seed(seed, sku, ctx.origin.isoformat(), name))
            smp = nb_samples(mf.mean, mf.dispersion_k, mf.level_sigma, rng, n_samples)[:, :n_obs]
            hq = np.quantile(smp, [0.1, 0.5, 0.9], axis=0)
            day_m = smp @ g_mask
            day_f = smp @ g_full
            dq = np.quantile(day_m, [0.1, 0.5, 0.9], axis=0)
            fq = np.quantile(day_f, [0.1, 0.5, 0.9], axis=0)
            pit_u = randomized_pit(day_m, y_day, rng)
            # floor: noise the model believes in around its mean (NegBin k), without its
            # own level uncertainty (M2 §D)
            frng = np.random.default_rng(
                stable_seed(seed, sku, ctx.origin.isoformat(), name, "floor")
            )
            fday = nb_samples(mf.mean, mf.dispersion_k, 0.0, frng, n_samples)[:, :n_obs] @ g_mask
            f_mae, f_mean = floor_stats(fday, np.median(fday, axis=0))
            mean = mf.mean[:n_obs]
            sel = mask > 0
            for j, d in enumerate(days):
                in_day = (g_full[:, j] > 0) & sel
                yy = y_act[i][in_day]
                day_rows.append(
                    {
                        "model_id": model.model_id,
                        "backtest": backtest,
                        "origin": ctx.origin_dt,
                        "sku_id": sku,
                        "demand_group": g,
                        "context": context,
                        "day": pd.Timestamp(d).date(),
                        "y": float(y_day[j]),
                        "p10": float(dq[0, j]),
                        "p50": float(dq[1, j]),
                        "p90": float(dq[2, j]),
                        "mean": float(mean @ g_mask[:, j]),
                        "full_p10": float(fq[0, j]),
                        "full_p50": float(fq[1, j]),
                        "full_p90": float(fq[2, j]),
                        "full_mean": float(mean @ g_full[:, j]),
                        "wape_eligible": eligible,
                        "pb10": pinball_sum(yy, hq[0][in_day], 0.1),
                        "pb50": pinball_sum(yy, hq[1][in_day], 0.5),
                        "pb90": pinball_sum(yy, hq[2][in_day], 0.9),
                        "n_hours": int(in_day.sum()),
                        "phase": mf.phase,
                        "pit_u": float(pit_u[j]),
                        "floor_mae": float(f_mae[j]),
                        "floor_mean": float(f_mean[j]),
                        "k": mf.dispersion_k,
                        "sigma": mf.level_sigma,
                        "full_samples": day_f[:, j].astype(np.float32) if keep_samples else None,
                    }
                )
            if hourly:
                for t in range(n_obs):
                    hour_rows.append(
                        {
                            "model_id": model.model_id,
                            "backtest": backtest,
                            "sku_id": sku,
                            "hour_utc": cal.hours[t],
                            "mean": float(mean[t]),
                            "p10": float(hq[0, t]),
                            "p50": float(hq[1, t]),
                            "p90": float(hq[2, t]),
                            "instock": bool(sel[t]),
                        }
                    )
    return day_rows, hour_rows, ctx


def run_backtest(
    m: PanelMatrix,
    specs: Sequence[BacktestSpec] | None = None,
    *,
    models: Sequence[str] = BACKTEST_MODELS,
    censoring: bool = True,
    n_samples: int = BACKTEST_SAMPLES,
    seed: int = 20261111,
    hourly: bool = False,
    keep_samples: bool = False,
    incumbents: dict[tuple[str, str], str] | None = None,
    groups_by_origin: dict[datetime, dict[str, str]] | None = None,
) -> BacktestResult:
    """Run the backtests available in ``m``, score them and pick champions."""
    t_start = time.perf_counter()
    specs = available_specs(m, specs or default_specs())
    day_rows: list[dict[str, object]] = []
    hour_rows: list[dict[str, object]] = []
    groups: dict[str, dict[str, str]] = {}
    for spec in specs:
        for origin in spec.origins:
            g_fixed = (groups_by_origin or {}).get(origin)
            d, h, ctx = forecast_origin(
                m,
                origin,
                spec.horizon_hours,
                spec.context,
                backtest=spec.name,
                models=models,
                censoring=censoring,
                n_samples=n_samples,
                seed=seed,
                hourly=hourly,
                keep_samples=keep_samples,
                groups=g_fixed,
            )
            day_rows += d
            hour_rows += h
            groups.setdefault(spec.name, {}).update(dict(zip(ctx.sku_ids, ctx.groups, strict=True)))
            groups[f"{spec.name}@{pd.Timestamp(origin).isoformat()}"] = dict(
                zip(ctx.sku_ids, ctx.groups, strict=True)
            )
    days = pd.DataFrame(day_rows)
    scores: list[ScoreRow] = []
    if not days.empty:
        scores = score_table(days)
        pooled = days.assign(backtest="pooled_" + days["context"].astype(str))
        scores += score_table(pooled)
    champions, changes = select_champions(scores, BACKTEST_ORDER, incumbents)
    res = BacktestResult(
        days=days,
        scores=scores,
        champions=champions,
        hours=pd.DataFrame(hour_rows) if hourly else None,
        groups=groups,
        seconds=time.perf_counter() - t_start,
        changes=list(changes),
    )
    log.info(
        "backtest_done",
        specs=[s.name for s in specs],
        rows=len(days),
        seconds=round(res.seconds, 1),
        censoring=censoring,
    )
    return res
