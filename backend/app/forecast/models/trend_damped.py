"""``trend_damped`` (FC-04, plan 03 §3.3): Holt with damped trend on ``log(1 + x)`` of the
deseasonalised daily demand, ``α = 0.4, β = 0.2, φ = 0.85``; spread to hours with ``P_h``.

Phase (in ``reasons`` and ``ForecastResult.phase``) from the 7-day slope of ``log(1 + x)``:
``tăng`` (> +5 %/day), ``nguội`` (< −3 %/day), otherwise ``đỉnh``.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np
import numpy.typing as npt

from app.forecast.base import FittedState
from app.forecast.context import FitContext
from app.forecast.data import Calendar
from app.forecast.models._common import BaseForecastModel, day_index_of

FloatArr = npt.NDArray[np.float64]

ALPHA, BETA, PHI = 0.4, 0.2, 0.85
WINDOW = 56
SIGMA_FLOOR = 0.15
UP, DOWN = 0.05, -0.03


def holt_damped(
    z: FloatArr, alpha: float = ALPHA, beta: float = BETA, phi: float = PHI
) -> tuple[float, float, FloatArr]:
    """Level, trend and one-step errors of damped Holt on ``z`` (NaN = missing day)."""
    obs = z[np.isfinite(z)]
    if obs.size == 0:
        return 0.0, 0.0, np.zeros(0)
    level, trend = float(obs[0]), 0.0
    errs: list[float] = []
    for v in z[1:] if np.isfinite(z[0]) else z:
        pred = level + phi * trend
        if not np.isfinite(v):
            level, trend = pred, phi * trend
            continue
        errs.append(float(v - pred))
        new_level = alpha * float(v) + (1 - alpha) * pred
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        level = new_level
    return level, trend, np.asarray(errs, dtype=np.float64)


def phase_of(slope: float) -> str:
    pct = float(np.expm1(slope))
    if pct > UP:
        return "tăng"
    if pct < DOWN:
        return "nguội"
    return "đỉnh"


PHASE_DAYS = 14


def slope7(ctx: FitContext, i: int) -> float:
    """Daily slope of ``log(1 + x)`` used for the phase: OLS over the last 14 days on the
    **product** (variants summed; they share the trend).

    [DEV] 03 §3.3 says "7-day slope". On one variant's daily counts a 7-point slope has a
    standard error of ≈ ±7 %/day for a 5 sp/day item, against a −3 %/day threshold, so
    the phase flipped day to day on ``demo``. 14 days × all variants brings it to ≈ ±2 %.
    Decision log M2 C5."""
    from app.forecast.classify import product_of

    pid = product_of(ctx, ctx.sku_ids[i])
    members = [j for j, s in enumerate(ctx.sku_ids) if product_of(ctx, s) == pid] or [i]
    d = ctx.n_days
    lo = max(0, d - PHASE_DAYS)
    valid = ctx.x_valid[members, lo:d]
    x = np.where(valid, ctx.x[members, lo:d], 0.0).sum(axis=0)
    ok = valid.any(axis=0)
    if ok.sum() < 7:
        return 0.0
    t = np.arange(lo, d)[ok].astype(np.float64)
    return float(np.polyfit(t, np.log1p(x[ok]), 1)[0])


class TrendDamped(BaseForecastModel):
    name: ClassVar[str] = "trend_damped"
    version: ClassVar[str] = "1.0.0"
    applicable_groups: ClassVar[set[str]] = {"trend", "on_dinh", "mua_vu"}
    params: ClassVar[dict[str, object]] = {"alpha": ALPHA, "beta": BETA, "phi": PHI}

    def _fit(self, state: FittedState, ctx: FitContext, i: int) -> None:
        lo = max(0, ctx.n_days - WINDOW, int(ctx.first_day[i]) if ctx.first_day[i] >= 0 else 0)
        ok = ctx.x_valid[i, lo : ctx.n_days] & (ctx.disc_day[i, lo : ctx.n_days] <= 0.02)
        z = np.where(ok, np.log1p(ctx.x[i, lo : ctx.n_days]), np.nan)
        level, trend, errs = holt_damped(z)
        tail = errs[-28:]
        s1 = float(np.std(tail)) if tail.size >= 5 else 0.5
        state.day_level = float(np.expm1(level))
        state.level_sigma = max(s1, SIGMA_FLOOR)
        state.params = {"level_log": level, "trend_log": trend, "sigma_1": s1}
        slope = slope7(ctx, i)
        state.phase = phase_of(slope)
        state.reasons.append(
            f"Pha trend: {state.phase} ({np.expm1(slope) * 100:+.0f}%/ngày, 14 ngày)"
        )

    def _day_levels(self, state: FittedState, cal: Calendar) -> FloatArr:
        ctx = state.ctx
        h = day_index_of(ctx, cal) - (ctx.n_days - 1)  # 1 = first day after the history
        h = np.clip(h, 1, None).astype(np.float64)
        phi = PHI
        damp = phi * (1 - phi**h) / (1 - phi)
        z = state.params["level_log"] + damp * state.params["trend_log"]
        # uncertainty grows with the horizon: use the mean horizon (days)
        hbar = float(np.mean(h))
        state.level_sigma = max(float(state.params["sigma_1"]) * np.sqrt(hbar), SIGMA_FLOOR)
        return np.asarray(np.clip(np.expm1(z), 0.0, None), dtype=np.float64)
