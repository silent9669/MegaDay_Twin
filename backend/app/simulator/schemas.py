"""Simulator contract (plan 05 §1, API 08 §3.8): ``SimRequest``, ``ActionSpec``,
``ScenarioResult``, ``Recommendation``, ``ZoneBar``, ``FlipCondition``.

Also the minimal **iROAS posterior protocol** the engine needs (:class:`IroasPosterior`) and
a prior-only implementation (:class:`PriorIroas`, label ``scenario``) so the simulator runs
before ``app.ads_effect`` publishes its ``IroasEstimate`` (12: "Nếu ADS-03 trễ, SIM-02 dùng
IroasEstimate prior"). See docs/quyet-dinh/M3-mo-phong.md for how to plug the real one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field, field_validator, model_validator

from app.ads_effect.schemas import IroasEstimate
from app.economics.breakeven import AltThreshold as AltThreshold
from app.economics.breakeven import ZoneBar as ZoneBar

FloatArr = npt.NDArray[np.float64]
Label = Literal["verified", "reference", "scenario"]
ActionKind = Literal["continue", "pause", "reduce_budget", "schedule_hours", "pause_until_restock"]
LABEL_ORDER: dict[str, int] = {"scenario": 0, "reference": 1, "verified": 2}

ACTION_LABEL_VI: dict[str, str] = {
    "continue": "Chạy tiếp",
    "pause": "Tạm dừng",
    "reduce_budget": "Giảm ngân sách",
    "schedule_hours": "Chỉ chạy theo khung giờ",
    "pause_until_restock": "Tạm dừng tới khi hàng về",
}


def min_label(*labels: str) -> Label:
    out = min(labels, key=lambda x: LABEL_ORDER.get(x, 0))
    return out if out in ("verified", "reference") else "scenario"  # type: ignore[return-value]


class ActionSpec(BaseModel):
    kind: ActionKind
    pct: float | None = None  # reduce_budget: 0.3 = −30 %
    windows_local: list[tuple[int, int]] | None = None  # schedule_hours, VN hours [start, end)

    @model_validator(mode="after")
    def _check(self) -> ActionSpec:
        if self.kind == "reduce_budget":
            if self.pct is None or not 0 < self.pct <= 1:
                raise ValueError("reduce_budget cần pct trong (0, 1]")
        elif self.pct is not None:
            raise ValueError("pct chỉ dùng cho reduce_budget")
        if self.kind == "schedule_hours":
            if not self.windows_local:
                raise ValueError("schedule_hours cần windows_local")
            for a, b in self.windows_local:
                if not (0 <= a < b <= 24):
                    raise ValueError("khung giờ phải là [đầu, cuối) trong 0–24")
        elif self.windows_local is not None:
            raise ValueError("windows_local chỉ dùng cho schedule_hours")
        return self

    @property
    def key(self) -> str:
        """Stable id: ``continue``, ``reduce_budget:0.3``, ``schedule_hours:0-2,12-14``."""
        if self.kind == "reduce_budget":
            return f"reduce_budget:{self.pct:g}"
        if self.kind == "schedule_hours" and self.windows_local:
            return "schedule_hours:" + ",".join(f"{a}-{b}" for a, b in self.windows_local)
        return self.kind

    def label_vi(self) -> str:
        base = ACTION_LABEL_VI[self.kind]
        if self.kind == "reduce_budget" and self.pct is not None:
            return f"{base} {self.pct:.0%}"
        if self.kind == "schedule_hours" and self.windows_local:
            return f"{base} " + ", ".join(f"{a:02d}:00–{b:02d}:00" for a, b in self.windows_local)
        return base


class SimRequest(BaseModel):
    campaign_id: str
    start: datetime | None = None  # None = next hour after data_as_of
    horizon_hours: int | None = Field(default=None, ge=1, le=72)  # None = to goal deadline
    goal_id: str
    actions: list[ActionSpec] | None = None  # None = default actions (5 kinds)
    param_overrides: dict[str, float | list[float]] = Field(default_factory=dict)
    samples: int = Field(default=1000, ge=50, le=5000)
    seed: int = 20261111
    session_id: str | None = None  # [DEV] scope of session parameters

    @field_validator("start")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("start phải có múi giờ")
        return v


class Dist(BaseModel):
    mean: float
    p10: float
    p50: float
    p90: float

    @classmethod
    def of(cls, x: FloatArr) -> Dist:
        a = np.asarray(x, dtype=np.float64)
        q = np.quantile(a, [0.1, 0.5, 0.9])
        return cls(mean=float(a.mean()), p10=float(q[0]), p50=float(q[1]), p90=float(q[2]))


class SkuOutcome(BaseModel):
    sku_id: str
    is_bundle: bool
    contribution_per_unit: float  # m at the first horizon hour
    leftover_value_per_unit: float  # V (0 for combos: they hold no stock)
    demand: Dist
    units_sold: Dist
    lost_units: Dist
    leftover_units: Dist | None  # None for combos
    stockout_hour_p50: datetime | None


class ScenarioResult(BaseModel):
    action: ActionSpec
    feasible: bool
    violations: list[str]
    violation_details: list[str] = Field(default_factory=list)  # [DEV] Vietnamese text
    objective: Dist  # J (VND)
    contribution: Dist
    spend: Dist
    leftover_units: Dist
    leftover_value: Dist
    units_sold: Dist
    lost_units: Dist  # [DEV]
    stockout_hour_p50: datetime | None
    per_sku: list[SkuOutcome]
    goal_eligible: bool = True  # [DEV] meets the goal constraint (05 §4)
    p_target: float | None = None  # [DEV] sell_target: P(Σ sold ≥ N)
    goal_note: str | None = None  # [DEV]
    goal_sku: str | None = None
    goal_units_sold: Dist | None = None  # focused SKU quantity; units_sold stays campaign total
    # [DEV] key of a better-ranked action with identical results (05 §9 step 3), e.g.
    # pause_until_restock ≡ pause when nothing arrives in the horizon
    duplicate_of: str | None = None


class FlipCondition(BaseModel):
    factor: Literal["leftover_value_per_unit", "demand_level", "ads_effect", "restock_delay_days"]
    flip_at: float  # value of the factor where best and runner-up swap
    base_value: float  # current value of the factor
    unit: str
    text: str


class InputsRef(BaseModel):
    forecast_ids: list[str]
    estimate_id: str | None
    param_version: dict[str, int]
    goal_id: str
    data_as_of: datetime | None
    panel_version: str | None = None
    iroas_source: Literal["estimate", "prior"] = "prior"


class Recommendation(BaseModel):
    recommendation_id: str
    campaign_id: str
    status: Literal["recommend", "undetermined", "none_meets_goal"]
    best: ActionSpec | None
    runner_up: ActionSpec | None
    p_best_beats_runner_up: float | None
    expected_gain_vnd: int | None
    zone_bar: ZoneBar | None
    flip_conditions: list[FlipCondition]
    confidence: Label
    reasons: list[str]
    inputs_ref: InputsRef
    scenario_id: str | None = None
    created_at: datetime | None = None
    superseded_by: str | None = None
    # [DEV] False when the best is the ONLY action meeting the goal (sell_target /
    # clear_stock): ``runner_up`` is then the best action that misses the goal, shown for
    # reference; ``p_best_beats_runner_up`` / ``expected_gain_vnd`` are None (not rivals) and
    # ``goal_cost_vnd`` = E[J(runner_up)] − E[J(best)] is what meeting the goal costs.
    runner_up_meets_goal: bool = True
    goal_cost_vnd: int | None = None


class SimOutput(BaseModel):
    """Body of ``POST /scenarios/run`` (``data``)."""

    scenario_id: str
    results: list[ScenarioResult]
    recommendation: Recommendation
    cache_hit: bool = False
    input_hash: str
    blocked: bool = False  # missing cost / price: nothing simulated
    # [DEV] run with param_overrides / session_id / its own action list: persisted, but its
    # recommendation never replaces the campaign's active one (service.is_what_if)
    what_if: bool = False
    timings_ms: dict[str, float] = Field(default_factory=dict)
    calculation_trace: dict[str, Any] | None = None


# ---------------------------------------------------------------------- iROAS
@runtime_checkable
class IroasPosterior(Protocol):
    """What the engine needs from an ads-effect estimate (04 §5, §7).

    ``uplift = a·(1 − exp(−A/K))`` with ``K = κ·spend_scale`` and adstock
    ``A_t = spend_t + δ·A_{t−1}`` (campaign level; per SKU ``K_i = K·share_i`` so the
    ratio is the same). ``sample(n, rng)`` draws ``(κ, a)`` from the posterior grid."""

    campaign_id: str
    confidence: Label
    delta: float
    spend_scale: float

    @property
    def estimate_id(self) -> str | None: ...

    def sample(self, n: int, rng: np.random.Generator) -> tuple[FloatArr, FloatArr]: ...


KAPPA_GRID: tuple[float, ...] = (0.5, 1.0, 1.5, 2.5, 4.0, 6.0)
A_GRID: tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(61))  # 0 … 3.0


def prior_weights() -> tuple[FloatArr, FloatArr, FloatArr]:
    """Prior of 04 §5.2 on the grid: ``a ~ LogNormal(log 0,4; 0,8)`` cut to [0, 3] (``a=0``
    takes the mass of the first cell), ``κ`` uniform. Returns ``(kappa, a, w)`` flat."""
    a = np.asarray(A_GRID, dtype=np.float64)
    edges = np.concatenate([[0.0], 0.5 * (a[1:] + a[:-1]), [a[-1] + 0.025]])
    from math import erf, log, sqrt

    def cdf(x: float) -> float:
        if x <= 0:
            return 0.0
        return 0.5 * (1 + erf((log(x) - log(0.4)) / (0.8 * sqrt(2))))

    wa = np.array([cdf(edges[i + 1]) - cdf(edges[i]) for i in range(len(a))])
    wa /= wa.sum()
    k = np.asarray(KAPPA_GRID, dtype=np.float64)
    kk, aa = np.meshgrid(k, a, indexing="ij")
    w = np.repeat(wa[None, :], len(k), axis=0) / len(k)
    return kk.ravel(), aa.ravel(), w.ravel()


class PriorIroas(BaseModel):
    """Prior-only ads effect (no data): label ``scenario``. Used until ``app.ads_effect``
    provides a fitted estimate for the campaign."""

    campaign_id: str
    delta: float = 0.6
    spend_scale: float = 1.0  # mean spend per running hour of the campaign (VND)
    confidence: Label = "scenario"
    reasons: list[str] = Field(
        default_factory=lambda: [
            "Chưa có ước lượng hiệu quả ads cho chiến dịch: dùng phân phối tiên nghiệm"
        ]
    )

    @property
    def estimate_id(self) -> str | None:
        return None

    def sample(self, n: int, rng: np.random.Generator) -> tuple[FloatArr, FloatArr]:
        k, a, w = prior_weights()
        idx = rng.choice(len(w), size=n, p=w)
        return k[idx].astype(np.float64), a[idx].astype(np.float64)

    def uplift(self, adstock: FloatArr, share: float = 1.0, draws: int | None = None) -> FloatArr:
        """``g − 1`` (04 §7 signature). ``draws=None`` → posterior mean of ``(κ, a)``."""
        k, a, w = prior_weights()
        ad = np.asarray(adstock, dtype=np.float64)
        scale = max(self.spend_scale * share, 1e-9)
        if draws is None:
            out: FloatArr = np.sum(
                w[:, None] * a[:, None] * (1 - np.exp(-ad[None, :] / (k[:, None] * scale))),
                axis=0,
            )
            return out
        rng = np.random.default_rng(0)
        ks, as_ = self.sample(draws, rng)
        res: FloatArr = as_[:, None] * (1 - np.exp(-ad[None, :] / (ks[:, None] * scale)))
        return res


class EstimatePosterior:
    """Adapter: a fitted ``app.ads_effect.schemas.IroasEstimate`` as an
    :class:`IroasPosterior` (``K = κ · spend_ref``, draws via ``sample_params``)."""

    def __init__(self, est: IroasEstimate) -> None:
        self.est = est
        self.campaign_id: str = est.campaign_id
        self.confidence: Label = est.confidence
        self.delta: float = float(est.delta)
        self.spend_scale: float = float(est.spend_ref)
        self.reasons: list[str] = list(est.reasons)

    @property
    def estimate_id(self) -> str | None:
        return self.est.estimate_id

    def sample(self, n: int, rng: np.random.Generator) -> tuple[FloatArr, FloatArr]:
        return self.est.sample_params(n, rng)


def as_posterior(obj: IroasPosterior | IroasEstimate) -> IroasPosterior:
    if isinstance(obj, IroasEstimate):
        return EstimatePosterior(obj)
    return obj
