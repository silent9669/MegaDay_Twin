"""Stock, combo, flash reserve and commitment constraints (SIM-03, plan 05 §5.4).

Hour loop (``T ≤ 72`` steps), vectorised over actions × samples ``[A, S]``:

1. Restock: ``stock_j += qty`` at the planned arrival hour (+ scenario delay).
2. Sellable stock outside flash windows: ``stock − reserve_left − safety``; inside a flash
   window: ``stock − safety`` (the reserve is sold there).
3. Single SKUs first: ``sold = min(demand, max(avail, 0))``; combos after:
   ``avail_combo = min_j ⌊avail_j / qty_j⌋``, every component loses ``qty_j·sold_combo``.
4. ``lost = demand − sold``; inside a flash window the reserve shrinks by what was sold.

Constraints checked **before** simulating (an infeasible action is still simulated and
shown, never recommended):

* ``campaign_must_run`` — the action switches the campaign off in a committed hour;
* ``committed_price`` — a what-if raises the price of a committed SKU;
* ``sku_level_control`` — a what-if toggles a single SKU of a shared campaign.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from app.core.errors import ConstraintViolation
from app.simulator.schemas import ActionSpec

FloatArr = npt.NDArray[np.float64]
BoolArr = npt.NDArray[np.bool_]
IntArr = npt.NDArray[np.int64]

UNLIMITED = 1e12  # stock of a SKU without stock data (flagged in the reasons)

MUST_RUN = "campaign_must_run"
PRICE_COMMITTED = "committed_price"
SKU_LEVEL = "sku_level_control"


# ---------------------------------------------------------------------- checks
def check_action(
    action: ActionSpec, on: BoolArr, must_run: BoolArr, hours_label: Sequence[str]
) -> list[tuple[str, str]]:
    """Violations ``[(code, Vietnamese text)]`` of one action. ``on`` = hours the action
    keeps the campaign on, ``must_run`` = committed hours inside the horizon."""
    out: list[tuple[str, str]] = []
    off_committed = must_run & ~on
    if action.kind == "reduce_budget" and action.pct is not None and action.pct >= 1:
        off_committed = must_run.copy()
    if off_committed.any():
        idx = np.flatnonzero(off_committed)
        first = hours_label[int(idx[0])] if len(hours_label) > idx[0] else "?"
        out.append(
            (
                MUST_RUN,
                f"Tắt chiến dịch trong khung flash sale đã cam kết với sàn ({len(idx)} giờ,"
                f" từ {first})",
            )
        )
    return out


def check_price_change(sku_id: str, new_price: float, committed: int | None) -> None:
    """What-if raising a committed price → ``ConstraintViolation``."""
    if committed is not None and new_price > committed:
        raise ConstraintViolation(
            f"Không được tăng giá {sku_id} trên giá đã cam kết {committed:,}đ.".replace(",", "."),
            constraint=PRICE_COMMITTED,
            sku_id=sku_id,
        )


def check_sku_toggle(campaign_id: str, sku_level_control: bool, skus: Sequence[str]) -> None:
    """What-if "tắt riêng SKU X" on a campaign with a shared budget → not simulated, the
    error suggests splitting the campaign."""
    if not sku_level_control:
        raise ConstraintViolation(
            f"Chiến dịch {campaign_id} dùng ngân sách chung cho {len(skus)} SKU, không tắt riêng"
            " từng SKU được. Muốn làm vậy cần tách SKU sang chiến dịch riêng.",
            constraint=SKU_LEVEL,
            campaign_id=campaign_id,
        )


# ---------------------------------------------------------------------- stock loop
@dataclass(frozen=True)
class StockSetup:
    """Stock-holding SKUs ``J`` (singles and combo components) and combos ``C``."""

    stock0: FloatArr  # [J]
    restock: FloatArr  # [J, T] units arriving at the start of hour t
    reserve0: FloatArr  # [J] flash reserve left (combo reserve × qty included)
    reserve_active: BoolArr  # [T] reserve applies (start day, before the last flash ends)
    flash: BoolArr  # [T] inside a flash window
    safety: FloatArr  # [J]
    combo_qty: FloatArr  # [C, J]


@dataclass
class StockResult:
    sold: FloatArr  # [J, A, S] single units sold
    lost: FloatArr  # [J, A, S]
    combo_sold: FloatArr  # [C, A, S]
    combo_lost: FloatArr  # [C, A, S]
    stock_end: FloatArr  # [J, A, S]
    contribution: FloatArr  # [A, S] Σ m·sold (singles + combos)
    first_lost_t: IntArr  # [J, A, S] first hour with unserved demand (T = never)
    combo_first_lost_t: IntArr  # [C, A, S]
    contribution_single: FloatArr  # [J, A, S], actual hourly margin times sales
    contribution_combo: FloatArr  # [C, A, S]


def simulate_stock(
    setup: StockSetup,
    demand: FloatArr,  # [J, A, S, T] single demand
    combo_demand: FloatArr,  # [C, A, S, T]
    m: FloatArr,  # [J, T] contribution per single unit
    m_combo: FloatArr,  # [C, T]
) -> StockResult:
    n_j, n_a, n_s, n_t = demand.shape
    n_c = combo_demand.shape[0]
    stock = np.broadcast_to(setup.stock0[:, None, None], (n_j, n_a, n_s)).astype(np.float64)
    reserve = np.broadcast_to(setup.reserve0[:, None, None], (n_j, n_a, n_s)).astype(np.float64)
    safety = setup.safety[:, None, None]
    sold_tot = np.zeros((n_j, n_a, n_s))
    lost_tot = np.zeros((n_j, n_a, n_s))
    csold_tot = np.zeros((n_c, n_a, n_s))
    clost_tot = np.zeros((n_c, n_a, n_s))
    contrib = np.zeros((n_a, n_s))
    contrib_single = np.zeros((n_j, n_a, n_s))
    contrib_combo = np.zeros((n_c, n_a, n_s))
    first = np.full((n_j, n_a, n_s), n_t, dtype=np.int64)
    cfirst = np.full((n_c, n_a, n_s), n_t, dtype=np.int64)
    q = setup.combo_qty
    for t in range(n_t):
        stock += setup.restock[:, t, None, None]
        held = 0.0 if (setup.flash[t] or not setup.reserve_active[t]) else reserve
        avail = np.maximum(stock - held - safety, 0.0)
        d = demand[:, :, :, t]
        sold = np.minimum(d, avail)
        stock -= sold
        avail -= sold
        used = sold.copy()
        lost = d - sold
        sold_tot += sold
        lost_tot += lost
        contrib += np.einsum("j,jas->as", m[:, t], sold)
        contrib_single += m[:, t, None, None] * sold
        np.minimum(first, np.where(lost > 0, t, n_t), out=first)
        for c in range(n_c):
            comp = np.flatnonzero(q[c] > 0)
            dc = combo_demand[c, :, :, t]
            if len(comp):
                cap = np.min(np.floor(avail[comp] / q[c, comp][:, None, None]), axis=0)
                sc = np.minimum(dc, np.maximum(cap, 0.0))
                take = q[c, comp][:, None, None] * sc[None]
                stock[comp] -= take
                avail[comp] -= take
                used[comp] += take
            else:
                sc = dc
            lc = dc - sc
            csold_tot[c] += sc
            clost_tot[c] += lc
            contrib += m_combo[c, t] * sc
            contrib_combo[c] += m_combo[c, t] * sc
            np.minimum(cfirst[c], np.where(lc > 0, t, n_t), out=cfirst[c])
        if setup.flash[t]:
            reserve = np.maximum(reserve - used, 0.0)
    return StockResult(
        sold=sold_tot,
        lost=lost_tot,
        combo_sold=csold_tot,
        combo_lost=clost_tot,
        stock_end=stock,
        contribution=contrib,
        first_lost_t=first,
        combo_first_lost_t=cfirst,
        contribution_single=contrib_single,
        contribution_combo=contrib_combo,
    )
