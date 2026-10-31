"""Model registry and champion/challenger (FC-09, plan 03 §8).

Adding a model = one file in ``forecast/models/`` + one line in ``MODEL_CLASSES``.

Champion per ``(demand_group, context)``:

1. among models whose ``applicable_groups`` contain the group, lowest WAPE (pinball_50
   for sparse groups); if the best two are within 2 WAPE points, lower pinball_50 wins;
2. a challenger replaces the champion only when it is ≥ 5 % better (relative WAPE) in
   **2 consecutive backtests** of the same context;
3. ``is_champion`` is written on the champion's rows; the history of champion changes is
   kept in ``model_scores`` (one set of rows per run, ``created_at``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

import duckdb

from app.core.ids import new_id
from app.core.timeutil import utcnow
from app.forecast.metrics import ScoreRow
from app.forecast.models._common import BaseForecastModel
from app.forecast.models.analog import Analog
from app.forecast.models.nowcast import Nowcast
from app.forecast.models.profile_ewma import ProfileEwma
from app.forecast.models.seasonal_calendar import SeasonalCalendar
from app.forecast.models.trend_damped import TrendDamped
from app.forecast.models.tsb import Tsb

MODEL_CLASSES: tuple[type[BaseForecastModel], ...] = (
    ProfileEwma,
    SeasonalCalendar,
    TrendDamped,
    Tsb,
    Analog,
    Nowcast,
)
MODELS: dict[str, BaseForecastModel] = {cls.name: cls() for cls in MODEL_CLASSES}
NOWCAST = "nowcast"
# nowcast only runs on a drift alarm (03 §3.6), it does not compete in backtests
BACKTEST_MODELS: tuple[str, ...] = tuple(n for n in MODELS if n != NOWCAST)
# used before any backtest exists
DEFAULT_CHAMPION: dict[str, str] = {
    "on_dinh": "profile_ewma",
    "mua_vu": "seasonal_calendar",
    "trend": "trend_damped",
    "ban_thua": "tsb",
    "moi": "analog",
    "unclassified": "profile_ewma",
}
TIE_WAPE = 0.02
CHALLENGE_GAIN = 0.05


def get_model(name_or_id: str) -> BaseForecastModel:
    name = name_or_id.split("@", 1)[0]
    try:
        return MODELS[name]
    except KeyError as exc:
        raise KeyError(f"Không có mô hình {name_or_id}") from exc


def models_for(group: str, *, backtest_only: bool = True) -> list[BaseForecastModel]:
    names = BACKTEST_MODELS if backtest_only else tuple(MODELS)
    return [MODELS[n] for n in names if group in MODELS[n].applicable_groups]


def register_models(conn: duckdb.DuckDBPyConnection, now: datetime | None = None) -> list[str]:
    """Upsert every model into ``model_registry`` (code_hash tells when code changed)."""
    ts = now or utcnow()
    ids = []
    for m in MODELS.values():
        conn.execute(
            "INSERT INTO model_registry (model_id, name, version, kind, params, code_hash,"
            " created_at) VALUES (?, ?, ?, 'forecast', ?, ?, ?) ON CONFLICT (model_id) DO UPDATE"
            " SET params = excluded.params, code_hash = excluded.code_hash",
            [
                m.model_id,
                m.name,
                m.version,
                json.dumps(m.params, ensure_ascii=False, default=str),
                m.code_hash(),
                ts,
            ],
        )
        ids.append(m.model_id)
    return ids


# ---------------------------------------------------------------------- champions
def _best(rows: Sequence[ScoreRow]) -> ScoreRow | None:
    def pinball_score(row: ScoreRow) -> float:
        # Zero is a valid, perfect score; only a missing metric ranks last.
        return row.pinball_50 if row.pinball_50 is not None else float("inf")

    cands = sorted(rows, key=lambda r: (r.key_metric, pinball_score(r)))
    if not cands:
        return None
    best = cands[0]
    if best.wape is not None and len(cands) > 1:
        second = cands[1]
        if (
            second.wape is not None
            and second.wape - best.wape < TIE_WAPE
            and pinball_score(second) < pinball_score(best)
        ):
            return second
    return best


def _beats(chal: ScoreRow | None, champ: ScoreRow | None) -> bool:
    if chal is None or champ is None:
        return False
    return chal.key_metric <= (1 - CHALLENGE_GAIN) * champ.key_metric


@dataclass
class ChampionChange:
    demand_group: str
    context: str
    backtest: str
    old: str | None
    new: str


def select_champions(
    rows: list[ScoreRow],
    order: dict[str, int],
    incumbents: dict[tuple[str, str], str] | None = None,
) -> tuple[dict[tuple[str, str], str], list[ChampionChange]]:
    """Mark ``is_champion`` on ``rows`` (in place). ``order`` = chronological rank of each
    backtest; ``incumbents`` = champions of a previous run (rule 2 applies to them too).
    Pooled rows (``backtest`` starting with ``pooled_``) get the final champion."""
    by_gc: dict[tuple[str, str], dict[str, dict[str, ScoreRow]]] = {}
    for r in rows:
        if r.backtest.startswith("pooled_"):
            continue
        by_gc.setdefault((r.demand_group, r.context), {}).setdefault(r.backtest, {})[r.model_id] = r
    final: dict[tuple[str, str], str] = {}
    changes: list[ChampionChange] = []
    for gc, per_bt in by_gc.items():
        seq = sorted(per_bt, key=lambda b: order.get(b, 0))
        champ = (incumbents or {}).get(gc)
        prev_bt: str | None = None
        for bt in seq:
            scores = per_bt[bt]
            if champ is None or champ not in scores:
                best = _best(list(scores.values()))
                if best is None:
                    continue
                if champ != best.model_id:
                    changes.append(ChampionChange(gc[0], gc[1], bt, champ, best.model_id))
                champ = best.model_id
            else:
                best = _best([r for r in scores.values() if r.model_id != champ])
                if (
                    best is not None
                    and prev_bt is not None
                    and _beats(best, scores[champ])
                    and _beats(per_bt[prev_bt].get(best.model_id), per_bt[prev_bt].get(champ))
                ):
                    changes.append(ChampionChange(gc[0], gc[1], bt, champ, best.model_id))
                    champ = best.model_id
            scores[champ].is_champion = True
            prev_bt = bt
        if champ is not None:
            final[gc] = champ
    for r in rows:
        if (
            r.backtest.startswith("pooled_")
            and final.get((r.demand_group, r.context)) == r.model_id
        ):
            r.is_champion = True
    return final, changes


def write_scores(
    conn: duckdb.DuckDBPyConnection,
    rows: Iterable[ScoreRow],
    panel_version: str,
    created_at: datetime | None = None,
) -> int:
    ts = created_at or utcnow()
    n = 0
    for r in rows:
        conn.execute(
            "INSERT INTO model_scores (score_id, model_id, backtest, demand_group, context, n_skus,"
            " n_sku_days, wape, bias, pinball_10, pinball_50, pinball_90, coverage, is_champion,"
            " panel_version, created_at, wape_floor)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                new_id("sc"),
                r.model_id,
                r.backtest,
                r.demand_group,
                r.context,
                r.n_skus,
                r.n_sku_days,
                r.wape,
                r.bias,
                r.pinball_10,
                r.pinball_50,
                r.pinball_90,
                r.coverage,
                r.is_champion,
                panel_version,
                ts,
                r.wape_floor,
            ],
        )
        n += 1
    return n


def _row(d: dict[str, object]) -> ScoreRow:
    def f(k: str) -> float | None:
        v = d.get(k)
        return None if v is None else float(v)  # type: ignore[arg-type]

    return ScoreRow(
        model_id=str(d["model_id"]),
        backtest=str(d["backtest"]),
        demand_group=str(d["demand_group"]),
        context=str(d["context"]),
        n_skus=int(d["n_skus"] or 0),  # type: ignore[call-overload]
        n_sku_days=int(d["n_sku_days"] or 0),  # type: ignore[call-overload]
        wape=f("wape"),
        bias=f("bias"),
        pinball_10=f("pinball_10"),
        pinball_50=f("pinball_50"),
        pinball_90=f("pinball_90"),
        coverage=f("coverage"),
        is_champion=bool(d["is_champion"]),
        wape_floor=f("wape_floor"),
    )


def latest_rows(conn: duckdb.DuckDBPyConnection) -> list[ScoreRow]:
    """Score rows of the latest backtest run."""
    cur = conn.execute(
        "SELECT * FROM model_scores WHERE created_at = (SELECT max(created_at) FROM model_scores)"
    )
    names = [d[0] for d in cur.description or []]
    return [_row(dict(zip(names, r, strict=True))) for r in cur.fetchall()]


def current_champions(conn: duckdb.DuckDBPyConnection) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for r in latest_rows(conn):
        if r.is_champion and r.backtest.startswith("pooled_"):
            out[(r.demand_group, r.context)] = r.model_id
    return out


@dataclass
class ChampionInfo:
    model_id: str
    scope: ScoreRow | None
    group_scored: bool
    from_backtest: bool


def champion_for(
    rows: Sequence[ScoreRow], group: str, context: str, order: dict[str, int]
) -> ChampionInfo:
    """Champion and its validated scope for (group, context) from score rows of one run.

    Scope = the champion's row on the most recent backtest of that context with ≥ 30
    SKU-days; otherwise its pooled row over every backtest of the context."""
    group_rows = [r for r in rows if r.demand_group == group]
    ctx_rows = [r for r in group_rows if r.context == context and r.is_champion]
    pooled = [r for r in ctx_rows if r.backtest.startswith("pooled_")]
    if not pooled:
        name = DEFAULT_CHAMPION.get(group, "profile_ewma")
        return ChampionInfo(MODELS[name].model_id, None, bool(group_rows), False)
    champ = pooled[0].model_id
    singles = sorted(
        (r for r in ctx_rows if r.model_id == champ and not r.backtest.startswith("pooled_")),
        key=lambda r: order.get(r.backtest, 0),
        reverse=True,
    )
    scope = next((r for r in singles if r.n_sku_days >= 30), pooled[0])
    return ChampionInfo(champ, scope, True, True)
