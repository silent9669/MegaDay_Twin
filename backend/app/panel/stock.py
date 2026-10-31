"""Hourly stock reconstruction and stockout flags (PNL-02, plan 02 §6.1).

History only has an end-of-day stock snapshot per SKU; live has one per hour.
Between two snapshots ("anchors") we walk hour by hour::

    stock_begin[h] = S_prev + receipts arrived at or before h − Σ_{h'<h} consumed[h']
    stock_end[h]   = stock_begin[h] − consumed[h]

``consumed`` is what physically leaves the shelf: every non-cancelled order of the
SKU **plus** ``qty ×`` the non-cancelled orders of each combo that contains it
(combos have no stock row of their own, 02 §2.3). The mismatch at the next anchor
``e = on_hand − stock_end`` is spread evenly over the selling hours when
``|e| ≤ max(3, 5% · on_hand)``; otherwise the snapshot wins (``STOCK_JUMP``): the jump
is put at the first hour that could not have been served without it (e > 0), else at
the end of the segment.

Receipt hour is unknown (the sheet only says ``Nhập {qty}`` for the day). Default
10:00 VN; moved earlier when sales prove stock must have arrived earlier, and later
(to the first hour that needs it) when the shelf was already empty at 10:00. When
the day has hourly snapshots, the receipt goes to the hour with the largest
unexplained increase.

Before the first anchor the walk is run backwards from it. After the last anchor it
runs forward without correction. An anchor whose value is invalid (``on_hand`` NULL,
e.g. negative in the sheet) is skipped: the walk spans both days and the hours are
marked ``interpolated``.

Stockout flag: ``stock_begin ≤ 0`` or the hour took everything that was left
(``units taken ≥ stock_begin`` with units taken > 0). "Taken" also counts orders that
were later cancelled: at order time they held stock, so the shelf did run empty.

Combo: ``stock = min_c floor(stock_c / qty_c)``. For the stockout flag the combo
competes with the components' own orders of the same hour, so its availability is
``min_c floor((stock_begin_c − own_consumed_c) / qty_c)``.

Pure numpy; no database access (``build.py`` loads the inputs).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

IntArr = npt.NDArray[np.int64]
FloatArr = npt.NDArray[np.float64]
BoolArr = npt.NDArray[np.bool_]
ObjArr = npt.NDArray[np.object_]

DEFAULT_RECEIPT_HOUR = 10  # VN local hour assumed for "Nhập {qty}" (plan 02 §6.1)
DAY_HOURS = 24  # a receipt may land anywhere in [VN midnight, +24 h)
SMALL_ERROR_ABS = 3
SMALL_ERROR_REL = 0.05

SOURCE_SNAPSHOT_HOUR = "snapshot_hour"
SOURCE_RECONSTRUCTED = "reconstructed"
SOURCE_INTERPOLATED = "interpolated"
CONF: dict[str, float] = {
    SOURCE_SNAPSHOT_HOUR: 1.0,
    SOURCE_RECONSTRUCTED: 0.8,
    SOURCE_INTERPOLATED: 0.5,
}
_SOURCE_RANK = {SOURCE_SNAPSHOT_HOUR: 0, SOURCE_RECONSTRUCTED: 1, SOURCE_INTERPOLATED: 2}


@dataclass
class SkuStockInput:
    """Inputs for one stocked (non-combo) SKU on a grid of ``n`` hours starting at a VN
    midnight.

    ``anchors``: boundary index k (stock valid at the start of grid hour k, i.e. after
    hour k-1; k may equal n) → on_hand, ``None`` when the raw value was invalid.
    ``receipts``: (grid index of the VN midnight of ``received_on``, qty).
    """

    consumed: IntArr
    need: IntArr
    anchors: dict[int, int | None] = field(default_factory=dict)
    receipts: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class StockSeries:
    begin: FloatArr  # NaN = unknown
    end: FloatArr
    source: ObjArr  # None = unknown
    restock: IntArr
    jump: BoolArr  # hour belongs to a segment corrected with STOCK_JUMP
    interpolated: BoolArr

    @classmethod
    def unknown(cls, n: int) -> StockSeries:
        return cls(
            begin=np.full(n, np.nan),
            end=np.full(n, np.nan),
            source=np.full(n, None, dtype=object),
            restock=np.zeros(n, dtype=np.int64),
            jump=np.zeros(n, dtype=bool),
            interpolated=np.zeros(n, dtype=bool),
        )

    @property
    def known(self) -> bool:
        return bool(np.isfinite(self.begin).any())


@dataclass(frozen=True)
class _Receipt:
    ws: int  # window start (VN midnight), grid index
    qty: int
    fixed: int | None = None  # grid index when pinned by hourly snapshots


def _excl_cumsum(a: IntArr | FloatArr) -> FloatArr:
    out = np.zeros(len(a), dtype=np.float64)
    if len(a) > 1:
        out[1:] = np.cumsum(a[:-1])
    return out


def _path(v0: float, pre: FloatArr, cons: IntArr) -> FloatArr:
    """stock_begin of each hour: start + arrivals so far (incl. this hour) − earlier sales."""
    return v0 + np.cumsum(pre) - _excl_cumsum(cons)


def _place_receipts(
    v0: float, cons: IntArr, need: IntArr, receipts: list[_Receipt], offset: int
) -> FloatArr:
    """Arrival hour of each receipt, segment-relative (segment starts at grid ``offset``)."""
    n = len(cons)
    pre = np.zeros(n, dtype=np.float64)
    for r in sorted(receipts, key=lambda x: (x.ws, x.qty)):
        if r.fixed is not None:
            pos = min(max(r.fixed - offset, 0), n - 1)
            pre[pos] += r.qty
            continue
        ws = r.ws - offset
        lo, hi = max(ws, 0), min(ws + DAY_HOURS, n)
        if lo >= hi:
            pre[min(max(ws, 0), n - 1)] += r.qty
            continue
        default = min(max(ws + DEFAULT_RECEIPT_HOUR, lo), hi - 1)
        begin_wo = _path(v0, pre, cons)
        short = np.nonzero(begin_wo[lo:hi] < need[lo:hi])[0]
        first_short = lo + int(short[0]) if short.size else None
        pos = default
        if first_short is not None and (
            first_short < default or bool((begin_wo[default : first_short + 1] <= 0).any())
        ):
            pos = first_short
        pre[pos] += r.qty
    return pre


def _walk_segment(
    v0: float,
    v1: float | None,
    cons: IntArr,
    need: IntArr,
    receipts: list[_Receipt],
    offset: int,
) -> tuple[FloatArr, FloatArr, FloatArr, bool]:
    """Walk one segment. Returns (begin, end, receipts placed, jumped)."""
    pre = _place_receipts(v0, cons, need, receipts, offset)
    begin = _path(v0, pre, cons)
    end = begin - cons
    if v1 is None or len(cons) == 0:
        return begin, end, pre, False
    e = v1 - float(end[-1])
    if e == 0:
        return begin, end, pre, False
    if abs(e) <= max(SMALL_ERROR_ABS, SMALL_ERROR_REL * abs(v1)):
        selling = cons > 0
        if not selling.any():
            selling = np.ones(len(cons), dtype=bool)
        cum = np.round(np.cumsum(selling * (e / int(selling.sum()))))
        cum[-1] = e
        end = end + cum
        begin = begin + np.concatenate([[0.0], cum[:-1]])
        return begin, end, pre, False
    short = np.nonzero(begin < need)[0]
    if e > 0 and short.size:
        h = int(short[0])
        begin[h:] += e
        end[h:] += e
    else:
        end[-1] += e
    return begin, end, pre, True


def _segment_of(k: int, bounds: list[int]) -> int:
    """Index i of the segment holding grid hour k: -1 before the first anchor, i for
    [bounds[i], bounds[i+1]), len(bounds)-1 after the last."""
    return int(np.searchsorted(np.asarray(bounds), k, side="right")) - 1


def _pin_receipts(
    receipts: list[tuple[int, int]],
    anchors: list[tuple[int, int]],
    cons: IntArr,
) -> list[_Receipt]:
    """Pin receipts whose day holds >= 2 anchors (hourly snapshots) to the sub-segment
    with the largest unexplained increase (v1 − v0 + sold)."""
    out: list[_Receipt] = []
    for ws, qty in receipts:
        inside = [(k, v) for k, v in anchors if ws <= k <= ws + DAY_HOURS]
        if not any(ws < k < ws + DAY_HOURS for k, _ in inside) or len(inside) < 2:
            out.append(_Receipt(ws, qty))
            continue
        best_k, best_gain = None, -np.inf
        for (ka, va), (kb, vb) in itertools.pairwise(inside):
            gain = vb - va + float(cons[ka:kb].sum())
            if gain > best_gain:
                best_k, best_gain = ka, gain
        out.append(_Receipt(ws, qty, fixed=best_k))
    return out


def reconstruct_sku(inp: SkuStockInput) -> StockSeries:
    """Hourly stock of one stocked SKU (see module docstring)."""
    n = len(inp.consumed)
    out = StockSeries.unknown(n)
    valid = sorted((k, int(v)) for k, v in inp.anchors.items() if v is not None and 0 <= k <= n)
    if not valid or n == 0:
        return out
    invalid = sorted(k for k, v in inp.anchors.items() if v is None and 0 < k < n)
    cons, need = inp.consumed, inp.need
    bounds = [k for k, _ in valid]
    in_grid = [(ws, q) for ws, q in inp.receipts if -DAY_HOURS < ws < n]
    receipts = _pin_receipts(in_grid, valid, cons)

    by_seg: dict[int, list[_Receipt]] = {}
    for r in receipts:
        at = r.fixed if r.fixed is not None else min(max(r.ws + DEFAULT_RECEIPT_HOUR, 0), n - 1)
        by_seg.setdefault(_segment_of(at, bounds), []).append(r)

    def fill(a: int, b: int, res: tuple[FloatArr, FloatArr, FloatArr, bool]) -> None:
        begin, end, pre, jumped = res
        out.begin[a:b] = begin
        out.end[a:b] = end
        out.restock[a:b] = np.round(pre).astype(np.int64)
        out.jump[a:b] = jumped
        interp = any(a < k < b for k in invalid)
        out.interpolated[a:b] = interp
        if interp:
            out.source[a:b] = SOURCE_INTERPOLATED
        elif b - a == 1:
            out.source[a:b] = SOURCE_SNAPSHOT_HOUR
        else:
            out.source[a:b] = SOURCE_RECONSTRUCTED

    k0, v_first = valid[0]
    if k0 > 0:
        rec = by_seg.get(-1, [])
        v_start = v_first + float(cons[:k0].sum()) - float(sum(r.qty for r in rec))
        fill(0, k0, _walk_segment(max(v_start, 0.0), v_first, cons[:k0], need[:k0], rec, 0))
    for i, ((ka, va), (kb, vb)) in enumerate(itertools.pairwise(valid)):
        if kb == ka:
            continue
        rec = by_seg.get(i, [])
        fill(ka, kb, _walk_segment(va, vb, cons[ka:kb], need[ka:kb], rec, ka))
    kl, vl = valid[-1]
    if kl < n:
        rec = by_seg.get(len(valid) - 1, [])
        fill(kl, n, _walk_segment(vl, None, cons[kl:], need[kl:], rec, kl))
    np.maximum(out.begin, 0.0, out=out.begin)
    np.maximum(out.end, 0.0, out=out.end)
    return out


def stockout_flags(begin: FloatArr, taken: IntArr | FloatArr) -> BoolArr:
    """``stock_begin ≤ 0`` or the hour took all that was left. Unknown stock → False."""
    known = np.isfinite(begin)
    b = np.where(known, begin, 1.0)
    flags = (b <= 0) | ((taken > 0) & (taken >= b))
    return np.asarray(flags & known, dtype=bool)


@dataclass
class ComboStock:
    begin: FloatArr
    end: FloatArr
    available: FloatArr  # what the combo could sell this hour after components' own sales
    source: ObjArr


def combo_stock(
    components: list[tuple[StockSeries, IntArr, int]],
) -> ComboStock:
    """Combine component series: list of (component stock, component own consumed, qty)."""
    n = len(components[0][0].begin)
    begin = np.full(n, np.inf)
    end = np.full(n, np.inf)
    avail = np.full(n, np.inf)
    rank = np.zeros(n, dtype=np.int64)
    for series, own_cons, qty in components:
        begin = np.minimum(begin, np.floor(series.begin / qty))
        end = np.minimum(end, np.floor(series.end / qty))
        avail = np.minimum(avail, np.floor(np.maximum(series.begin - own_cons, 0.0) / qty))
        r = np.array([_SOURCE_RANK.get(s, 3) if s is not None else 3 for s in series.source])
        rank = np.maximum(rank, r)
    names = {v: k for k, v in _SOURCE_RANK.items()}
    source = np.array([names.get(int(r)) for r in rank], dtype=object)
    for arr in (begin, end, avail):
        arr[~np.isfinite(arr)] = np.nan
    return ComboStock(begin=begin, end=end, available=avail, source=source)
