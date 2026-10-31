"""ECO-04 · closed-form break-even, zone bar and the page-10 example (05 §7–§8)."""

from __future__ import annotations

import pytest

from app.economics.breakeven import (
    UniformCase,
    bisect_sign_change,
    breakeven,
    continue_result,
    flip_leftover_value,
    linear_root,
    make_zone_bar,
    uniform_breakeven,
    verdict,
)

PAGE10 = UniformCase(
    units_left=120,
    hours_left=4,
    contribution_per_unit_vnd=45_000,
    spend_per_hour_vnd=450_000,
    rate_continue_per_hour=40,
    pause_rate_interval=(18, 24, 31),
    leftover_value_per_unit_vnd=0,
)


def test_page10_pi_continue() -> None:
    assert continue_result(PAGE10) == 4_050_000


def test_page10_threshold_v0() -> None:
    p = breakeven(4_050_000, 120, 4, 45_000, 0)
    assert p.status == "ok"
    assert p.q_star == 90
    assert p.x_star == 22.5


def test_page10_threshold_v20000() -> None:
    p = breakeven(4_050_000, 120, 4, 45_000, 20_000)
    assert p.q_star == 66
    assert p.x_star == 16.5


def test_page10_flip_leftover_value() -> None:
    assert flip_leftover_value(4_050_000, 45_000, 120, 18 * 4) == 16_875


def test_page10_uniform_endpoint() -> None:
    """``POST /scenarios/breakeven`` example of 08 §3.8."""
    res = uniform_breakeven(PAGE10)
    bar = res.zone_bar
    assert bar is not None
    assert bar.threshold == 22.5
    assert bar.interval == (18, 24, 31)
    assert bar.verdict == "undetermined"
    assert bar.axis_label == "Lượng bán mỗi giờ nếu pause (sp)"
    assert [(a.label, a.threshold) for a in bar.alt_thresholds] == [("hàng dư 20.000đ", 16.5)]
    assert res.flip_leftover_value_vnd == 16_875


def test_m_le_v_has_no_bar() -> None:
    p = breakeven(4_050_000, 120, 4, 20_000, 20_000)
    assert p.status == "hold_better" and p.x_star is None
    bar, point = make_zone_bar(4_050_000, 120, 4, 20_000, 30_000, (1, 2, 3))
    assert bar is None and point.status == "hold_better"


def test_q_star_le_zero_pause_sure() -> None:
    p = breakeven(-1_000_000, 120, 4, 45_000, 0)
    assert p.status == "pause_sure" and p.q_star is not None and p.q_star <= 0
    bar, _ = make_zone_bar(-1_000_000, 120, 4, 45_000, 0, (0, 1, 2))
    assert bar is not None and bar.verdict == "pause_better" and bar.threshold == 0


def test_q_star_above_n_continue_sure() -> None:
    p = breakeven(6_000_000, 120, 4, 45_000, 0)
    assert p.status == "continue_sure" and p.q_star is not None and p.q_star > 120
    bar, _ = make_zone_bar(6_000_000, 120, 4, 45_000, 0, (25, 28, 30))
    assert bar is not None and bar.verdict == "continue_better" and bar.threshold == 30


@pytest.mark.parametrize(
    ("interval", "expected"),
    [
        ((23, 25, 30), "pause_better"),
        ((10, 15, 22), "continue_better"),
        ((18, 24, 31), "undetermined"),
    ],
)
def test_verdict(interval: tuple[float, float, float], expected: str) -> None:
    assert verdict(22.5, interval) == expected


def test_bisect_and_linear_root() -> None:
    x = bisect_sign_change(lambda v: v - 0.3, 0.0, 1.0)
    assert x is not None and abs(x - 0.3) < 1e-9  # linear interpolation in the last bracket
    assert bisect_sign_change(lambda v: v + 1, 0.0, 1.0) is None
    assert linear_root(-10.0, 2.0) == 5.0
    assert linear_root(1.0, 0.0) is None
