"""Shared machinery of the six models (plan 03 §3).

Every model reduces to a **daily base level** for each future day (``_day_levels``) and an
hour profile; the hourly mean is then::

    λ(t) = level(d(t)) · P_h · W_w · E(t) · price(t)

with ``W``, ``E`` and price factors taken from :class:`FitContext` (pooling, 03 §2).
Quantiles come from :func:`app.forecast.base.nb_quantiles` for every model.
"""

from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import numpy.typing as npt
import pandas as pd

from app.core.ids import new_id
from app.forecast.base import (
    Context,
    FittedState,
    ForecastRequest,
    ForecastResult,
    ValidatedScope,
    nb_quantiles,
)
from app.forecast.context import FitContext
from app.forecast.data import Calendar

FloatArr = npt.NDArray[np.float64]

DD_EXTRA_SIGMA = 0.15  # 03 §5: only 3 double days to learn E(t)
NO_DD_EXTRA_SIGMA = 0.30  # [DEV] no past double day at all (default factors)
LEVEL_SIGMA_FLOOR = 0.10
SCENARIO_DAYS = 14


@dataclass
class MeanForecast:
    """Point forecast + uncertainty parameters, before sampling."""

    hours: pd.DatetimeIndex
    mean: FloatArr
    dispersion_k: float
    level_sigma: float
    reasons: list[str] = field(default_factory=list)
    phase: str | None = None
    context: Context = "normal"


def stable_seed(*parts: object) -> int:
    return zlib.crc32("|".join(str(p) for p in parts).encode("utf-8"))


def ewma_weights(ages_days: FloatArr, half_life: float) -> FloatArr:
    return np.asarray(0.5 ** (ages_days / half_life), dtype=np.float64)


def infer_context(ctx: FitContext, i: int, cal: Calendar) -> Context:
    """03 §2: None = inferred. Double day anywhere in the horizon wins."""
    if bool(cal.is_dd.any()):
        return "double_day"
    if ctx.history_days(i) < SCENARIO_DAYS:
        return "new"
    kind = ctx.kinds[i]
    if (
        ctx.groups[i] == "mua_vu"
        and kind == "qua_tang"
        and bool((np.abs(cal.days_to_gift) <= 7).any())
    ):
        return "gift_season"
    return "normal"


def stockout_reason(ctx: FitContext, i: int) -> str | None:
    n = ctx.recent_stockout_hours(i, 7)
    if n > 0 and ctx.censoring:
        return f"{n} giờ hết hàng trong 7 ngày qua, đã hiệu chỉnh nhu cầu"
    return None


class BaseForecastModel:
    """Common ``fit``/``predict``; subclasses implement ``_fit`` and ``_day_levels``."""

    name: ClassVar[str] = "base"
    version: ClassVar[str] = "1.0.0"
    applicable_groups: ClassVar[set[str]] = set()
    params: ClassVar[dict[str, Any]] = {}

    @property
    def model_id(self) -> str:
        return f"{self.name}@{self.version}"

    @classmethod
    def code_hash(cls) -> str:
        import inspect

        path = Path(inspect.getfile(cls))
        common = Path(__file__)
        h = hashlib.sha256()
        for p in (path, common):
            h.update(p.read_bytes())
        return h.hexdigest()[:16]

    # ------------------------------------------------------------------ fit
    def fit(
        self, panel: pd.DataFrame | None, sku_id: str, origin: datetime, ctx: FitContext
    ) -> FittedState:
        """``panel`` is accepted for the 03 §2 signature; the data actually used is the
        FitContext built from the same panel with data < ``origin`` (no future leak)."""
        if pd.Timestamp(origin).tz_convert("UTC") != ctx.origin:
            raise ValueError("origin khác mốc của FitContext")
        i = ctx.idx(sku_id)
        state = FittedState(
            model_id=self.model_id,
            sku_id=sku_id,
            origin=ctx.origin_dt,
            ctx=ctx,
            demand_group=ctx.groups[i],
            day_level=0.0,
            hour_profile=ctx.p_sku[i].copy(),
            dispersion_k=ctx.k_for(ctx.groups[i]),
            level_sigma=LEVEL_SIGMA_FLOOR,
            history_days=ctx.history_days(i),
            kind=ctx.kinds[i],
        )
        self._fit(state, ctx, i)
        return state

    def _fit(self, state: FittedState, ctx: FitContext, i: int) -> None:
        raise NotImplementedError

    def _day_levels(self, state: FittedState, cal: Calendar) -> FloatArr:
        """Base level (units/day) for each hour of ``cal`` (per-day value repeated)."""
        return np.full(len(cal), state.day_level)

    def _profile(self, state: FittedState) -> FloatArr:
        return state.hour_profile

    # ------------------------------------------------------------------ predict
    def mean_forecast(self, state: FittedState, req: ForecastRequest) -> MeanForecast:
        ctx = state.ctx
        i = ctx.idx(state.sku_id)
        origin = pd.Timestamp(req.origin).tz_convert("UTC")
        if origin != ctx.origin:
            raise ValueError("origin của yêu cầu khác mốc đã fit")
        cal = ctx.horizon_calendar(req.horizon_hours)
        level = self._day_levels(state, cal)
        prof = self._profile(state)[cal.hour_local]
        mean = level * prof * ctx.weekday[cal.dow] * ctx.event_price_factor(i, cal, req.price_plan)
        mean = np.clip(np.nan_to_num(mean, nan=0.0), 0.0, None)
        context = req.context or infer_context(ctx, i, cal)
        sigma = max(state.level_sigma, LEVEL_SIGMA_FLOOR)
        reasons = list(state.reasons)
        if bool(cal.is_dd.any()):
            if ctx.n_past_dd == 0:
                sigma += NO_DD_EXTRA_SIGMA
                reasons.append("Chưa có ngày đôi nào trong lịch sử, dùng hệ số ngày đôi mặc định")
            else:
                sigma += DD_EXTRA_SIGMA
                reasons.append(f"Hệ số ngày đôi học từ {ctx.n_past_dd} ngày đôi trước")
        if req.price_plan:
            reasons.append("Đã áp giá dự kiến (giá cam kết) vào dự báo")
        so = stockout_reason(ctx, i)
        if so:
            reasons.append(so)
        return MeanForecast(
            hours=cal.hours,
            mean=mean,
            dispersion_k=float(state.dispersion_k),
            level_sigma=float(sigma),
            reasons=reasons,
            phase=state.phase,
            context=context,
        )

    def predict(self, state: FittedState, req: ForecastRequest) -> ForecastResult:
        mf = self.mean_forecast(state, req)
        ctx = state.ctx
        rng = np.random.default_rng(
            stable_seed(ctx.seed, state.sku_id, ctx.origin.isoformat(), self.model_id)
        )
        q = nb_quantiles(mf.mean, mf.dispersion_k, mf.level_sigma, rng, n=ctx.n_samples)
        label = "scenario" if state.history_days < SCENARIO_DAYS else "reference"
        data_as_of = min(ctx.origin, ctx.start + pd.Timedelta(hours=ctx.t0))
        return ForecastResult(
            forecast_id=new_id("fc"),
            sku_id=state.sku_id,
            model_id=self.model_id,
            origin=ctx.origin_dt,
            hours=[h.to_pydatetime() for h in mf.hours],
            mean=[float(v) for v in mf.mean],
            p10=q.p10.tolist(),
            p50=q.p50.tolist(),
            p90=q.p90.tolist(),
            dispersion_k=mf.dispersion_k,
            level_sigma=mf.level_sigma,
            cum_p10=q.cum_p10.tolist(),
            cum_p50=q.cum_p50.tolist(),
            cum_p90=q.cum_p90.tolist(),
            validated_scope=ValidatedScope(
                model_id=self.model_id, demand_group=state.demand_group, context=mf.context
            ),
            confidence=label,
            reasons=mf.reasons,
            data_as_of=data_as_of.to_pydatetime(),
            panel_version=ctx.panel_version,
            demand_group=state.demand_group,
            context=mf.context,
            history_days=state.history_days,
            phase=mf.phase,
        )


# ---------------------------------------------------------------------- helpers
def recent_x(
    ctx: FitContext, i: int, days: int = 56, *, normal_only: bool = True
) -> tuple[FloatArr, FloatArr]:
    """Deseasonalised daily level and its age in days (0 = yesterday) over ``days``."""
    lo = max(0, ctx.n_days - days)
    sl = slice(lo, ctx.n_days)
    ok = ctx.x_valid[i, sl].copy()
    if normal_only:
        voucher = ctx.disc_day[i, sl] > 0.02
        ok &= ctx.normal_day[sl] & ~voucher
    x = ctx.x[i, sl][ok]
    ages = (ctx.n_days - 1 - np.arange(lo, ctx.n_days))[ok].astype(np.float64)
    return np.asarray(x, dtype=np.float64), ages


def bootstrap_log_sigma(
    x: FloatArr, w: FloatArr, rng: np.random.Generator, n_boot: int = 200
) -> float:
    """Std of ``log`` of the weighted mean, bootstrapping days (03 §5)."""
    if x.size < 3 or w.sum() <= 0:
        return 0.5
    idx = rng.integers(0, x.size, size=(n_boot, x.size))
    ww = w[idx]
    means = (x[idx] * ww).sum(axis=1) / np.maximum(ww.sum(axis=1), 1e-12)
    means = np.maximum(means, 1e-3 + 0.05 * float(np.mean(x)))
    return float(np.std(np.log(means)))


def day_index_of(ctx: FitContext, cal: Calendar) -> npt.NDArray[np.int64]:
    """Index of each horizon hour's local day relative to the context day grid."""
    first = ctx.days[0] if len(ctx.days) else cal.day[0]
    return (cal.day - first).astype(np.int64)
