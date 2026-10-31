"""``nowcast`` (FC-07, plan 03 §3.6): demand shock inside the day.

Used only while CUSUM drift is up (``drift.py``). Re-estimates the level from the last
3–6 in-stock hours, exponential weights with a 2-hour half-life, keeping the hour profile::

    L_now = Σ_j w_j·y_j / Σ_j w_j·P_h(j)·W(j)·E(j)
    λ(t)  = L_now · P_h · W · E(t),   level_sigma = 0.5

Label at most ``reference`` (``labels``), reason "nhu cầu đang lệch mạnh so với lịch sử".
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from app.forecast.base import FittedState
from app.forecast.context import FitContext
from app.forecast.data import calendar_for
from app.forecast.models._common import BaseForecastModel
from app.forecast.models.profile_ewma import ewma_level

LOOKBACK_HOURS = 6
MIN_HOURS = 3
HALF_LIFE_H = 2.0
SIGMA = 0.5
REASON = "Nhu cầu đang lệch mạnh so với lịch sử"


class Nowcast(BaseForecastModel):
    name: ClassVar[str] = "nowcast"
    version: ClassVar[str] = "1.0.0"
    applicable_groups: ClassVar[set[str]] = {
        "on_dinh",
        "mua_vu",
        "trend",
        "moi",
        "ban_thua",
        "unclassified",
    }
    params: ClassVar[dict[str, object]] = {
        "lookback_hours": LOOKBACK_HOURS,
        "half_life_hours": HALF_LIFE_H,
        "level_sigma": SIGMA,
    }

    def _fit(self, state: FittedState, ctx: FitContext, i: int) -> None:
        lo = max(0, ctx.t0 - LOOKBACK_HOURS)
        n = ctx.t0 - lo
        state.level_sigma = SIGMA
        if n <= 0:
            self._fallback(state, ctx, i, "chưa có giờ nào")
            return
        cal = calendar_for(ctx.start + pd.Timedelta(hours=lo), n)
        y = ctx.y[i, lo : ctx.t0]
        ok = ctx.present[i, lo : ctx.t0] & ~ctx.stockout[i, lo : ctx.t0]
        if int(ok.sum()) < MIN_HOURS:
            self._fallback(state, ctx, i, f"chỉ có {int(ok.sum())} giờ còn hàng gần đây")
            return
        age = (n - 1 - np.arange(n)).astype(np.float64)
        w = 0.5 ** (age / HALF_LIFE_H) * ok
        exp = (
            state.hour_profile[cal.hour_local]
            * ctx.weekday[cal.dow]
            * ctx.event_price_factor(i, cal)
        )
        den = float((w * exp).sum())
        level = float((w * y).sum()) / den if den > 0 else 0.0
        state.day_level = level
        state.params = {"hours_used": int(ok.sum()), "level_now": level}
        state.reasons.append(f"{REASON}: ước lượng lại mức từ {int(ok.sum())} giờ gần nhất")

    def _fallback(self, state: FittedState, ctx: FitContext, i: int, why: str) -> None:
        level, _, _ = ewma_level(ctx, i)
        state.day_level = level
        state.params = {"hours_used": 0, "fallback": why}
        state.reasons.append(f"{REASON}, nhưng {why}: tạm dùng mức nền lịch sử")
