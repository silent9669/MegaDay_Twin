"""Forecast contract (FC-01, plan 03 §2): request/result schemas, the ``ForecastModel``
protocol, the fitted state and the shared quantile sampler ``nb_quantiles``.

Every model returns the same :class:`ForecastResult` and draws its quantiles with the
same function, so models are comparable and the simulator (05 §5.3) can resample from
``mean`` / ``dispersion_k`` / ``level_sigma`` exactly like this module does.

Target (03 §1): hourly demand (non-cancelled units customers *want*) under the current
ads policy (status quo). Times are tz-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

import numpy as np
import numpy.typing as npt
import pandas as pd
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.forecast.context import FitContext

FloatArr = npt.NDArray[np.float64]

Context = Literal["normal", "double_day", "gift_season", "new"]
Label = Literal["verified", "reference", "scenario"]
DemandGroup = Literal["on_dinh", "mua_vu", "trend", "moi", "ban_thua", "unclassified"]
SeasonKind = Literal["qua_tang", "tet", "mua_dong"]

CONTEXTS: tuple[Context, ...] = ("normal", "double_day", "gift_season", "new")
DEMAND_GROUPS: tuple[DemandGroup, ...] = (
    "on_dinh",
    "mua_vu",
    "trend",
    "moi",
    "ban_thua",
    "unclassified",
)
SEASON_KINDS: tuple[SeasonKind, ...] = ("qua_tang", "tet", "mua_dong")
DEFAULT_SEED = 20261111  # 01 §7
DEFAULT_SAMPLES = 2000
MAX_MEAN = 1e5  # units/hour: guard against a degenerate fit
MAX_SIGMA = 2.0


class ForecastRequest(BaseModel):
    sku_id: str
    origin: datetime  # data used up to (excluding) this instant, UTC
    horizon_hours: int = Field(default=72, ge=1, le=14 * 24)
    context: Context | None = None  # None = inferred
    price_plan: dict[datetime, int] | None = None  # planned effective price per hour (UTC)


class ValidatedScope(BaseModel):
    """Backtest score of the model on the request's (group, context) (03 §7.3).
    ``backtest`` is None when the model was never scored there."""

    model_id: str
    demand_group: str
    context: str
    backtest: str | None = None
    wape: float | None = None
    bias: float | None = None
    coverage: float | None = None  # randomised-PIT coverage of the 80 % interval
    wape_floor: float | None = None  # expected WAPE under the model's own distribution
    pinball_50: float | None = None
    n_skus: int | None = None
    n_sku_days: int | None = None
    scored_at: datetime | None = None


class ForecastResult(BaseModel):
    forecast_id: str
    sku_id: str
    model_id: str  # "profile_ewma@1.0.0"
    origin: datetime
    hours: list[datetime]  # UTC, len = horizon
    mean: list[float]  # λ_sq(t)
    p10: list[float]
    p50: list[float]
    p90: list[float]
    dispersion_k: float  # Negative Binomial size
    level_sigma: float  # log-scale uncertainty of the base level
    cum_p10: list[float]  # quantiles of the running total from the first hour
    cum_p50: list[float]
    cum_p90: list[float]
    validated_scope: ValidatedScope
    confidence: Label
    reasons: list[str]
    data_as_of: datetime
    panel_version: str
    # [DEV] extra fields (not in 03 §2), useful to the simulator / AI layer
    demand_group: str = "unclassified"
    context: Context = "normal"
    history_days: int = 0
    phase: str | None = None  # trend phase: tăng | đỉnh | nguội
    primary_label: str | None = None
    model_selection: dict[str, Any] = Field(default_factory=dict)
    selection_signature: str | None = None


@dataclass
class FittedState:
    """Output of ``ForecastModel.fit``. ``day_level`` is the base level (units/day before
    hour, weekday, event and price factors); models with a time-varying level fill
    ``future_levels`` lazily in ``predict``. ``ctx`` is kept so ``predict`` can apply the
    shop factors (03 §2: models read FitContext instead of re-estimating)."""

    model_id: str
    sku_id: str
    origin: datetime
    ctx: FitContext
    demand_group: str
    day_level: float
    hour_profile: FloatArr
    dispersion_k: float
    level_sigma: float
    history_days: int
    kind: str | None = None
    phase: str | None = None
    reasons: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)


class ForecastModel(Protocol):
    name: str
    version: str
    applicable_groups: set[str]

    @property
    def model_id(self) -> str: ...

    def fit(
        self, panel: pd.DataFrame | None, sku_id: str, origin: datetime, ctx: FitContext
    ) -> FittedState: ...

    def predict(self, state: FittedState, req: ForecastRequest) -> ForecastResult: ...


@dataclass(frozen=True)
class Quantiles:
    p10: FloatArr
    p50: FloatArr
    p90: FloatArr
    cum_p10: FloatArr
    cum_p50: FloatArr
    cum_p90: FloatArr


def nb_samples(
    mean: FloatArr,
    k: float,
    level_sigma: float,
    rng: np.random.Generator,
    n: int = DEFAULT_SAMPLES,
) -> FloatArr:
    """Samples ``[n, T]`` of the mixture: level ``L ~ LogNormal(0, level_sigma)`` shared by
    all hours of a sample, then ``NegBin(mean·L, k)`` per hour (as Gamma-Poisson)."""
    mu = np.clip(np.nan_to_num(np.asarray(mean, dtype=np.float64), nan=0.0), 0.0, MAX_MEAN)
    t = mu.shape[-1]
    sigma = float(np.clip(np.nan_to_num(level_sigma, nan=1.0), 0.0, MAX_SIGMA))
    lvl = np.exp(rng.normal(0.0, sigma, size=(n, 1)))
    kk = max(float(k), 1e-6)
    g = rng.gamma(kk, 1.0 / kk, size=(n, t))
    out: FloatArr = rng.poisson(mu[None, :] * lvl * g).astype(np.float64)
    return out


def _order_quantiles(samples: FloatArr) -> FloatArr:
    """Quantiles 0.1/0.5/0.9 along axis 0 as order statistics (one sort, ~3× faster than
    ``np.quantile``; samples are integer counts so interpolation adds nothing)."""
    n = samples.shape[0]
    idx = [min(n - 1, max(0, int(np.ceil(q * n)) - 1)) for q in (0.1, 0.5, 0.9)]
    out: FloatArr = np.sort(samples, axis=0)[idx]
    return out


def quantiles_of(samples: FloatArr) -> Quantiles:
    """p10/p50/p90 per hour and of the running total, from ``[n, T]`` samples."""
    q = _order_quantiles(samples)
    cq = _order_quantiles(np.cumsum(samples, axis=1))
    return Quantiles(
        p10=np.asarray(q[0], dtype=np.float64),
        p50=np.asarray(q[1], dtype=np.float64),
        p90=np.asarray(q[2], dtype=np.float64),
        cum_p10=np.asarray(cq[0], dtype=np.float64),
        cum_p50=np.asarray(cq[1], dtype=np.float64),
        cum_p90=np.asarray(cq[2], dtype=np.float64),
    )


def nb_quantiles(
    mean: FloatArr,
    k: float,
    level_sigma: float,
    rng: np.random.Generator,
    n: int = DEFAULT_SAMPLES,
) -> Quantiles:
    """Mixture LogNormal level × NegBin counts (03 §2). Vectorised over ``[n, T]``."""
    return quantiles_of(nb_samples(mean, k, level_sigma, rng, n))


def make_rng(seed: int | None = None) -> np.random.Generator:
    return np.random.default_rng(DEFAULT_SEED if seed is None else seed)
