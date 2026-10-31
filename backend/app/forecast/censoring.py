"""Demand censored by stock (FC-02, plan 03 §4).

Stockout hours do not show demand (``units_gross`` is capped by stock). The base level is
estimated on in-stock hours only, over a trailing window of 14 days::

    L̂ = Σ_{t ∉ stockout} y_t / Σ_{t ∉ stockout} P_h(t)·W_w(t)·E(t)·(1 − disc_t)^β

and daily demand is rebuilt as::

    demand_adj(d) = Σ_{t ∈ d, in stock} y_t + Σ_{t ∈ d, stockout} max(y_t, L̂·P_h·W·E·…)

Hours with ``stockout_conf < 0.7`` (interpolated stock) count half in the level estimate,
and a low-confidence stockout hour only adds half of the correction (the shelf may not
really have been empty).

**Long stockouts** ([DEV], decision log M2 "Hoàn thiện M2"). When the trailing window holds
less than ``REF_SHARE`` days of expected in-stock demand, the level estimated on the few
hours left is noise (on ``demo`` the last hours before a stockout read 2–4× too low or too
high) and it describes demand from before the shelf emptied. The level of such a day is

    L(d) = L_pre · g_sib · g_trend

* ``L_pre``: level over the last ``EXPOSURE_DAYS`` days of in-stock expected share,
  reaching back as far as needed (the demand just before the stockout), at mean day ``t̄``;
* ``g_sib``: **sibling variants** of the same product move like it (M2 C2). Growth of
  ``Σ_J L_J`` from the day whose window is centred on ``t̄`` to the last day ``ℓ ≤ d`` the
  siblings have a reliable level, faded with the age of that information::

      g_sib = (Σ_J L_J(ℓ) / Σ_J L_J(t̄ + 7)) ^ (φ^(d − ℓ)),   φ = 0.85

  (a product seen growing two weeks ago is weak evidence today: on ``demo`` the same
  sibling growth that is right for 10.10 over-forecast Nov by 70% once the trend faded);
* ``g_trend``: **trend items** (``trend`` mask) follow the damped trend line estimated
  before the gap, ``exp(slope · φ(1 − φⁿ)/(1 − φ))``, ``n`` = days since the last observed
  data, ``slope`` = OLS of ``log L`` over the last ``SLOPE_DAYS`` reliable days;
* otherwise flat.

What demand did while every variant was out of stock cannot be recovered: that is the
information limit of the data (M2 "Hoàn thiện M2", oracle-history experiment).

All functions are vectorised over ``[sku, day, hour]`` (the long-stockout pass loops only
over SKU-days without a reliable level) and only see data before the origin.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArr = npt.NDArray[np.float64]
BoolArr = npt.NDArray[np.bool_]
IntArr = npt.NDArray[np.int64]

LOW_CONF = 0.7
LOW_CONF_WEIGHT = 0.5
LEVEL_WINDOW_DAYS = 14
MIN_EXPECTED_SHARE = 0.3  # Σ expected share of in-stock hours needed for a local level
# [DEV] a level is "reliable" when the window holds ≥ 3 days of in-stock expected demand;
# below that it is carried from the last reliable one (long stockouts, see module doc)
REF_SHARE = 3.0
EXPOSURE_DAYS = 7.0  # in-stock expected share behind a carried level
TREND_PHI = 0.85  # same damping as ``trend_damped`` (03 §3.3)
SIB_DECAY = TREND_PHI  # per day of age of the sibling information
SLOPE_DAYS = 14
SLOPE_CLIP = 0.10  # |slope| of log level per day used for the extrapolation


@dataclass(frozen=True)
class CensoredDays:
    """Per ``[sku, day]`` results of :func:`adjust_demand`."""

    demand_adj: FloatArr  # censoring-corrected daily demand
    raw: FloatArr  # Σ units_gross (what was sold)
    level: FloatArr  # local level L̂ (units per unit of expected share), trailing window
    expected_share: FloatArr  # Σ_h P·W·E·price over present hours (≈ W·E·price for a full day)
    stockout_hours: FloatArr  # number of stockout hours in the day
    instock_share: FloatArr  # Σ expected share of in-stock hours
    carried: BoolArr  # level carried over a long stockout (no reliable estimate that day)


def level_weights(instock: BoolArr, conf: FloatArr) -> FloatArr:
    """Weight of each hour in the level estimate: 0 stockout, 0.5 low-confidence stock."""
    w = instock.astype(np.float64)
    return np.where(conf < LOW_CONF, w * LOW_CONF_WEIGHT, w)


def _trailing_sum(a: FloatArr, window: int) -> FloatArr:
    """Sum over the trailing ``window`` days (including the day) along the last axis."""
    c = np.cumsum(a, axis=-1)
    out = c.copy()
    out[..., window:] = c[..., window:] - c[..., :-window]
    return out


def _ffill(a: FloatArr) -> FloatArr:
    """Forward-fill NaN along the last axis (vectorised); leading NaN stay NaN."""
    mask = np.isnan(a)
    idx = np.where(~mask, np.arange(a.shape[-1]), 0)
    np.maximum.accumulate(idx, axis=-1, out=idx)
    out = np.take_along_axis(a, idx, axis=-1)
    out[np.isnan(out)] = np.nan
    return out


def damped_growth(slope: float, n: float, phi: float = TREND_PHI) -> float:
    """Growth factor of a damped log-linear trend after ``n`` days (``n ≤ 0`` → 1)."""
    if n <= 0:
        return 1.0
    return float(np.exp(slope * phi * (1.0 - phi**n) / (1.0 - phi)))


def log_slope(level: FloatArr, ok: BoolArr, upto: int, days: int = SLOPE_DAYS) -> float:
    """OLS slope of ``log level`` over the last ``days`` reliable days ``≤ upto``."""
    idx = np.flatnonzero(ok[: upto + 1])[-days:]
    if idx.size < 5:
        return 0.0
    v = np.log(np.maximum(level[idx], 1e-3))
    return float(np.clip(np.polyfit(idx.astype(np.float64), v, 1)[0], -SLOPE_CLIP, SLOPE_CLIP))


def exposure_level(a: FloatArr, b: FloatArr, s: int, d: int, need: float) -> tuple[float, float]:
    """Level and exposure-weighted mean day over the last ``need`` days of in-stock
    expected share up to day ``d`` (reaching back as far as needed)."""
    bb = b[s, : d + 1]
    cum = np.cumsum(bb[::-1])
    j = int(np.searchsorted(cum, need))  # days back needed
    lo = max(0, d - j)
    num = float(a[s, lo : d + 1].sum())
    den = float(bb[lo:].sum())
    if den <= 0:
        return float("nan"), float(d)
    t = float(np.dot(bb[lo:], np.arange(lo, d + 1, dtype=np.float64)) / den)
    return num / den, t


def carry_long_stockouts(
    a: FloatArr,
    b: FloatArr,
    strict: FloatArr,
    tbar: FloatArr,
    product: IntArr | None,
    trend: BoolArr | None,
    window: int = LEVEL_WINDOW_DAYS,
) -> tuple[FloatArr, BoolArr]:
    """Level over days without a reliable estimate (NaN in ``strict``), see module doc.

    ``a``/``b`` are the daily weighted sums of units / expected share on in-stock hours,
    ``strict`` the reliable trailing level and ``tbar`` its exposure-weighted mean day.
    Returns (level, carried) ``[S, D]``; days before the first reliable estimate stay NaN."""
    s_count, n_days = strict.shape
    ok = np.isfinite(strict)
    out = strict.copy()
    carried = np.zeros_like(ok)
    prod = np.arange(s_count) if product is None else np.asarray(product)
    trd = np.zeros(s_count, dtype=bool) if trend is None else np.asarray(trend, dtype=bool)
    half = window // 2
    for s in range(s_count):
        if ok[s].all() or not ok[s].any():
            continue
        sibs = np.flatnonzero((prod == prod[s]) & (np.arange(s_count) != s))
        first = int(np.argmax(ok[s]))
        ref = -1
        for d in range(first, n_days):
            if ok[s, d]:
                ref = d
                continue
            # level before the shelf emptied: last EXPOSURE_DAYS of in-stock share
            base, anchor_t = exposure_level(a, b, s, d, EXPOSURE_DAYS)
            if not np.isfinite(base):
                base, anchor_t = float(strict[s, ref]), float(tbar[s, ref])
            growth = 1.0
            # 1. siblings: growth of their reliable level from the day whose window is
            #    centred on the data behind ``base`` to the last day they are observed
            j0 = int(np.clip(round(anchor_t) + half, 0, d))
            if sibs.size:
                both = ok[sibs, j0][:, None] & ok[sibs, j0 + 1 : d + 1]
                days_ok = np.flatnonzero(both.any(axis=0))
                if days_ok.size:
                    last = j0 + 1 + int(days_ok[-1])
                    use = sibs[ok[sibs, j0] & ok[sibs, last]]
                    den = float(strict[use, j0].sum())
                    num = float(strict[use, last].sum())
                    if den > 0 and num > 0:
                        growth = (num / den) ** (SIB_DECAY ** max(0, d - last))
                        anchor_t = float(np.dot(tbar[use, last], strict[use, last]) / num)
            # 2. trend items: follow the damped trend line after the last observed data
            if trd[s]:
                slope = log_slope(strict[s], ok[s], ref)
                growth *= damped_growth(slope, d - anchor_t)
            out[s, d] = base * growth
            carried[s, d] = True
    return out, carried


def adjust_demand(
    y: FloatArr,
    present: BoolArr,
    stockout: BoolArr,
    conf: FloatArr,
    expected: FloatArr,
    *,
    censoring: bool = True,
    window: int = LEVEL_WINDOW_DAYS,
    product: IntArr | None = None,
    trend: BoolArr | None = None,
) -> CensoredDays:
    """``y``, ``present``, ``stockout``, ``conf``, ``expected`` are ``[S, D, 24]``.

    ``expected`` is the relative expected demand of each hour (``P_h·W·E·price``). With
    ``censoring=False`` every present hour counts as in stock (the "uncorrected" variant
    used to show the effect of the correction, 03 §10). ``product`` (``[S]`` int codes)
    links variants of one product and ``trend`` (``[S]``) marks trend items, both used only
    over long stockouts (module doc)."""
    pres = present.astype(np.float64)
    so = (stockout & present) if censoring else np.zeros_like(present)
    instock = present & ~so
    wl = level_weights(instock, conf) if censoring else pres
    exp_p = expected * pres
    a = np.sum(y * wl, axis=2)
    b = np.sum(exp_p * wl, axis=2)
    ra = _trailing_sum(a, window)
    rb = _trailing_sum(b, window)
    ratio = ra / np.maximum(rb, 1e-12)
    loose = np.where(rb >= MIN_EXPECTED_SHARE, ratio, np.nan)
    carried = np.zeros(a.shape, dtype=bool)
    if censoring and a.size:
        day = np.arange(a.shape[1], dtype=np.float64)
        tbar = _trailing_sum(b * day[None, :], window) / np.maximum(rb, 1e-12)
        strict = np.where(rb >= REF_SHARE, ratio, np.nan)
        level, carried = carry_long_stockouts(a, b, strict, tbar, product, trend, window)
        # before the first reliable estimate (new SKU): the loose local level
        level = np.where(np.isfinite(level), level, _ffill(loose))
    else:
        level = _ffill(loose)
    level = np.nan_to_num(level, nan=0.0)
    raw = np.sum(y * pres, axis=2)
    if censoring:
        y_in = np.sum(y * instock, axis=2)
        imputed = np.maximum(level[:, :, None] * expected, y)
        # low-confidence stockout hour: half of the correction
        part = np.where(conf < LOW_CONF, y + LOW_CONF_WEIGHT * (imputed - y), imputed)
        demand_adj = y_in + np.sum(part * so, axis=2)
    else:
        demand_adj = raw
    return CensoredDays(
        demand_adj=demand_adj,
        raw=raw,
        level=level,
        expected_share=np.sum(exp_p, axis=2),
        stockout_hours=np.sum(so, axis=2).astype(np.float64),
        instock_share=np.sum(expected * instock, axis=2),
        carried=carried,
    )
