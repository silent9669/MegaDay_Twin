"""``ZoneBar.inputs`` (M3-web §2): the uniform case attached to a simulated zone bar must
reproduce its threshold through ``uniform_breakeven`` (= ``web/src/lib/breakeven.ts``)."""

from __future__ import annotations

import pytest

from app.economics.breakeven import make_zone_bar, uniform_breakeven
from app.simulator.recommend import uniform_inputs

# page-10 numbers: continue sells 120 units over 4 h, spends 1.35 M → Π_c = 4.05 M
PI_C = 45_000 * 120 - 1_350_000


def test_inputs_reproduce_threshold() -> None:
    bar, _ = make_zone_bar(PI_C, 120, 4, 45_000, 0.0, (18, 24, 31))
    assert bar is not None and bar.threshold == pytest.approx(22.5)
    req = uniform_inputs(bar, PI_C, 120, 4.0, 45_000, 0.0, 120.0, 0.0)
    assert req is not None
    again = uniform_breakeven(req.case()).zone_bar
    assert again is not None and again.threshold == pytest.approx(bar.threshold)
    assert req.spend_per_hour_vnd == pytest.approx(337_500)


def test_inputs_with_leftover_value() -> None:
    v = 20_000.0
    pi_c = 45_000 * 100 + v * 20 - 1_200_000
    bar, _ = make_zone_bar(pi_c, 120, 4, 45_000, v, (18, 24, 31))
    assert bar is not None
    req = uniform_inputs(bar, pi_c, 120, 4.0, 45_000, v, 100.0, 0.0)
    assert req is not None and req.alt_leftover_values == [0.0]
    again = uniform_breakeven(req.case()).zone_bar
    assert again is not None and again.threshold == pytest.approx(bar.threshold)


def test_no_inputs_when_pause_spends() -> None:
    bar, _ = make_zone_bar(PI_C, 120, 4, 45_000, 0.0, (18, 24, 31), spend_p=100_000)
    assert bar is not None
    assert uniform_inputs(bar, PI_C, 120, 4.0, 45_000, 0.0, 120.0, 100_000) is None
