"""Automatic demand groups (FC-08, plan 03 §6).

Features on the last 56 days of censoring-corrected, deseasonalised daily demand ``x``
(weekday, double-day, payday and price effects divided out, so a double day does not look
like a trend):

==================  ===========================================================
``age_days``        days since the first order
``ADI``             mean interval between days with a sale
``CV2``             squared coefficient of variation of sale sizes
``slope28``         slope of ``log(1 + x)`` over the last 28 days
``peak_ratio``      mean of the 7 highest days / median of 56 days
``seasonal_corr``   correlation of ``x`` with the template curve of each kind
==================  ===========================================================

Rules (first match wins): ``age < 14`` → ``moi``; ``ADI > 1.32`` → ``ban_thua``;
``max corr ≥ 0.6`` and ``peak_ratio ≥ 2`` → ``mua_vu``; ``peak_ratio ≥ 3`` or
``|slope28| ≥ 0.04`` → ``trend``; otherwise ``on_dinh``. Never sold → ``unclassified``.

[DEV] Seasonality the history cannot show: Tết is ~3 months after the data, so a Tết item
has an almost flat history. The product name is used as a prior (``NAME_HINTS``): a name
that says "Tết" makes the SKU ``mua_vu/tet`` on its own; a winter/gift name lowers the
correlation needed for that kind to 0.3 (the data must still agree). Decision log M2 C3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
from unidecode import unidecode

if TYPE_CHECKING:
    from app.forecast.context import FitContext

FloatArr = npt.NDArray[np.float64]

WINDOW = 56
ADI_CUT = 1.32  # Syntetos-Boylan
CORR_CUT = 0.6
CORR_CUT_HINT = 0.3
PEAK_SEASON = 2.0
PEAK_TREND = 3.0
SLOPE_TREND = 0.04
NEW_DAYS = 14
WINTER_MID = (10, 25)  # centre of the winter ramp template (late October, North VN)

NAME_HINTS: dict[str, re.Pattern[str]] = {
    "tet": re.compile(r"\btet\b|li xi|cau doi|den long|banh chung|mut tet"),
    "mua_dong": re.compile(
        r"ao giu nhiet|tat giu nhiet|chan long|chan len|\blen\b|ao khoac|mua dong|suoi"
    ),
    "qua_tang": re.compile(r"\bqua\b|\bhoa\b|gau bong|nen thom|thiep|20/10|20/11"),
}


@dataclass
class Classification:
    sku_id: str
    group: str
    kind: str | None
    features: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def gift_template(days_to_gift: FloatArr) -> FloatArr:
    """Bump around the gift days (20/10, 20/11): rises ~a week before, falls right after."""
    d = days_to_gift
    sig = np.where(d >= 0, 4.0, 2.0)
    return np.asarray(np.exp(-(d**2) / (2 * sig**2)), dtype=np.float64)


def tet_template(days_to_tet: FloatArr, tau: float = 25.0) -> FloatArr:
    return np.asarray(np.exp(-np.clip(days_to_tet, 0, None) / tau), dtype=np.float64)


def winter_template(days: npt.NDArray[np.datetime64], width: float = 8.0) -> FloatArr:
    years = days.astype("datetime64[Y]").astype(int) + 1970
    mids = np.array(
        [np.datetime64(f"{y}-{WINTER_MID[0]:02d}-{WINTER_MID[1]:02d}") for y in years],
        dtype="datetime64[D]",
    )
    z = (days - mids).astype(np.float64) / width
    return np.asarray(1.0 / (1.0 + np.exp(-z)), dtype=np.float64)


def templates(ctx: FitContext) -> dict[str, FloatArr]:
    return {
        "qua_tang": gift_template(ctx.days_to_gift),
        "tet": tet_template(ctx.days_to_tet),
        "mua_dong": winter_template(ctx.days),
    }


def name_hint(name: str | None) -> str | None:
    if not name:
        return None
    text = unidecode(name).lower()
    for kind, pat in NAME_HINTS.items():
        if pat.search(text):
            return kind
    return None


def _corr(a: FloatArr, b: FloatArr) -> float:
    if len(a) < 7 or np.std(a) <= 1e-9 or np.std(b) <= 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _features(
    x_all: FloatArr,
    valid_all: npt.NDArray[np.bool_],
    raw_all: FloatArr,
    adj_all: FloatArr,
    first: int,
    n_days_total: int,
    tmpl: dict[str, FloatArr],
) -> dict[str, Any]:
    """Features of one daily series (03 §6). ``peak_ratio`` uses the highest 7-day
    rolling mean (robust to single noisy days) over the 56-day median."""
    feats: dict[str, Any] = {}
    d_end = n_days_total
    lo = max(0, d_end - WINDOW, first)
    if first < 0 or d_end - lo <= 0:
        return feats
    valid = valid_all[lo:d_end]
    xs = x_all[lo:d_end]
    raw = raw_all[lo:d_end]
    n_days = d_end - lo
    sale_days = int((raw > 0).sum())
    feats["n_days"] = n_days
    feats["ADI"] = float(n_days / sale_days) if sale_days else float("inf")
    sizes = adj_all[lo:d_end][raw > 0]
    feats["CV2"] = float((sizes.std() / sizes.mean()) ** 2) if sizes.size and sizes.mean() else 0.0
    feats["mean_day"] = float(np.mean(adj_all[lo:d_end]))
    x = xs[valid]
    if x.size < 7:
        return feats
    lo28 = max(lo, d_end - 28)
    v28 = valid_all[lo28:d_end]
    x28 = x_all[lo28:d_end][v28]
    t28 = np.arange(lo28, d_end)[v28].astype(np.float64)
    feats["slope28"] = float(np.polyfit(t28, np.log1p(x28), 1)[0]) if x28.size >= 7 else 0.0
    filled = np.where(valid, xs, np.nan)
    roll = np.array(
        [np.nanmean(filled[j : j + 7]) for j in range(max(1, len(filled) - 6))], dtype=np.float64
    )
    top = float(np.nanmax(roll)) if np.isfinite(roll).any() else 0.0
    med = float(np.median(x))
    feats["peak_ratio"] = top / med if med > 0 else top / max(float(x.mean()), 1e-9)
    feats["seasonal_corr"] = {
        kind: _corr(x, curve[lo:d_end][valid]) for kind, curve in tmpl.items()
    }
    return feats


def product_of(ctx: FitContext, sku: str) -> str:
    pid = ctx.product_id.get(sku)
    return pid if pid else sku.split("-", 1)[0]


def _decide(sku: str, age: int, f: dict[str, Any], hint: str | None) -> Classification:
    f = dict(f)
    f["age_days"] = age
    f["name_hint"] = hint
    if "ADI" not in f:
        return Classification(sku, "unclassified", None, f, "Chưa có đơn nào")
    if age < NEW_DAYS:
        return Classification(sku, "moi", None, f, f"Mới bán {age} ngày")
    if f["ADI"] > ADI_CUT:
        return Classification(sku, "ban_thua", None, f, f"ADI = {f['ADI']:.2f} > {ADI_CUT}")
    if "peak_ratio" not in f:
        return Classification(sku, "unclassified", None, f, "Chưa đủ ngày dữ liệu")
    corr: dict[str, float] = f["seasonal_corr"]
    best = max(corr, key=lambda k: corr[k])
    if corr[best] >= CORR_CUT and f["peak_ratio"] >= PEAK_SEASON:
        return Classification(sku, "mua_vu", best, f, f"Tương quan mùa {best} = {corr[best]:.2f}")
    if hint == "tet":
        return Classification(sku, "mua_vu", "tet", f, "Tên sản phẩm là hàng Tết")
    if hint is not None and corr.get(hint, 0.0) >= CORR_CUT_HINT:
        return Classification(
            sku, "mua_vu", hint, f, f"Tên sản phẩm + tương quan mùa {hint} = {corr[hint]:.2f}"
        )
    if f["peak_ratio"] >= PEAK_TREND or abs(f["slope28"]) >= SLOPE_TREND:
        return Classification(
            sku,
            "trend",
            None,
            f,
            f"peak_ratio = {f['peak_ratio']:.1f}, slope28 = {f['slope28']:+.3f}",
        )
    return Classification(sku, "on_dinh", None, f, "Bán ổn định")


def classify_context(ctx: FitContext) -> dict[str, Classification]:
    """Group of every SKU. [DEV] Features are computed on the **product** (variants summed):
    variants share their demand pattern, and a 0.3 sp/day colour of a stable product would
    otherwise look intermittent. ``age_days`` (rule 1) stays per SKU, so a new variant of an
    old product is still ``moi``. Decision log M2 C2."""
    tmpl = templates(ctx)
    products: dict[str, list[int]] = {}
    for i, s in enumerate(ctx.sku_ids):
        products.setdefault(product_of(ctx, s), []).append(i)
    out: dict[str, Classification] = {}
    for idx in products.values():
        valid = ctx.x_valid[idx]
        x = np.where(valid, ctx.x[idx], 0.0).sum(axis=0)
        v = np.asarray(valid.any(axis=0), dtype=bool)
        raw = ctx.raw_day[idx].sum(axis=0)
        adj = ctx.demand_adj[idx].sum(axis=0)
        firsts = [int(ctx.first_day[i]) for i in idx if ctx.first_day[i] >= 0]
        first = min(firsts) if firsts else -1
        f = _features(x, v, raw, adj, first, ctx.n_days, tmpl)
        for i in idx:
            sku = ctx.sku_ids[i]
            out[sku] = _decide(sku, ctx.history_days(i), f, name_hint(ctx.names.get(sku)))
    return out
