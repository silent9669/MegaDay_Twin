"""``tsb`` (FC-05, plan 03 §3.4): Teunter–Syntetos–Babai for intermittent items.

Daily: ``p`` = probability of a sale (updated every day, ``α_p = 0.1``), ``z`` = size when
there is a sale (updated on sale days, ``α_z = 0.1``); ``λ_day = p·z`` spread to hours with
the **shop** profile (too little data for a SKU profile).

[DEV] Intervals use the shared ``nb_quantiles`` (NegBin with the group ``k``) instead of a
separate Bernoulli × Poisson mixture, so every model is sampled the same way (03 §2).
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np
import numpy.typing as npt

from app.forecast.base import FittedState
from app.forecast.context import FitContext
from app.forecast.models._common import BaseForecastModel

FloatArr = npt.NDArray[np.float64]
ALPHA_P = 0.1
ALPHA_Z = 0.1
SIGMA = 0.35


def tsb_level(
    x: FloatArr, alpha_p: float = ALPHA_P, alpha_z: float = ALPHA_Z
) -> tuple[float, float]:
    """(p, z) after running TSB over ``x`` (NaN = missing day, skipped)."""
    v = x[np.isfinite(x)]
    if v.size == 0:
        return 0.0, 0.0
    head = v[: min(30, v.size)]
    p = float((head > 0).mean())
    nz = v[v > 0]
    z = float(nz[: min(5, nz.size)].mean()) if nz.size else 0.0
    for val in v:
        if val > 0:
            p += alpha_p * (1 - p)
            z += alpha_z * (float(val) - z)
        else:
            p += alpha_p * (0 - p)
    return p, z


class Tsb(BaseForecastModel):
    name: ClassVar[str] = "tsb"
    version: ClassVar[str] = "1.0.0"
    applicable_groups: ClassVar[set[str]] = {"ban_thua", "on_dinh"}
    params: ClassVar[dict[str, object]] = {"alpha_p": ALPHA_P, "alpha_z": ALPHA_Z}

    def _fit(self, state: FittedState, ctx: FitContext, i: int) -> None:
        first = int(ctx.first_day[i])
        if first < 0:
            state.day_level = 0.0
            state.reasons.append("Chưa có ngày bán nào, dự báo bằng 0")
            return
        lo = max(first, ctx.n_days - 120)
        ok = ctx.x_valid[i, lo : ctx.n_days]
        x = np.where(ok, ctx.x[i, lo : ctx.n_days], np.nan)
        p, z = tsb_level(x)
        state.day_level = p * z
        state.hour_profile = ctx.p_shop.copy()
        state.level_sigma = SIGMA
        state.params = {"p": p, "z": z}
        state.reasons.append(
            f"Hàng bán thưa: xác suất có đơn mỗi ngày {p:.0%}, trung bình {z:.1f} sp khi có đơn"
        )
