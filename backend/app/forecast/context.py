"""``FitContext``: shop-wide factors computed once per origin (FC-01, plan 03 §2, §3.1).

Only data strictly before ``origin`` is copied into the context (03 §7.1: no future leak,
FitContext included), so models cannot read the future even by mistake.

Contents
--------
* ``p_shop[h]`` hour profile of the shop, ``p_sku[s, h]`` SKU profile shrunk to it (κ = 50)
* ``weekday[w]`` weekday factors (mean 1), from normal days
* ``e_dd[group][h]`` double-day hour factors = median over past double days of
  (sales at h) / (expected normal sales at h); ``e_pre``, ``e_post``, ``e_pay`` day factors
* ``beta[group]`` price elasticity, ``k[group]`` NegBin dispersion (method of moments)
* per SKU × day: censoring-corrected demand (``censoring.adjust_demand``) and its
  deseasonalised level ``x`` (demand / expected share of the day)
* demand groups (``classify``), recomputed with data < origin
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from app.forecast import censoring as cens_mod
from app.forecast.data import Calendar, PanelMatrix, calendar_for, day_flags

FloatArr = npt.NDArray[np.float64]
BoolArr = npt.NDArray[np.bool_]

SHOP = "_shop"
KAPPA_PROFILE = 50.0  # 03 §3.1
DEFAULT_BETA = -1.8
BETA_BOUNDS = (-3.0, -0.5)
DEFAULT_DD_FACTOR = 2.5  # [DEV] no past double day yet (dd_0808): flat prior
DEFAULT_DD_DISCOUNT = 0.15  # typical double-day discount assumed inside DEFAULT_DD_FACTOR
DEFAULT_K = 3.0
K_BOUNDS = (0.5, 50.0)
MIN_GROUP_EXPECTED = 150.0  # expected units needed for a group-specific factor
VOUCHER_MIN = 0.02
DD_ADJ_KAPPA = 20.0


@dataclass
class FitContext:
    origin: pd.Timestamp
    censoring: bool
    sku_ids: list[str]
    start: pd.Timestamp
    t0: int  # hours of history (index of origin in the grid)
    n_days: int  # complete local days before origin
    panel_version: str
    list_price: FloatArr
    product_id: dict[str, str]
    names: dict[str, str]
    # history (copied, < origin)
    y: FloatArr  # [S, t0]
    present: BoolArr
    stockout: BoolArr
    conf: FloatArr
    disc: FloatArr
    cal: Calendar  # of the t0 history hours
    # per day [D]
    days: npt.NDArray[np.datetime64]
    is_dd: BoolArr
    is_pre: BoolArr
    is_post: BoolArr
    is_payday: BoolArr
    normal_day: BoolArr
    dow_day: npt.NDArray[np.int64]
    days_to_tet: FloatArr
    days_to_gift: FloatArr
    # shop factors
    p_shop: FloatArr
    weekday: FloatArr
    e_dd: dict[str, FloatArr]
    e_pre: float
    e_post: float
    e_pay: float
    n_past_dd: int
    dd_adj: FloatArr  # [S] per-SKU multiplier on the group double-day factor
    beta: dict[str, float]
    k: dict[str, float]
    # per SKU
    p_sku: FloatArr  # [S, 24]
    first_day: npt.NDArray[np.int64]  # -1 = never sold
    disc_day: FloatArr  # [S, D]
    disc_dd: FloatArr  # [S] typical double-day discount
    demand_adj: FloatArr  # [S, D]
    raw_day: FloatArr  # [S, D]
    level_local: FloatArr  # [S, D]
    share_day: FloatArr  # [S, D] expected share of the day (W·E·price)
    x: FloatArr  # [S, D] deseasonalised daily level
    x_valid: BoolArr  # [S, D]
    stockout_hours: FloatArr  # [S, D]
    groups: list[str]
    kinds: list[str | None]
    group_source: dict[str, str] = field(default_factory=dict)
    features: dict[str, dict[str, Any]] = field(default_factory=dict)
    seed: int = 20261111
    n_samples: int = 2000
    cache: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ helpers
    @property
    def index(self) -> dict[str, int]:
        cached = self.cache.get("_index")
        if cached is None:
            cached = {s: i for i, s in enumerate(self.sku_ids)}
            self.cache["_index"] = cached
        return dict(cached)

    def idx(self, sku_id: str) -> int:
        try:
            return int(self.index[sku_id])
        except KeyError as exc:
            raise KeyError(f"SKU {sku_id} không có trong panel") from exc

    @property
    def origin_dt(self) -> datetime:
        return self.origin.to_pydatetime()

    def group_of(self, i: int) -> str:
        return self.groups[i]

    def history_days(self, i: int) -> int:
        """Days from the first sale to the origin (partial current day counts)."""
        f = int(self.first_day[i])
        if f < 0:
            return 0
        return max(0, int(np.ceil(self.t0 / 24)) - f)

    def k_for(self, group: str) -> float:
        return float(self.k.get(group, self.k[SHOP]))

    def beta_for(self, group: str) -> float:
        return float(self.beta.get(group, self.beta[SHOP]))

    def e_dd_for(self, group: str) -> FloatArr:
        return self.e_dd.get(group, self.e_dd[SHOP])

    def recent_stockout_hours(self, i: int, days: int = 7) -> int:
        lo = max(0, self.n_days - days)
        return int(self.stockout_hours[i, lo : self.n_days].sum())

    def event_price_factor(
        self, i: int, cal: Calendar, price_plan: Mapping[datetime, int] | None = None
    ) -> FloatArr:
        """Hourly multiplier ``E(t)·price(t)`` for SKU ``i`` over ``cal`` (03 §3.1).

        Double-day factors already contain the typical double-day discount of the past;
        a price plan only moves demand by ``((1 − disc_plan) / (1 − disc_ref))^β``."""
        g = self.groups[i]
        e = np.ones(len(cal), dtype=np.float64)
        e = np.where(cal.is_payday, self.e_pay, e)
        e = np.where(cal.is_post, self.e_post * np.where(cal.is_payday, self.e_pay, 1.0), e)
        e = np.where(cal.is_pre, self.e_pre, e)
        e = np.where(cal.is_dd, self.e_dd_for(g)[cal.hour_local] * self.dd_adj[i], e)
        if price_plan:
            lp = self.list_price[i]
            if np.isfinite(lp) and lp > 0:
                beta = self.beta_for(g)
                plan = {pd.Timestamp(k).tz_convert("UTC"): float(v) for k, v in price_plan.items()}
                for j, h in enumerate(cal.hours):
                    p = plan.get(h)
                    if p is None:
                        continue
                    d_plan = float(np.clip(1.0 - p / lp, 0.0, 0.95))
                    d_ref = float(self.disc_dd[i]) if cal.is_dd[j] else 0.0
                    e[j] *= ((1.0 - d_plan) / (1.0 - d_ref)) ** beta
        return e

    def horizon_calendar(self, hours: int) -> Calendar:
        return calendar_for(self.origin, hours)


# ---------------------------------------------------------------------- builders
def _window(n_days: int, size: int) -> slice:
    return slice(max(0, n_days - size), n_days)


def _norm(a: FloatArr) -> FloatArr:
    s = float(a.sum())
    if not np.isfinite(s) or s <= 0:
        return np.full(a.shape, 1.0 / a.shape[-1])
    return a / s


def _hour_profiles(
    y: FloatArr, w: BoolArr, use_day: BoolArr
) -> tuple[FloatArr, FloatArr, FloatArr]:
    """Shop profile ``P_shop[h]``, SKU profiles ``P_s[h]`` (shrunk, κ = 50) and the rough
    daily level ``L_s`` of each SKU on the given days, in-stock hours only."""
    wd = w & use_day[None, :, None]
    yw = y * wd
    n_sh = yw.sum(axis=1)  # [S, 24]
    cnt_sh = wd.sum(axis=1).astype(np.float64)
    rate_sh = np.divide(n_sh, cnt_sh, out=np.zeros_like(n_sh), where=cnt_sh > 0)
    num = n_sh.sum(axis=0)
    den = (cnt_sh * rate_sh.sum(axis=1, keepdims=True) / 24.0).sum(axis=0)
    # too little data: plain counts (+1) instead of the in-stock weighted ratio
    p_shop = _norm(num + 1.0) if num.sum() < 24 or np.any(den <= 0) else _norm(num / den)
    nd = use_day.sum()
    n_adj = rate_sh * float(nd)
    has = cnt_sh > 0
    tot_obs = (n_adj * has).sum(axis=1, keepdims=True)
    p_obs = (p_shop[None, :] * has).sum(axis=1, keepdims=True)
    fill = np.divide(tot_obs, p_obs, out=np.zeros_like(tot_obs), where=p_obs > 0) * p_shop[None, :]
    n_adj = np.where(has, n_adj, fill)
    p_sku = (n_adj + KAPPA_PROFILE * p_shop[None, :]) / (
        n_adj.sum(axis=1, keepdims=True) + KAPPA_PROFILE
    )
    exp_share = (wd * p_sku[:, None, :]).sum(axis=(1, 2))
    l_day = np.divide(yw.sum(axis=(1, 2)), exp_share, out=np.zeros(y.shape[0]), where=exp_share > 0)
    return p_shop, p_sku, l_day


def _weekday(
    y: FloatArr, w: BoolArr, p_sku: FloatArr, l_day: FloatArr, use_day: BoolArr, dow: Any
) -> FloatArr:
    """Weekday factors from the shop daily index, detrended by a ±3-day local mean."""
    exp = (w * p_sku[:, None, :]).sum(axis=2) * l_day[:, None]  # [S, D]
    act = (y * w).sum(axis=2)
    num = act.sum(axis=0)
    den = exp.sum(axis=0)
    idx = np.where(use_day & (den > 0), num / np.maximum(den, 1e-12), np.nan)
    n = len(idx)
    ratio = np.full(n, np.nan)
    for d in range(n):
        if np.isnan(idx[d]):
            continue
        lo, hi = max(0, d - 3), min(n, d + 4)
        m = np.nanmean(idx[lo:hi])
        if np.isfinite(m) and m > 0:
            ratio[d] = idx[d] / m
    wk = np.ones(7)
    for k in range(7):
        r = ratio[(dow == k) & np.isfinite(ratio)]
        if len(r) >= 2:
            wk[k] = float(np.median(r))
    if not np.all(np.isfinite(wk)) or wk.mean() <= 0:
        return np.ones(7)
    return np.asarray(np.clip(wk / wk.mean(), 0.5, 1.5), dtype=np.float64)


def _trailing_mean(values: FloatArr, valid: BoolArr, lo: int, hi: int) -> FloatArr:
    """Mean of ``values`` over valid days in ``[d − lo, d − hi]`` for each day d."""
    v = np.where(valid, values, 0.0)
    c = np.concatenate([np.zeros((v.shape[0], 1)), np.cumsum(v, axis=1)], axis=1)
    n = np.concatenate(
        [np.zeros((v.shape[0], 1)), np.cumsum(valid.astype(np.float64), axis=1)], axis=1
    )
    d = np.arange(v.shape[1])
    a = np.clip(d - lo, 0, None)
    b = np.clip(d - hi + 1, 0, None)
    s = c[:, b] - c[:, a]
    cnt = n[:, b] - n[:, a]
    out = np.where(cnt >= 3, s / np.maximum(cnt, 1), np.nan)
    return np.asarray(out, dtype=np.float64)


def _beta(rate: FloatArr, lpre: FloatArr, disc: FloatArr, mask: BoolArr) -> float | None:
    m = mask & np.isfinite(lpre) & np.isfinite(rate) & (lpre > 0)
    if m.sum() < 15 or lpre[m].sum() < 30:
        return None
    grid = np.arange(BETA_BOUNDS[0], BETA_BOUNDS[1] + 1e-9, 0.1)
    tot = rate[m].sum()
    errs = [abs(np.log(tot / np.sum(lpre[m] * (1 - disc[m]) ** b))) for b in grid]
    return float(grid[int(np.argmin(errs))])


def _group_masks(groups: list[str]) -> dict[str, BoolArr]:
    arr = np.array(groups, dtype=object)
    out = {SHOP: np.ones(len(groups), dtype=bool)}
    for g in sorted(set(groups)):
        out[g] = arr == g
    return out


def _double_day_factors(
    y: FloatArr,
    w: BoolArr,
    p_sku: FloatArr,
    weekday_d: FloatArr,
    lpre: FloatArr,
    dd_days: list[int],
    masks: dict[str, BoolArr],
) -> dict[str, FloatArr]:
    """E[h] per group: median over past double days of observed / expected (03 §3.1)."""
    out: dict[str, FloatArr] = {}
    shop: FloatArr | None = None
    for g, m in [(SHOP, masks[SHOP])] + [(k, v) for k, v in masks.items() if k != SHOP]:
        per_day = []
        total_den = 0.0
        for d in dd_days:
            ok = m & np.isfinite(lpre[:, d])
            if not ok.any():
                continue
            ww = w[ok, d, :]
            num = (y[ok, d, :] * ww).sum(axis=0)
            den = (ww * p_sku[ok] * (weekday_d[d] * lpre[ok, d])[:, None]).sum(axis=0)
            if den.sum() <= 0:
                continue
            e_day = num.sum() / den.sum()
            c = 2.0
            per_day.append((num + c * e_day) / (den + c))
            total_den += float(den.sum())
        if not per_day:
            continue
        e = np.median(np.vstack(per_day), axis=0)
        if g == SHOP:
            shop = e
            out[SHOP] = e
        elif total_den >= MIN_GROUP_EXPECTED:
            out[g] = e
    if shop is None:
        out[SHOP] = np.full(24, DEFAULT_DD_FACTOR)
    return out


def _sku_dd_adjust(
    y: FloatArr,
    w: BoolArr,
    p_sku: FloatArr,
    weekday_d: FloatArr,
    lpre: FloatArr,
    dd_days: list[int],
    e_dd_s: FloatArr | None,
) -> FloatArr:
    """[DEV] Per-SKU multiplier on the pooled double-day factor, shrunk to 1:
    ``(Σ observed + κ) / (Σ expected with the group E + κ)``, κ = 20 units, over past
    double days. Items whose ads/discount react more than the group (NOICHIEN2L) keep
    their own response; small SKUs stay on the group factor. Decision log M2 C4."""
    s_count = y.shape[0]
    if e_dd_s is None or not dd_days:
        return np.ones(s_count)
    obs = np.zeros(s_count)
    exp = np.zeros(s_count)
    for d in dd_days:
        ok = np.isfinite(lpre[:, d])
        ww = w[:, d, :]
        o = (y[:, d, :] * ww).sum(axis=1)
        e = (ww * p_sku * e_dd_s).sum(axis=1) * weekday_d[d] * np.nan_to_num(lpre[:, d])
        obs += np.where(ok, o, 0.0)
        exp += np.where(ok, e, 0.0)
    return np.asarray(
        np.clip((obs + DD_ADJ_KAPPA) / (exp + DD_ADJ_KAPPA), 0.5, 2.0), dtype=np.float64
    )


def _day_factor(
    y: FloatArr,
    w: BoolArr,
    p_sku: FloatArr,
    weekday_d: FloatArr,
    lpre: FloatArr,
    days: list[int],
) -> float:
    vals = []
    for d in days:
        ok = np.isfinite(lpre[:, d])
        if not ok.any():
            continue
        num = (y[ok, d, :] * w[ok, d, :]).sum()
        den = (w[ok, d, :] * p_sku[ok] * (weekday_d[d] * lpre[ok, d])[:, None]).sum()
        if den > 5:
            vals.append(num / den)
    if not vals:
        return 1.0
    return float(np.clip(np.median(vals), 0.3, 3.0))


def _dispersion(
    y: FloatArr, w: BoolArr, lam: FloatArr, use_day: BoolArr, mask: BoolArr
) -> float | None:
    """Method of moments on in-stock hours: ``1/k = Σ((y − λ)² − y) / Σ λ²``."""
    sel = (w & use_day[None, :, None])[mask]
    yy = y[mask][sel]
    ll = lam[mask][sel]
    if ll.sum() < 100:
        return None
    den = float(np.sum((yy - ll) ** 2 - yy))
    num = float(np.sum(ll**2))
    if den <= 0:
        return K_BOUNDS[1]
    return float(np.clip(num / den, *K_BOUNDS))


def build_fit_context(
    m: PanelMatrix,
    origin: datetime | pd.Timestamp,
    *,
    censoring: bool = True,
    groups: Mapping[str, str] | None = None,
    user_groups: Mapping[str, str] | None = None,
    seed: int = 20261111,
    n_samples: int = 2000,
) -> FitContext:
    """FitContext from data strictly before ``origin``.

    ``groups`` forces the demand group of every SKU (skip auto classification);
    ``user_groups`` overrides only the listed SKUs (``demand_group_source='user'``)."""
    from app.forecast.classify import classify_context  # local: classify uses FitContext

    origin_ts = pd.Timestamp(origin).tz_convert("UTC")
    t0 = int(np.clip(m.hour_index(origin_ts), 0, m.n_hours))
    n_days = t0 // 24
    s_count = len(m.sku_ids)
    y = m.y[:, :t0].copy()
    present = m.present[:, :t0].copy()
    stockout = m.stockout[:, :t0].copy() if censoring else np.zeros_like(present)
    conf = m.conf[:, :t0].copy()
    disc = m.disc[:, :t0].copy()
    cal = calendar_for(m.start, t0) if t0 > 0 else calendar_for(m.start, 1)

    dd_len = n_days * 24
    yd = y[:, :dd_len].reshape(s_count, n_days, 24)
    pd_ = present[:, :dd_len].reshape(s_count, n_days, 24)
    sod = stockout[:, :dd_len].reshape(s_count, n_days, 24)
    cd = conf[:, :dd_len].reshape(s_count, n_days, 24)
    disc_day = (
        disc[:, :dd_len].reshape(s_count, n_days, 24).max(axis=2)
        if n_days
        else np.zeros((s_count, 0))
    )
    w = pd_ & ~sod

    day_index = pd.date_range(
        (m.start + pd.Timedelta(hours=7)).tz_localize(None), periods=max(n_days, 1), freq="D"
    )[:n_days]
    fl = day_flags(pd.DatetimeIndex(day_index))
    is_dd = np.asarray(fl["is_dd"], dtype=bool)
    is_pre = np.asarray(fl["is_pre"], dtype=bool)
    is_post = np.asarray(fl["is_post"], dtype=bool)
    is_pay = np.asarray(fl["is_payday"], dtype=bool)
    dow_day = np.asarray(fl["dow"], dtype=np.int64)
    normal = ~(is_dd | is_pre | is_post)
    voucher = disc_day > VOUCHER_MIN
    raw_any = (yd * pd_).sum(axis=2) > 0
    first_day = np.where(raw_any.any(axis=1), raw_any.argmax(axis=1), -1).astype(np.int64)
    if t0 > dd_len:  # partial current day can hold the first sale
        part = (y[:, dd_len:t0] > 0).any(axis=1)
        first_day = np.where((first_day < 0) & part, n_days, first_day)

    win28 = np.zeros(n_days, dtype=bool)
    win28[_window(n_days, 28)] = True
    use28 = win28 & normal & ~is_pay
    w_nv = w & ~voucher[:, :, None]
    p_shop, p_sku, l_day = _hour_profiles(yd, w_nv, use28)
    win56 = np.zeros(n_days, dtype=bool)
    win56[_window(n_days, 56)] = True
    weekday = _weekday(yd, w_nv, p_sku, l_day, win56 & normal & ~is_pay, dow_day)
    weekday_d = weekday[dow_day] if n_days else np.ones(0)

    # per-SKU daily rate on in-stock hours, deseasonalised by the weekday factor
    share_in = (w * p_sku[:, None, :]).sum(axis=2) * weekday_d[None, :]
    rate = np.where(share_in >= 0.25, (yd * w).sum(axis=2) / np.maximum(share_in, 1e-12), np.nan)
    base_ok = normal[None, :] & ~is_pay[None, :] & ~voucher & np.isfinite(rate)
    lpre = _trailing_mean(np.nan_to_num(rate), base_ok, lo=15, hi=2)

    dd_days = [int(d) for d in np.flatnonzero(is_dd)]
    disc_dd = np.full(s_count, 0.0)
    if dd_days:
        dvals = disc_day[:, dd_days]
        shop_dd = float(dvals.mean())
        disc_dd = np.where(dvals.max(axis=1) > 0, dvals.mean(axis=1), shop_dd)
    else:
        disc_dd[:] = DEFAULT_DD_DISCOUNT

    e_pre = _day_factor(yd, w, p_sku, weekday_d, lpre, [int(d) for d in np.flatnonzero(is_pre)])
    e_post = _day_factor(
        yd, w, p_sku, weekday_d, lpre, [int(d) for d in np.flatnonzero(is_post & ~is_pay)]
    )
    e_pay = _day_factor(
        yd, w, p_sku, weekday_d, lpre, [int(d) for d in np.flatnonzero(is_pay & normal)]
    )

    _, prod_code = np.unique(
        np.array([str(m.product_id.get(s, s)) for s in m.sku_ids], dtype=object),
        return_inverse=True,
    )

    def finish(group_list: list[str]) -> dict[str, Any]:
        masks = _group_masks(group_list)
        e_dd = _double_day_factors(yd, w, p_sku, weekday_d, lpre, dd_days, masks)
        beta: dict[str, float] = {}
        bmask_base = normal[None, :] & ~is_pay[None, :] & voucher
        b_shop = _beta(rate, lpre, disc_day, bmask_base)
        beta[SHOP] = DEFAULT_BETA if b_shop is None else b_shop
        for g, gm in masks.items():
            if g == SHOP:
                continue
            b = _beta(rate, lpre, disc_day, bmask_base & gm[:, None])
            if b is not None:
                beta[g] = b
        beta_s = np.array([beta.get(g, beta[SHOP]) for g in group_list])
        # expected relative demand of each hour: P·W·E·price
        e_hour = np.ones((s_count, n_days, 24))
        e_hour *= np.where(is_pay, e_pay, 1.0)[None, :, None]
        e_hour = np.where(is_post[None, :, None], e_hour * e_post, e_hour)
        e_hour = np.where(is_pre[None, :, None], e_pre, e_hour)
        e_dd_s = np.vstack([e_dd.get(g, e_dd[SHOP]) for g in group_list]) if s_count else None
        dd_adj = _sku_dd_adjust(yd, w, p_sku, weekday_d, lpre, dd_days, e_dd_s)
        price = (1.0 - disc_day) ** beta_s[:, None]
        if e_dd_s is not None and dd_days:
            ref = (1.0 - disc_dd) ** beta_s
            for d in dd_days:
                e_hour[:, d, :] = e_dd_s * (dd_adj * price[:, d] / ref)[:, None]
                price[:, d] = 1.0
        expected = p_sku[:, None, :] * weekday_d[None, :, None] * e_hour * price[:, :, None]
        cens = cens_mod.adjust_demand(
            yd,
            pd_,
            stockout[:, :dd_len].reshape(s_count, n_days, 24),
            cd,
            expected,
            censoring=censoring,
            product=prod_code,
            trend=np.array([g == "trend" for g in group_list], dtype=bool),
        )
        share = cens.expected_share
        valid = (share > 0.05) & (np.arange(n_days)[None, :] >= first_day[:, None])
        valid &= first_day[:, None] >= 0
        x = np.where(valid, cens.demand_adj / np.maximum(share, 1e-12), np.nan)
        lam = cens.level[:, :, None] * expected
        k: dict[str, float] = {}
        k_shop = _dispersion(yd, w, lam, use28, masks[SHOP])
        k[SHOP] = DEFAULT_K if k_shop is None else k_shop
        for g, gm in masks.items():
            if g == SHOP:
                continue
            kg = _dispersion(yd, w, lam, use28, gm)
            if kg is not None:
                k[g] = kg
        return {
            "e_dd": e_dd,
            "dd_adj": dd_adj,
            "beta": beta,
            "k": k,
            "cens": cens,
            "x": x,
            "valid": valid,
        }

    base_groups = ["unclassified"] * s_count
    if groups is not None:
        base_groups = [str(groups.get(s, "unclassified")) for s in m.sku_ids]
    r = finish(base_groups)
    ctx = FitContext(
        origin=origin_ts,
        censoring=censoring,
        sku_ids=list(m.sku_ids),
        start=m.start,
        t0=t0,
        n_days=n_days,
        panel_version=m.panel_version,
        list_price=m.list_price.copy(),
        product_id=dict(m.product_id),
        names=dict(m.names),
        y=y,
        present=present,
        stockout=stockout,
        conf=conf,
        disc=disc,
        cal=cal,
        days=day_index.to_numpy().astype("datetime64[D]"),
        is_dd=is_dd,
        is_pre=is_pre,
        is_post=is_post,
        is_payday=is_pay,
        normal_day=normal,
        dow_day=dow_day,
        days_to_tet=np.asarray(fl["days_to_tet"], dtype=np.float64),
        days_to_gift=np.asarray(fl["days_to_gift"], dtype=np.float64),
        p_shop=p_shop,
        weekday=weekday,
        e_dd=r["e_dd"],
        e_pre=e_pre,
        e_post=e_post,
        e_pay=e_pay,
        n_past_dd=len(dd_days),
        dd_adj=r["dd_adj"],
        beta=r["beta"],
        k=r["k"],
        p_sku=p_sku,
        first_day=first_day,
        disc_day=disc_day,
        disc_dd=np.asarray(disc_dd, dtype=np.float64),
        demand_adj=r["cens"].demand_adj,
        raw_day=r["cens"].raw,
        level_local=r["cens"].level,
        share_day=r["cens"].expected_share,
        x=r["x"],
        x_valid=r["valid"],
        stockout_hours=r["cens"].stockout_hours,
        groups=list(base_groups),
        kinds=[None] * s_count,
        seed=seed,
        n_samples=n_samples,
    )
    if groups is None:
        result = classify_context(ctx)
        new_groups = [result[s].group for s in m.sku_ids]
        kinds = [result[s].kind for s in m.sku_ids]
        source = {s: "auto" for s in m.sku_ids}
        for s, g in (user_groups or {}).items():
            if s in ctx.index:
                new_groups[ctx.idx(s)] = g
                source[s] = "user"
        r = finish(new_groups)
        ctx.groups = new_groups
        ctx.kinds = kinds
        ctx.group_source = source
        ctx.features = {s: result[s].features for s in m.sku_ids}
        ctx.e_dd, ctx.beta, ctx.k, ctx.dd_adj = r["e_dd"], r["beta"], r["k"], r["dd_adj"]
        cens = r["cens"]
        ctx.demand_adj, ctx.raw_day, ctx.level_local = cens.demand_adj, cens.raw, cens.level
        ctx.share_day, ctx.stockout_hours = cens.expected_share, cens.stockout_hours
        ctx.x, ctx.x_valid = r["x"], r["valid"]
    else:
        ctx.group_source = {s: "given" for s in m.sku_ids}
    return ctx
