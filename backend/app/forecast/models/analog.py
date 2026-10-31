"""``analog`` (FC-06, plan 03 §3.5): new items (< 14 days) borrow similar SKUs.

Similar = same ``product_id`` (new variant), else same template prefix of the code, else
list price within ±30 % among ``moi`` / ``on_dinh`` SKUs. ``L_analog`` = median daily level
of days 7–21 after their launch, scaled by price (``(p_i / p_j)^β``); none found → median
of the ``moi`` group. Shrinkage: ``L = (n_obs·mean_obs + κ·L_analog) / (n_obs + κ)``,
``κ = 7`` days; ``level_sigma = 0.6`` shrinking with ``n_obs``. The label is always
``scenario`` under 14 days of history (applied by ``labels``).
"""

from __future__ import annotations

import re
from typing import ClassVar

import numpy as np
import numpy.typing as npt

from app.forecast.base import FittedState
from app.forecast.context import FitContext
from app.forecast.models._common import BaseForecastModel

FloatArr = npt.NDArray[np.float64]
KAPPA = 7.0
SIGMA0 = 0.6
SIGMA_MIN = 0.25
MIN_ANALOG_AGE = 21
_PREFIX = re.compile(r"^([A-Z]+)")


def _template(sku: str) -> str:
    m = _PREFIX.match(sku.upper())
    return m.group(1) if m else sku


def launch_level(ctx: FitContext, j: int) -> float | None:
    f = int(ctx.first_day[j])
    if f < 0:
        return None
    lo, hi = f + 7, min(f + 22, ctx.n_days)
    if hi - lo < 5:
        return None
    ok = ctx.x_valid[j, lo:hi]
    v = ctx.x[j, lo:hi][ok]
    return float(np.median(v)) if v.size >= 5 else None


def analog_candidates(ctx: FitContext, i: int) -> tuple[list[int], str]:
    sku = ctx.sku_ids[i]
    pid = ctx.product_id.get(sku)
    old = [
        j
        for j in range(len(ctx.sku_ids))
        if j != i and ctx.history_days(j) >= MIN_ANALOG_AGE and launch_level(ctx, j) is not None
    ]
    same_pid = [j for j in old if pid and ctx.product_id.get(ctx.sku_ids[j]) == pid]
    if same_pid:
        return same_pid, "cùng sản phẩm gốc"
    tpl = _template(sku)
    same_tpl = [j for j in old if _template(ctx.sku_ids[j]) == tpl]
    if same_tpl:
        return same_tpl, "cùng nhóm mã"
    p = ctx.list_price[i]
    if np.isfinite(p) and p > 0:
        near = [
            j
            for j in old
            if ctx.groups[j] in ("moi", "on_dinh")
            and np.isfinite(ctx.list_price[j])
            and abs(ctx.list_price[j] / p - 1) <= 0.3
        ]
        if near:
            return near, "cùng khoảng giá ±30%"
    return [], ""


class Analog(BaseForecastModel):
    name: ClassVar[str] = "analog"
    version: ClassVar[str] = "1.0.0"
    applicable_groups: ClassVar[set[str]] = {"moi"}
    params: ClassVar[dict[str, object]] = {"kappa_days": KAPPA, "sigma0": SIGMA0}

    def _fit(self, state: FittedState, ctx: FitContext, i: int) -> None:
        cands, how = analog_candidates(ctx, i)
        beta = ctx.beta_for(ctx.groups[i])
        p_i = ctx.list_price[i]
        levels: list[float] = []
        for j in cands:
            lv = launch_level(ctx, j)
            if lv is None:
                continue
            p_j = ctx.list_price[j]
            scale = (
                (p_i / p_j) ** beta if np.isfinite(p_i) and np.isfinite(p_j) and p_j > 0 else 1.0
            )
            levels.append(lv * float(scale))
        if levels:
            l_analog = float(np.median(levels))
            source = f"mượn mức bán của {len(levels)} SKU tương tự ({how})"
        else:
            moi = [launch_level(ctx, j) for j in range(len(ctx.sku_ids)) if ctx.groups[j] == "moi"]
            vals = [v for v in moi if v is not None]
            l_analog = (
                float(np.median(vals))
                if vals
                else float(np.nanmedian(ctx.level_local[:, -1]) if ctx.n_days else 0.0)
            )
            source = "không có SKU tương tự, dùng trung vị nhóm hàng mới"
        first = int(ctx.first_day[i])
        if first >= 0:
            ok = ctx.x_valid[i, first : ctx.n_days]
            obs = ctx.x[i, first : ctx.n_days][ok]
        else:
            obs = np.zeros(0)
        n_obs = float(obs.size)
        mean_obs = float(obs.mean()) if obs.size else 0.0
        state.day_level = (n_obs * mean_obs + KAPPA * l_analog) / (n_obs + KAPPA)
        state.level_sigma = max(SIGMA0 * float(np.sqrt(KAPPA / (KAPPA + n_obs))), SIGMA_MIN)
        state.params = {
            "l_analog": l_analog,
            "n_obs": n_obs,
            "mean_obs": mean_obs,
            "analogs": len(levels),
        }
        state.reasons.append(f"Hàng mới: {state.history_days} ngày lịch sử, {source}")
