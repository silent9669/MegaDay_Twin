"""``seasonal_calendar`` (FC-03, plan 03 §3.2): ``profile_ewma`` × a seasonal curve
``S_kind(d)`` estimated jointly for every SKU of the same kind (pooling).

* ``qua_tang``: bump ``1 + a·exp(−dtg²/2σ²)`` before the gift day (dtg = days to the nearest
  20/10 or 20/11), fitted on the pre-peak days seen so far; after a peak the empirical
  ratio observed after 20/10 is used, and the same shape is reused for 20/11 (stated in
  ``reasons``).
* ``tet``: ``1 + a·exp(−days_to_tet/τ)`` (extrapolated: no Tết in the history).
* ``mua_dong``: logistic ``1 + a / (1 + exp(−(d − c)/s))``.

Parameters come from a small grid minimising a Poisson-like χ² over the last 90 days with a
per-SKU level. The SKU level is the EWMA of ``x / S`` (half-life 7 days).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import ClassVar

import numpy as np
import numpy.typing as npt

from app.forecast.base import FittedState
from app.forecast.classify import WINTER_MID
from app.forecast.context import FitContext
from app.forecast.data import Calendar
from app.forecast.models._common import (
    LEVEL_SIGMA_FLOOR,
    BaseForecastModel,
    bootstrap_log_sigma,
    ewma_weights,
    stable_seed,
)

FloatArr = npt.NDArray[np.float64]
FIT_DAYS = 90
HALF_LIFE = 7.0
POST_DAYS = 15
MIN_S = 0.2  # a seasonal curve never says demand vanishes


@dataclass
class SeasonCurve:
    kind: str
    params: dict[str, float]
    post_ratio: dict[int, float]  # qua_tang: empirical ratio by (negative) days to peak
    n_skus: int

    def value(
        self,
        days_to_gift: FloatArr,
        days_to_tet: FloatArr,
        days: npt.NDArray[np.datetime64],
    ) -> FloatArr:
        p = self.params
        if self.kind == "qua_tang":
            d = days_to_gift
            pre = 1.0 + p["a"] * np.exp(-(d**2) / (2 * p["sigma"] ** 2))
            post = 1.0 + p["a"] * np.exp(-(d**2) / (2 * 1.5**2))
            emp = np.array([self.post_ratio.get(int(v), np.nan) for v in d])
            post = np.where(np.isfinite(emp), emp, post)
            return np.asarray(np.clip(np.where(d >= 0, pre, post), MIN_S, None), dtype=np.float64)
        if self.kind == "tet":
            return np.asarray(
                1.0 + p["a"] * np.exp(-np.clip(days_to_tet, 0, None) / p["tau"]),
                dtype=np.float64,
            )
        years = days.astype("datetime64[Y]").astype(int) + 1970
        mids = np.array(
            [np.datetime64(f"{y}-{WINTER_MID[0]:02d}-{WINTER_MID[1]:02d}") for y in years],
            dtype="datetime64[D]",
        )
        z = ((days - mids).astype(np.float64) - p["c"]) / p["s"]
        return np.asarray(1.0 + p["a"] / (1.0 + np.exp(-z)), dtype=np.float64)


def _grid(kind: str) -> list[dict[str, float]]:
    if kind == "qua_tang":
        return [
            {"a": a, "sigma": s}
            for a, s in itertools.product(
                [0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0], [2.0, 3.0, 4.0, 6.0, 8.0]
            )
        ]
    if kind == "tet":
        return [
            {"a": a, "tau": t}
            for a, t in itertools.product(
                [0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0], [10.0, 15.0, 20.0, 25.0, 35.0, 50.0]
            )
        ]
    return [
        {"a": a, "c": c, "s": s}
        for a, c, s in itertools.product(
            [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0],
            [float(v) for v in range(-40, 45, 5)],
            [4.0, 8.0, 12.0],
        )
    ]


def kind_of(ctx: FitContext, i: int) -> str:
    k = ctx.kinds[i]
    if k:
        return k
    f = ctx.features.get(ctx.sku_ids[i], {})
    hint = f.get("name_hint")
    if hint:
        return str(hint)
    corr: dict[str, float] = f.get("seasonal_corr") or {}
    return max(corr, key=lambda c: corr[c]) if corr else "mua_dong"


def fit_curve(ctx: FitContext, kind: str, include: int | None = None) -> SeasonCurve:
    key = f"season:{kind}:{include}"
    cached = ctx.cache.get(key)
    if isinstance(cached, SeasonCurve):
        return cached
    members = [
        i for i in range(len(ctx.sku_ids)) if ctx.groups[i] == "mua_vu" and kind_of(ctx, i) == kind
    ]
    if include is not None and include not in members:
        members.append(include)
    lo = max(0, ctx.n_days - FIT_DAYS)
    sl = slice(lo, ctx.n_days)
    curve = SeasonCurve(kind=kind, params=_grid(kind)[0], post_ratio={}, n_skus=len(members))
    if not members or ctx.n_days - lo < 7:
        ctx.cache[key] = curve
        return curve
    x = ctx.x[members, sl]
    ok = ctx.x_valid[members, sl] & (ctx.disc_day[members, sl] <= 0.02)
    x = np.where(ok, x, 0.0)
    dtg, dtt, days = ctx.days_to_gift[sl], ctx.days_to_tet[sl], ctx.days[sl]
    use = np.ones(ctx.n_days - lo, dtype=bool)
    if kind == "qua_tang":
        use = dtg >= 0  # pre-peak days; after-peak handled empirically
    best: tuple[float, dict[str, float]] | None = None
    for params in _grid(kind):
        c = SeasonCurve(kind, params, {}, len(members))
        s = c.value(dtg, dtt, days)
        m = ok & use[None, :]
        den = (s[None, :] * m).sum(axis=1)
        lvl = np.where(den > 0, (x * m).sum(axis=1) / np.maximum(den, 1e-12), 0.0)
        fit = lvl[:, None] * s[None, :]
        loss = float((((x - fit) ** 2) / (fit + 1.0) * m).sum())
        if best is None or loss < best[0]:
            best = (loss, params)
    assert best is not None
    curve = SeasonCurve(kind, best[1], {}, len(members))
    if kind == "qua_tang":
        s = curve.value(dtg, dtt, days)
        m = ok & (dtg >= 0)[None, :]
        den = (s[None, :] * m).sum(axis=1)
        lvl = np.where(den > 0, (x * m).sum(axis=1) / np.maximum(den, 1e-12), 0.0)
        good = lvl > 0
        for dd in range(-POST_DAYS, 0):
            cols = np.flatnonzero(dtg == dd)
            if not cols.size or not good.any():
                continue
            r = x[good][:, cols] / lvl[good][:, None]
            okc = ok[good][:, cols]
            if okc.sum() >= 2:
                curve.post_ratio[dd] = float(np.clip(np.median(r[okc]), MIN_S, None))
    ctx.cache[key] = curve
    return curve


class SeasonalCalendar(BaseForecastModel):
    name: ClassVar[str] = "seasonal_calendar"
    version: ClassVar[str] = "1.0.0"
    applicable_groups: ClassVar[set[str]] = {"mua_vu"}
    params: ClassVar[dict[str, object]] = {"fit_days": FIT_DAYS, "half_life_days": HALF_LIFE}

    def _fit(self, state: FittedState, ctx: FitContext, i: int) -> None:
        kind = kind_of(ctx, i)
        curve = fit_curve(ctx, kind, include=i)
        state.kind = kind
        lo = max(0, ctx.n_days - 56)
        sl = slice(lo, ctx.n_days)
        s_hist = curve.value(ctx.days_to_gift[sl], ctx.days_to_tet[sl], ctx.days[sl])
        ok = ctx.x_valid[i, sl] & ctx.normal_day[sl] & (ctx.disc_day[i, sl] <= 0.02)
        x = (ctx.x[i, sl] / np.maximum(s_hist, MIN_S))[ok]
        x = x[np.isfinite(x)]
        ages = (ctx.n_days - 1 - np.arange(lo, ctx.n_days))[ok].astype(np.float64)
        ages = ages[np.isfinite((ctx.x[i, sl] / np.maximum(s_hist, MIN_S))[ok])]
        if x.size == 0:
            state.day_level = 0.0
            state.level_sigma = 1.0
            state.reasons.append("Chưa có ngày bán nào đủ dữ liệu, dự báo bằng 0")
        else:
            w = ewma_weights(ages, HALF_LIFE)
            state.day_level = float((w * x).sum() / w.sum())
            rng = np.random.default_rng(stable_seed(ctx.seed, state.sku_id, ctx.origin, "boot"))
            state.level_sigma = max(bootstrap_log_sigma(x, w, rng), LEVEL_SIGMA_FLOOR)
        state.params = {
            "kind": kind,
            "curve": curve.params,
            "n_skus": curve.n_skus,
            "_curve": curve,
        }
        names = {"qua_tang": "quà tặng", "tet": "Tết", "mua_dong": "mùa đông"}
        state.reasons.append(
            f"Hàng mùa vụ ({names.get(kind, kind)}), đường mùa học chung {curve.n_skus} SKU"
        )
        if kind == "tet":
            state.reasons.append("Chưa có dữ liệu mùa Tết, ngoại suy")

    def _day_levels(self, state: FittedState, cal: Calendar) -> FloatArr:
        curve: SeasonCurve = state.params["_curve"]
        s = curve.value(cal.days_to_gift, cal.days_to_tet, cal.day)
        if curve.kind == "qua_tang":
            months = cal.day.astype("datetime64[M]").astype(int) % 12 + 1
            if bool((months == 11).any()) and "20/11" not in " ".join(state.reasons):
                state.reasons.append(
                    "Dùng lại hình dạng mùa quà 20/10 cho 20/11 (lịch sử chưa có đợt 20/11)"
                )
        return np.asarray(state.day_level * s, dtype=np.float64)
