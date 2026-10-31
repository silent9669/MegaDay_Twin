"""Confidence labels (FC-09, plan 03 §7.3).

``scope`` = score of the champion on the **same group and context** as the request (11.11
is ``double_day`` → scores of ``dd_*`` backtests, never normal days).

Rule (03 §7.3, measured as in 03 §7.2):

* ``history_days < 14`` → ``scenario``
* ``verified`` when ``WAPE ≤ (1 + WAPE_FLOOR_TOL) · WAPE_floor`` and the randomised-PIT
  coverage of the 80 % interval is in ``[0.72, 0.88]`` and ``n_sku_days ≥ 30``;
  otherwise ``reference``. Drift active → at most ``reference``.

``WAPE_floor`` is the WAPE the p50 would have if demand were ``NegBin(mean, k)`` around the
model's own mean (computed by the backtest by Monte Carlo, no answer key; the model's
``level_sigma`` is left out so vagueness does not lower the bar). Widening the
distribution does not help: the floor does not move and the PIT coverage goes above 0.88.
Decision log M2 §D.

[DEV] 03 §7.3 has a code block (``scope is None → scenario``) and a sentence ("no score for
this context → reference + reason"). Both are kept: never scored for the group at all →
``scenario``; scored for the group but not this context → ``reference`` with the reason
"chưa kiểm ở bối cảnh này". Decision log M2 C6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Label = Literal["verified", "reference", "scenario"]

MIN_HISTORY_DAYS = 14
WAPE_FLOOR_TOL = 0.10  # WAPE may exceed the unavoidable WAPE by at most 10 % (M2-du-bao §D)
COVERAGE_BAND = (0.72, 0.88)
MIN_SKU_DAYS = 30


class ScopeLike(Protocol):
    @property
    def wape(self) -> float | None: ...

    @property
    def wape_floor(self) -> float | None: ...

    @property
    def coverage(self) -> float | None: ...

    @property
    def n_sku_days(self) -> int | None: ...


def wape_ok(wape: float | None, floor: float | None, tol: float = WAPE_FLOOR_TOL) -> bool:
    return wape is not None and floor is not None and wape <= (1 + tol) * floor


def coverage_ok(cov: float | None) -> bool:
    return cov is not None and COVERAGE_BAND[0] <= cov <= COVERAGE_BAND[1]


def confidence_label(
    scope: ScopeLike | None,
    history_days: int,
    drift_active: bool,
    *,
    tol: float = WAPE_FLOOR_TOL,
) -> Label:
    """The rule of 03 §7.3 (error relative to the unavoidable error)."""
    if history_days < MIN_HISTORY_DAYS or scope is None:
        return "scenario"
    ok = (
        wape_ok(scope.wape, scope.wape_floor, tol)
        and coverage_ok(scope.coverage)
        and (scope.n_sku_days or 0) >= MIN_SKU_DAYS
    )
    label: Label = "verified" if ok else "reference"
    if drift_active and label == "verified":
        label = "reference"
    return label


@dataclass
class LabelDecision:
    label: Label
    reasons: list[str]


def decide_label(
    scope: ScopeLike | None,
    history_days: int,
    drift_active: bool,
    *,
    group_scored: bool,
    nowcast: bool = False,
    tol: float = WAPE_FLOOR_TOL,
) -> LabelDecision:
    """Label + Vietnamese reasons, including the two plan cases above and ``nowcast``
    (at most ``reference``)."""
    reasons: list[str] = []
    if history_days < MIN_HISTORY_DAYS:
        reasons.append(f"Chỉ có {history_days} ngày lịch sử (< {MIN_HISTORY_DAYS}): kịch bản")
        return LabelDecision("scenario", reasons)
    if scope is None:
        if group_scored:
            reasons.append("Chưa kiểm ở bối cảnh này")
            label: Label = "reference"
        else:
            reasons.append("Mô hình chưa được backtest cho nhóm hàng này")
            label = "scenario"
    else:
        label = confidence_label(scope, history_days, drift_active, tol=tol)
        if label == "reference" and not drift_active:
            parts = []
            if scope.wape is None:
                parts.append("không đủ SKU ≥ 1 sp/ngày để tính WAPE")
            elif not wape_ok(scope.wape, scope.wape_floor, tol):
                fl = "—" if scope.wape_floor is None else f"{scope.wape_floor:.0%}"
                parts.append(
                    f"WAPE backtest {scope.wape:.0%} > {1 + tol:.2f} ×"
                    f" mức sai không tránh được {fl}"
                )
            if not coverage_ok(scope.coverage):
                cov = "—" if scope.coverage is None else f"{scope.coverage:.0%}"
                parts.append(f"độ phủ khoảng 80% (PIT) {cov} ngoài 72–88%")
            if (scope.n_sku_days or 0) < MIN_SKU_DAYS:
                parts.append(f"chỉ {scope.n_sku_days or 0} SKU-ngày để kiểm")
            if parts:
                reasons.append("Tham khảo: " + "; ".join(parts))
        if drift_active:
            reasons.append("Đang có cảnh báo lệch dự báo")
    if nowcast and label == "verified":
        label = "reference"
    return LabelDecision(label, reasons)
