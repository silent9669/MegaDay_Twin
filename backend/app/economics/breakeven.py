"""Break-even threshold, zone bar and sensitivity helpers (ECO-04, plan 05 §7–§8).

Closed form (uniform selling rate): ``n`` units left, ``H`` hours, contribution ``m``,
leftover value ``V``. ``Π_c`` = result of ``continue`` (spend and leftover included). If
``pause`` sells ``q`` units:

    Π_p(q) = m·q + V·(n − q) − spend_p
    q* = (Π_c + spend_p − n·V) / (m − V),   x* = q* / H

* ``m ≤ V``: keeping stock always beats selling → pause wins, no zone bar.
* ``q* ≤ 0``: pause wins for sure; ``q* > n``: continue wins for sure (pause cannot sell
  more than ``n``).

Page-10 example (unit-tested): ``n=120, H=4, m=45.000``, continue sells 40/h and spends
450.000đ/h until stock-out (3 h) → ``x* = 22,5`` (V=0), ``16,5`` (V=20.000); pause p10 =
18/h → leftover value that flips the call ``V* = 16.875đ``.

Schemas :class:`ZoneBar` / :class:`AltThreshold` are re-exported by
``app.simulator.schemas`` (API contract 08 §3.8).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel, Field

from app.core.money import format_vnd

AXIS_LABEL = "Lượng bán mỗi giờ nếu pause (sp)"
BreakevenStatus = Literal["ok", "hold_better", "pause_sure", "continue_sure"]
Verdict = Literal["pause_better", "continue_better", "undetermined"]


class AltThreshold(BaseModel):
    label: str  # "hàng dư 20.000đ"
    threshold: float | None  # x* under the alternative V (None: no threshold, see status)
    leftover_value_per_unit: float
    status: BreakevenStatus = "ok"


class BreakevenRequest(BaseModel):
    """Body of ``POST /scenarios/breakeven`` (08 §3.8) = uniform case of 05 §7.1.

    Also carried by :attr:`ZoneBar.inputs` so the browser can redraw the bar while a slider
    moves (``web/src/lib/breakeven.ts`` uses the same formula, decision log M3-web §2)."""

    units_left: float = Field(gt=0)
    hours_left: float = Field(gt=0)
    contribution_per_unit_vnd: float
    spend_per_hour_vnd: float
    rate_continue_per_hour: float = Field(ge=0)
    pause_rate_interval: tuple[float, float, float]
    # net of cost (05 §3): may be < 0 when leftovers are recovered below cost
    leftover_value_per_unit_vnd: float = 0.0
    alt_leftover_values: list[float] = Field(default_factory=lambda: [20_000.0])

    def case(self) -> UniformCase:
        return UniformCase(
            units_left=self.units_left,
            hours_left=self.hours_left,
            contribution_per_unit_vnd=self.contribution_per_unit_vnd,
            spend_per_hour_vnd=self.spend_per_hour_vnd,
            rate_continue_per_hour=self.rate_continue_per_hour,
            pause_rate_interval=self.pause_rate_interval,
            leftover_value_per_unit_vnd=self.leftover_value_per_unit_vnd,
            alt_leftover_values=tuple(self.alt_leftover_values),
        )


class ZoneBar(BaseModel):
    axis_label: str = AXIS_LABEL
    threshold: float  # x*
    interval: tuple[float, float, float]  # p10, p50, p90 of the pause selling rate
    verdict: Verdict
    alt_thresholds: list[AltThreshold] = []
    # [DEV] uniform-case inputs that reproduce ``threshold`` exactly (M3-web §2); None when
    # the simulated bar has no exact uniform equivalent (pause still spends, ...)
    inputs: BreakevenRequest | None = None
    # [DEV] ``verdict`` compares continue and pause on money only. When pause is not
    # allowed (flash-sale commitment, ...) ``pause_feasible`` is False and ``verdict_note``
    # is the sentence to show INSTEAD of "pause tốt hơn": the bar then measures the cost of
    # the constraint, it is not a recommendation to pause (decision log M3-mo-phong Q14).
    pause_feasible: bool = True
    pause_violations: list[str] = []
    verdict_note: str | None = None


@dataclass(frozen=True)
class BreakevenPoint:
    status: BreakevenStatus
    q_star: float | None  # units over the horizon
    x_star: float | None  # units per hour

    @property
    def has_threshold(self) -> bool:
        return self.status == "ok"


def breakeven(
    pi_c: float, n: float, hours: float, m: float, v: float = 0.0, spend_p: float = 0.0
) -> BreakevenPoint:
    """``q*`` / ``x*`` of 05 §7.1 with the edge cases named."""
    if m <= v:
        return BreakevenPoint("hold_better", None, None)
    q = (pi_c + spend_p - n * v) / (m - v)
    x = q / hours if hours > 0 else math.inf
    if q <= 0:
        return BreakevenPoint("pause_sure", q, x)
    if q > n:
        return BreakevenPoint("continue_sure", q, x)
    return BreakevenPoint("ok", q, x)


def verdict(threshold: float, interval: Sequence[float]) -> Verdict:
    """p10 > x* → pause better; p90 < x* → continue better; otherwise undetermined."""
    p10, _p50, p90 = interval
    if p10 > threshold:
        return "pause_better"
    if p90 < threshold:
        return "continue_better"
    return "undetermined"


def alt_label(v: float) -> str:
    return f"hàng dư {format_vnd(round(v))}"


def make_zone_bar(
    pi_c: float,
    n: float,
    hours: float,
    m: float,
    v: float,
    interval: Sequence[float],
    *,
    spend_p: float = 0.0,
    alt_values: Sequence[tuple[float, float]] = (),
) -> tuple[ZoneBar | None, BreakevenPoint]:
    """Zone bar or ``None`` when there is no meaningful threshold (``m ≤ V``). With
    ``q* ≤ 0`` / ``q* > n`` the threshold is clamped to ``0`` / ``n/H`` so the bar still
    shows a sure verdict. ``alt_values`` = ``[(V_alt, Π_c under V_alt), …]`` (dashed lines
    on the UI)."""
    point = breakeven(pi_c, n, hours, m, v, spend_p)
    alts = []
    for av, alt_pi in alt_values:
        ap = breakeven(alt_pi, n, hours, m, av, spend_p)
        alts.append(
            AltThreshold(
                label=alt_label(av),
                threshold=_clamp(ap, n, hours),
                leftover_value_per_unit=float(av),
                status=ap.status,
            )
        )
    thr = _clamp(point, n, hours)
    if thr is None:
        return None, point
    iv = (float(interval[0]), float(interval[1]), float(interval[2]))
    if point.status == "pause_sure":
        vd: Verdict = "pause_better"
    elif point.status == "continue_sure":
        vd = "continue_better"
    else:
        vd = verdict(thr, iv)
    return ZoneBar(threshold=thr, interval=iv, verdict=vd, alt_thresholds=alts), point


def _clamp(p: BreakevenPoint, n: float, hours: float) -> float | None:
    if p.status == "hold_better" or p.x_star is None:
        return None
    if p.status == "pause_sure":
        return 0.0
    if p.status == "continue_sure":
        return n / hours if hours > 0 else 0.0
    return p.x_star


def flip_leftover_value(pi_c: float, m: float, n: float, q_pause: float) -> float | None:
    """Leftover value ``V*`` at which pause (selling ``q_pause``) ties with continue:
    ``m·q + V·(n − q) = Π_c``. ``None`` when pause sells everything (V has no effect)."""
    if n - q_pause <= 0:
        return None
    return (pi_c - m * q_pause) / (n - q_pause)


# ---------------------------------------------------------------------- uniform case
@dataclass(frozen=True)
class UniformCase:
    """Inputs of ``POST /scenarios/breakeven`` (08 §3.8)."""

    units_left: float
    hours_left: float
    contribution_per_unit_vnd: float
    spend_per_hour_vnd: float
    rate_continue_per_hour: float
    pause_rate_interval: tuple[float, float, float]
    leftover_value_per_unit_vnd: float = 0.0
    alt_leftover_values: tuple[float, ...] = (20_000.0,)


@dataclass(frozen=True)
class UniformResult:
    pi_continue: float
    zone_bar: ZoneBar | None
    point: BreakevenPoint
    flip_leftover_value_vnd: float | None  # V* from the pause p10 rate


def continue_result(case: UniformCase) -> float:
    """``Π_c``: continue sells at a uniform rate until the stock runs out and spends
    ``spend_per_hour`` while it runs; leftover valued at ``V``."""
    n, h, r = case.units_left, case.hours_left, case.rate_continue_per_hour
    sold = min(n, r * h)
    hours_running = min(h, n / r) if r > 0 else h
    v = case.leftover_value_per_unit_vnd
    return (
        case.contribution_per_unit_vnd * sold
        + v * (n - sold)
        - case.spend_per_hour_vnd * hours_running
    )


def uniform_breakeven(case: UniformCase) -> UniformResult:
    pi_c = continue_result(case)
    alts = [
        (v, continue_result(replace(case, leftover_value_per_unit_vnd=v)))
        for v in case.alt_leftover_values
        if v != case.leftover_value_per_unit_vnd
    ]
    bar, point = make_zone_bar(
        pi_c,
        case.units_left,
        case.hours_left,
        case.contribution_per_unit_vnd,
        case.leftover_value_per_unit_vnd,
        case.pause_rate_interval,
        alt_values=alts,
    )
    q10 = min(case.units_left, case.pause_rate_interval[0] * case.hours_left)
    flip = flip_leftover_value(pi_c, case.contribution_per_unit_vnd, case.units_left, q10)
    return UniformResult(pi_c, bar, point, flip)


# ---------------------------------------------------------------------- sensitivity
def bisect_sign_change(
    f: Callable[[float], float], lo: float, hi: float, steps: int = 6
) -> float | None:
    """Point in ``[lo, hi]`` where ``f`` changes sign (``f(lo)`` and ``f(hi)`` must differ
    in sign, else ``None``). ``steps`` halvings (05 §8: 6 steps × 300 samples)."""
    flo, fhi = f(lo), f(hi)
    if flo == 0:
        return lo
    if fhi == 0:
        return hi
    if (flo > 0) == (fhi > 0):
        return None
    a, b, fa = lo, hi, flo
    for _ in range(steps):
        mid = 0.5 * (a + b)
        fm = f(mid)
        if fm == 0:
            return mid
        if (fm > 0) == (fa > 0):
            a, fa = mid, fm
        else:
            b = mid
    # linear interpolation inside the last bracket
    fa_, fb_ = f(a), f(b)
    if fa_ == fb_:
        return 0.5 * (a + b)
    return a - fa_ * (b - a) / (fb_ - fa_)


def linear_root(f0: float, slope: float) -> float | None:
    """Root of ``f0 + slope·x`` (exact, for factors that enter ``J`` linearly)."""
    if slope == 0:
        return None
    return -f0 / slope
