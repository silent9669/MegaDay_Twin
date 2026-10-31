"""CUSUM drift detection (FC-07, plan 03 §9).

Run each hour during the day for SKUs with a forecast, on in-stock hours only::

    z_t  = (y_t − λ_t) / sqrt(λ_t + λ_t²/k)
    S⁺_t = max(0, S⁺_{t−1} + z_t − 0.5)
    S⁻_t = max(0, S⁻_{t−1} − z_t − 0.5)

Hours with ``λ < 0.25`` are skipped too (see ``MIN_LAMBDA``). Alarm when ``S⁺ > 5``
(above forecast) or ``S⁻ > 5`` (below); both reset to 0 after an alarm. Stockout hours are
skipped, so an empty shelf reads as "hết hàng", not as falling demand (``AOGIUNHIET-M`` on
11.11). ``up`` → ``nowcast`` for 24 h; ``down`` → only an alert with a checklist for the AI
agent (no automatic "trend is over").

[DEV] **Product shock detector** (decision log M2 "Hoàn thiện M2"). Variants of one
product share their demand shocks (a colour does not go viral alone, M2 C2). For a product
with ≥ 2 variants, ``up`` is detected on the product with the CUSUM of the NegBin
log-likelihood ratio "demand is ρ = 2× the forecast", summed over the variants in stock::

    LLR_v(t) = y·log(ρλ(λ + k) / (λ(ρλ + k))) + k·log((λ + k) / (ρλ + k))
    S_t      = max(0, S_{t−1} + Σ_v LLR_v(t)),   alarm when S_t > 6

It weighs each hour by how much it tells about a doubling (busy hours count more, a quiet
hour with one order little), and three variants accumulate evidence ~3× faster than one.
Threshold 6 = the smallest of {4, …, 10} whose alarm rate on past days (normal days,
8.8/9.9/10.10 and the days around them) is not above the per-SKU CUSUM's — calibrated
without the answer key. The alarm is raised on every variant in stock (``scope =
"product"``). ``down`` stays the per-SKU CUSUM above (stockout hours skipped), and
single-variant products keep the per-SKU CUSUM both ways.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
import numpy.typing as npt

from app.core.events import DriftDetected

FloatArr = npt.NDArray[np.float64]
BoolArr = npt.NDArray[np.bool_]

THRESHOLD = 5.0
SLACK = 0.5
# [DEV] hours whose forecast is below this are skipped: with λ ≈ 0.05 a single order gives
# z ≈ 4, the Normal approximation behind the standardised residual does not hold (night
# hours). Decision log M2 C7.
MIN_LAMBDA = 0.25
NOWCAST_HOURS = 24
# [DEV] product shock detector (decision log M2 "Hoàn thiện M2"): the shift it is tuned to
# (demand 2× the forecast) and its threshold, calibrated on past days so that the alarm
# rate is not above the per-SKU CUSUM's
SHOCK_RATIO = 2.0
LLR_THRESHOLD = 6.0
DOWN_CHECKLIST = (
    "Giá hoặc voucher có đổi không",
    "Có biến thể nào hết hàng không",
    "Quảng cáo có bị tắt hoặc hết ngân sách không",
    "Dữ liệu đơn có bị trễ không",
)


@dataclass
class DriftAlarm:
    sku_id: str
    hour: datetime  # UTC start of the hour that triggered the alarm
    direction: Literal["up", "down"]
    stat: float
    checklist: list[str] = field(default_factory=list)
    scope: Literal["sku", "product"] = "sku"  # [DEV] which CUSUM raised it

    @property
    def nowcast_until(self) -> datetime | None:
        if self.direction != "up":
            return None
        return self.hour + timedelta(hours=1 + NOWCAST_HOURS)

    def event(self) -> DriftDetected:
        return DriftDetected(sku_id=self.sku_id, direction=self.direction, stat=self.stat)


@dataclass
class CusumTrace:
    s_pos: FloatArr
    s_neg: FloatArr
    z: FloatArr
    alarms: list[tuple[int, Literal["up", "down"], float]]


def _run(z: FloatArr, use: BoolArr, threshold: float, slack: float) -> CusumTrace:
    """Two-sided CUSUM over standardised residuals ``z`` (hours with ``use`` only)."""
    n = len(z)
    sp = np.zeros(n)
    sn = np.zeros(n)
    zz = np.where(use, z, 0.0).astype(np.float64)
    alarms: list[tuple[int, Literal["up", "down"], float]] = []
    s_pos = s_neg = 0.0
    for t in range(n):
        if use[t]:
            s_pos = max(0.0, s_pos + float(zz[t]) - slack)
            s_neg = max(0.0, s_neg - float(zz[t]) - slack)
            if s_pos > threshold:
                alarms.append((t, "up", s_pos))
                s_pos = s_neg = 0.0
            elif s_neg > threshold:
                alarms.append((t, "down", s_neg))
                s_pos = s_neg = 0.0
        sp[t], sn[t] = s_pos, s_neg
    return CusumTrace(s_pos=sp, s_neg=sn, z=zz, alarms=alarms)


def cusum(
    y: FloatArr,
    lam: FloatArr,
    k: float,
    instock: BoolArr | None = None,
    *,
    threshold: float = THRESHOLD,
    slack: float = SLACK,
    min_lambda: float = MIN_LAMBDA,
) -> CusumTrace:
    """CUSUM over hourly actuals ``y`` vs forecast mean ``lam`` (same length)."""
    yy = np.asarray(y, dtype=np.float64)
    lt = np.asarray(lam, dtype=np.float64)
    use = np.ones(len(yy), dtype=bool) if instock is None else np.asarray(instock, dtype=bool)
    use = use & np.isfinite(yy) & (lt >= min_lambda)
    kk = max(float(k), 1e-6)
    var = np.maximum(lt + lt * lt / kk, 1e-12)
    z = np.where(use, (np.nan_to_num(yy) - lt) / np.sqrt(var), 0.0)
    return _run(z, use, threshold, slack)


def nb_llr(y: FloatArr, mu: FloatArr, k: FloatArr, rho: float) -> FloatArr:
    """Log-likelihood ratio of NegBin(mean ``rho·mu``, k) against NegBin(mean ``mu``, k)."""
    m0 = np.maximum(mu, 1e-9)
    m1 = rho * m0
    return np.asarray(
        y * np.log(m1 * (m0 + k) / (m0 * (m1 + k))) + k * np.log((m0 + k) / (m1 + k)),
        dtype=np.float64,
    )


def llr_cusum_pooled(
    y: FloatArr,
    lam: FloatArr,
    k: Sequence[float],
    instock: BoolArr | None = None,
    *,
    rho: float = SHOCK_RATIO,
    threshold: float = LLR_THRESHOLD,
    min_lambda: float = MIN_LAMBDA,
) -> CusumTrace:
    """Upward CUSUM of the NegBin log-likelihood ratio "demand is ``rho``× the forecast",
    summed over the in-stock variants of each hour (``[V, T]`` inputs)::

        S_t = max(0, S_{t−1} + Σ_v LLR_v(t)),  alarm when S_t > threshold

    Hours whose summed forecast is below ``min_lambda`` are skipped (as in :func:`cusum`).
    ``s_pos`` holds ``S``; ``z`` the hour's summed LLR; ``s_neg`` is unused (zeros)."""
    yy = np.atleast_2d(np.asarray(y, dtype=np.float64))
    ll = np.atleast_2d(np.asarray(lam, dtype=np.float64))
    ins = np.ones_like(yy, dtype=bool) if instock is None else np.atleast_2d(instock)
    use_v = ins.astype(bool) & np.isfinite(yy)
    kk = np.maximum(np.asarray(k, dtype=np.float64), 1e-6)[:, None]
    llr = np.where(use_v, nb_llr(np.nan_to_num(yy), ll, kk, rho), 0.0).sum(axis=0)
    use = use_v.any(axis=0) & (np.where(use_v, ll, 0.0).sum(axis=0) >= min_lambda)
    n = llr.shape[0]
    sp = np.zeros(n)
    alarms: list[tuple[int, Literal["up", "down"], float]] = []
    s = 0.0
    for t in range(n):
        if use[t]:
            s = max(0.0, s + float(llr[t]))
            if s > threshold:
                alarms.append((t, "up", s))
                s = 0.0
        sp[t] = s
    return CusumTrace(s_pos=sp, s_neg=np.zeros(n), z=np.where(use, llr, 0.0), alarms=alarms)


def _alarm(
    sku_id: str,
    hour: datetime,
    direction: Literal["up", "down"],
    stat: float,
    scope: Literal["sku", "product"],
) -> DriftAlarm:
    return DriftAlarm(
        sku_id=sku_id,
        hour=hour,
        direction=direction,
        stat=float(stat),
        checklist=list(DOWN_CHECKLIST) if direction == "down" else [],
        scope=scope,
    )


def scan(
    sku_id: str,
    hours: Sequence[datetime],
    y: FloatArr,
    lam: FloatArr,
    k: float,
    instock: BoolArr | None = None,
) -> list[DriftAlarm]:
    trace = cusum(y, lam, k, instock)
    return [_alarm(sku_id, hours[t], d, s, "sku") for t, d, s in trace.alarms]


def scan_product(
    sku_ids: Sequence[str],
    hours: Sequence[datetime],
    y: FloatArr,
    lam: FloatArr,
    k: Sequence[float],
    instock: BoolArr,
    *,
    threshold: float = LLR_THRESHOLD,
) -> list[DriftAlarm]:
    """Product shock detector (``[V, T]`` inputs, :func:`llr_cusum_pooled`); each ``up``
    alarm is raised on the variants in stock at that hour."""
    ins = np.atleast_2d(np.asarray(instock, dtype=bool))
    trace = llr_cusum_pooled(y, lam, k, ins, threshold=threshold)
    return [
        _alarm(sku, hours[t], d, s, "product")
        for t, d, s in trace.alarms
        for v, sku in enumerate(sku_ids)
        if ins[v, t]
    ]


def merge_alarms(*groups: Sequence[DriftAlarm]) -> list[DriftAlarm]:
    """Union of alarm lists, one per (SKU, hour, direction) — the first list wins — by hour."""
    seen: dict[tuple[str, datetime, str], DriftAlarm] = {}
    for g in groups:
        for a in g:
            seen.setdefault((a.sku_id, a.hour, a.direction), a)
    return sorted(seen.values(), key=lambda a: (a.hour, a.sku_id))


def active_up(alarms: Sequence[DriftAlarm], at: datetime) -> set[str]:
    """SKUs whose last ``up`` alarm still keeps ``nowcast`` on at ``at``."""
    out: set[str] = set()
    for a in alarms:
        until = a.nowcast_until
        if until is not None and a.hour < at <= until:
            out.add(a.sku_id)
    return out
