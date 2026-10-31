"""``profile_ewma`` (FC-02, plan 03 §3.1): default model for stable items.

``λ(t) = L · P_h · W_w · E(t) · (1 − disc_t)^β``, with ``L`` the EWMA (half-life 7 days) of
the censoring-corrected, deseasonalised daily demand on normal days (double day ±1 and
voucher days left out), ``P_h`` the SKU hour profile shrunk to the shop's (κ = 50).
``level_sigma`` = bootstrap (200 × over days) std of ``log L`` with a floor of 0.1 (03 §5;
[DEV] "cộng sàn" read as ``max(se, 0.1)``, decision log M2 C8).
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from app.forecast.base import FittedState
from app.forecast.context import FitContext
from app.forecast.models._common import (
    LEVEL_SIGMA_FLOOR,
    BaseForecastModel,
    bootstrap_log_sigma,
    ewma_weights,
    recent_x,
    stable_seed,
)

HALF_LIFE = 7.0
WINDOW = 56


def ewma_level(
    ctx: FitContext, i: int, *, half_life: float = HALF_LIFE, window: int = WINDOW
) -> tuple[float, float, int]:
    """(level, bootstrap log-sigma, number of days used)."""
    x, ages = recent_x(ctx, i, window)
    if x.size < 3:
        x, ages = recent_x(ctx, i, window, normal_only=False)
    if x.size == 0:
        return 0.0, 1.0, 0
    w = ewma_weights(ages, half_life)
    level = float((w * x).sum() / w.sum())
    rng = np.random.default_rng(stable_seed(ctx.seed, ctx.sku_ids[i], ctx.origin, "boot"))
    return level, bootstrap_log_sigma(x, w, rng), int(x.size)


class ProfileEwma(BaseForecastModel):
    name: ClassVar[str] = "profile_ewma"
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
        "half_life_days": HALF_LIFE,
        "window_days": WINDOW,
        "kappa_profile": 50,
        "level_sigma_floor": LEVEL_SIGMA_FLOOR,
    }

    def _fit(self, state: FittedState, ctx: FitContext, i: int) -> None:
        level, sigma, n = ewma_level(ctx, i)
        state.day_level = level
        state.level_sigma = max(sigma, LEVEL_SIGMA_FLOOR)
        state.params = {"n_days": n}
        if n == 0:
            state.reasons.append("Chưa có ngày bán nào đủ dữ liệu, dự báo bằng 0")
        elif state.history_days < 28:
            state.reasons.append(f"Chỉ có {state.history_days} ngày lịch sử")
