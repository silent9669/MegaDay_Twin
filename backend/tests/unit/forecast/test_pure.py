"""Pure functions: censoring, metrics, labels, CUSUM, champion selection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.core.events import DriftDetected
from app.forecast import censoring
from app.forecast.drift import (
    active_up,
    cusum,
    llr_cusum_pooled,
    merge_alarms,
    nb_llr,
    scan,
    scan_product,
)
from app.forecast.labels import WAPE_FLOOR_TOL, confidence_label, coverage_ok, decide_label, wape_ok
from app.forecast.metrics import (
    ScoreRow,
    aggregate,
    bias,
    coverage,
    floor_stats,
    pinball,
    pit_coverage,
    randomized_pit,
    wape,
    wape_floor,
)
from app.forecast.registry import select_champions


# ---------------------------------------------------------------------- censoring
def _flat(days: int, rate: float) -> tuple[np.ndarray, np.ndarray]:
    prof = np.full(24, 1 / 24)
    y = np.full((1, days, 24), rate / 24)
    exp = np.broadcast_to(prof, (1, days, 24)).copy()
    return y, exp


def test_censoring_recovers_level_and_demand() -> None:
    y, exp = _flat(20, 24.0)  # 1 unit / hour
    so = np.zeros_like(y, dtype=bool)
    so[0, 15:, 12:] = True  # afternoon stockouts on the last 5 days
    y_obs = np.where(so, 0.0, y)
    pres = np.ones_like(so)
    conf = np.ones_like(y)
    c = censoring.adjust_demand(y_obs, pres, so, conf, exp)
    raw = censoring.adjust_demand(y_obs, pres, so, conf, exp, censoring=False)
    assert np.allclose(c.level[0, -1], 24.0)
    assert np.allclose(c.demand_adj[0, 15:], 24.0)
    assert np.allclose(raw.demand_adj[0, 15:], 12.0)
    assert c.stockout_hours[0, -1] == 12


def test_censoring_keeps_last_units_of_a_stockout_hour() -> None:
    y, exp = _flat(3, 24.0)
    so = np.zeros_like(y, dtype=bool)
    so[0, 2, 10] = True
    y[0, 2, 10] = 5.0  # sold the last 5 units in the hour the shelf emptied
    c = censoring.adjust_demand(y, np.ones_like(so), so, np.ones_like(y), exp)
    assert c.demand_adj[0, 2] == pytest.approx(23 + 5)


def test_low_confidence_hours_count_half() -> None:
    w = censoring.level_weights(np.array([True, True, False]), np.array([1.0, 0.5, 1.0]))
    assert w.tolist() == [1.0, 0.5, 0.0]


def _days(n_skus: int, days: int, rate: float) -> tuple[np.ndarray, np.ndarray]:
    prof = np.full(24, 1 / 24)
    y = np.full((n_skus, days, 24), rate / 24)
    exp = np.broadcast_to(prof, (n_skus, days, 24)).copy()
    return y, exp


def test_long_stockout_carries_level_from_before_the_shelf_emptied() -> None:
    # 20 days at 24/day, then the last 2 in-stock days sell almost nothing (shelf nearly
    # empty) and the item is out for 25 days: the carried level is the pre-stockout one,
    # not the tail of the trailing window (M2 "Hoàn thiện M2")
    y, exp = _days(1, 47, 24.0)
    y[0, 20:22] = 1 / 24
    so = np.zeros_like(y, dtype=bool)
    so[0, 22:] = True
    y = np.where(so, 0.0, y)
    c = censoring.adjust_demand(y, np.ones_like(so), so, np.ones_like(y), exp)
    pre = (5 * 24 + 2 * 1) / 7  # last 7 days of in-stock exposure
    assert c.carried[0, -1] and not c.carried[0, 21]
    assert c.level[0, -1] == pytest.approx(pre, rel=1e-6)
    assert c.demand_adj[0, -1] == pytest.approx(pre, rel=1e-6)


def test_long_stockout_follows_siblings_and_fades() -> None:
    # A and B are variants of one product. A sells out on day 10; B doubles from day 10
    # and is in stock until day 30 (its 14-day level stays reliable ~10 days more)
    y, exp = _days(2, 50, 24.0)
    y[1, 10:] = 48 / 24
    so = np.zeros_like(y, dtype=bool)
    so[0, 10:] = True
    so[1, 30:] = True
    y = np.where(so, 0.0, y)
    ones = np.ones_like(y)
    prod = np.array([0, 0])
    c = censoring.adjust_demand(y, np.ones_like(so), so, ones, exp, product=prod)
    alone = censoring.adjust_demand(y[:1], np.ones_like(so[:1]), so[:1], ones[:1], exp[:1])
    assert alone.level[0, 29] == pytest.approx(24.0)  # no sibling: flat
    assert 24.0 * 1.4 < c.level[0, 29] <= 24.0 * 2.05  # follows B while B is observed
    # once B is out too, the evidence ages: the growth fades back towards the base
    assert c.level[0, 49] < c.level[0, 40] <= c.level[0, 29]
    assert c.level[0, 49] < 24.0 * 1.2


def test_long_stockout_trend_item_follows_damped_trend() -> None:
    days = 60
    rate = 4.0 * np.exp(0.04 * np.arange(days))  # +4 %/day trend
    y, exp = _days(1, days, 1.0)
    y[0] = (rate[:, None] / 24) * np.ones(24)
    so = np.zeros_like(y, dtype=bool)
    so[0, 40:] = True
    y = np.where(so, 0.0, y)
    args = (y, np.ones_like(so), so, np.ones_like(y), exp)
    flat = censoring.adjust_demand(*args)
    tr = censoring.adjust_demand(*args, trend=np.array([True]))
    assert tr.level[0, -1] > flat.level[0, -1]
    cap = np.exp(censoring.SLOPE_CLIP * censoring.TREND_PHI / (1 - censoring.TREND_PHI))
    assert tr.level[0, -1] <= flat.level[0, -1] * cap  # damped: bounded growth


def test_low_confidence_stockout_hour_adds_half_the_correction() -> None:
    y, exp = _days(1, 20, 24.0)
    so = np.zeros_like(y, dtype=bool)
    so[0, -1, 12:] = True
    y = np.where(so, 0.0, y)
    conf = np.ones_like(y)
    conf[0, -1, 12:] = 0.5
    full = censoring.adjust_demand(y, np.ones_like(so), so, np.ones_like(y), exp)
    half = censoring.adjust_demand(y, np.ones_like(so), so, conf, exp)
    assert full.demand_adj[0, -1] == pytest.approx(24.0)
    assert half.demand_adj[0, -1] == pytest.approx(12.0 + 6.0)


# ---------------------------------------------------------------------- metrics
def test_metrics_basic() -> None:
    y = np.array([10.0, 0.0, 5.0])
    p = np.array([8.0, 1.0, 5.0])
    assert wape(y, p) == pytest.approx(3 / 15)
    assert bias(y, p) == pytest.approx(-1 / 15)
    assert wape(np.zeros(2), np.ones(2)) is None
    assert pinball(np.array([2.0]), np.array([1.0]), 0.9) == pytest.approx(0.9)
    assert pinball(np.array([0.0]), np.array([1.0]), 0.9) == pytest.approx(0.1)
    assert coverage(y, p - 1, p + 1) == pytest.approx(2 / 3)


def test_aggregate_drops_days_without_instock_hours() -> None:
    days = pd.DataFrame(
        {
            "sku_id": ["a", "a", "b"],
            "y": [10.0, 0.0, 4.0],
            "p10": [5.0, 0.0, 2.0],
            "p50": [9.0, 0.0, 4.0],
            "p90": [12.0, 0.0, 6.0],
            "wape_eligible": [True, True, False],
            "pb10": [0.1, 0.0, 0.1],
            "pb50": [0.5, 0.0, 0.2],
            "pb90": [0.1, 0.0, 0.1],
            "n_hours": [24, 0, 24],
            "pit_u": [0.5, 0.5, 0.95],
            "floor_mae": [2.0, 0.0, 1.0],
            "floor_mean": [10.0, 0.0, 4.0],
        }
    )
    r = aggregate(days, "bt", "on_dinh", "normal", "m")
    assert r.n_sku_days == 2
    assert r.wape == pytest.approx(0.1)  # only the eligible SKU
    assert r.coverage_interval == 1.0
    assert r.coverage == 0.5  # PIT: 0.5 in, 0.95 out
    assert r.wape_floor == pytest.approx(0.2)  # eligible SKU only


# ---------------------------------------------------------------------- labels
def _score(w: float | None, c: float | None, n: int = 60, floor: float | None = 0.35) -> ScoreRow:
    return ScoreRow(
        "m", "bt", "on_dinh", "normal", 10, n, w, 0.0, 0.1, 0.2, 0.1, c, wape_floor=floor
    )


def test_confidence_label_rules() -> None:
    # WAPE 37 % against an unavoidable 35 % passes (≤ 1.15 ×); a fixed 30 % would not
    assert confidence_label(_score(0.37, 0.8), 30, False) == "verified"
    assert confidence_label(_score(0.37, 0.8), 30, True) == "reference"
    assert confidence_label(_score(0.42, 0.8), 30, False) == "reference"  # > 1.15 × 0.35
    assert confidence_label(_score(0.2, 0.8, floor=None), 30, False) == "reference"
    assert confidence_label(_score(0.2, 0.95), 30, False) == "reference"
    assert confidence_label(_score(0.2, 0.8, n=20), 30, False) == "reference"
    assert confidence_label(_score(0.1, 0.8), 7, False) == "scenario"
    assert confidence_label(None, 30, False) == "scenario"


def test_decide_label_context_and_nowcast() -> None:
    d = decide_label(None, 60, False, group_scored=True)
    assert d.label == "reference" and "Chưa kiểm ở bối cảnh này" in d.reasons
    assert decide_label(None, 60, False, group_scored=False).label == "scenario"
    assert decide_label(_score(0.3, 0.8), 60, False, group_scored=True, nowcast=True).label == (
        "reference"
    )
    assert decide_label(_score(0.2, 0.8), 5, False, group_scored=True).label == "scenario"


# ---------------------------------------------------------------------- drift
def test_cusum_quiet_then_up() -> None:
    rng = np.random.default_rng(0)
    lam = np.full(48, 2.0)
    y = rng.poisson(lam).astype(float)
    assert not cusum(y, lam, 3.0).alarms
    y2 = y.copy()
    y2[30:] = rng.poisson(8.0, 18)
    tr = cusum(y2, lam, 3.0)
    assert tr.alarms and tr.alarms[0][1] == "up"
    assert 30 <= tr.alarms[0][0] <= 33
    # reset after alarm
    t = tr.alarms[0][0]
    assert tr.s_pos[t] == 0.0


def test_cusum_skips_stockout_hours() -> None:
    lam = np.full(24, 3.0)
    y = np.full(24, 3.0)
    y[8:] = 0.0  # shelf empty from 08:00
    ins = np.ones(24, dtype=bool)
    assert cusum(y, lam, 3.0, ins).alarms[0][1] == "down"
    ins[8:] = False
    assert not cusum(y, lam, 3.0, ins).alarms


def test_scan_events_and_nowcast_window() -> None:
    t0 = datetime(2026, 11, 11, 0, tzinfo=UTC)
    hours = [t0 + timedelta(hours=h) for h in range(10)]
    lam = np.full(10, 1.0)
    y = np.array([1, 1, 6, 7, 8, 9, 9, 9, 9, 9], dtype=float)
    al = scan("X", hours, y, lam, 3.0)
    assert al[0].direction == "up"
    ev = al[0].event()
    assert isinstance(ev, DriftDetected) and ev.sku_id == "X"
    assert "X" in active_up(al, al[0].hour + timedelta(hours=1))
    assert "X" not in active_up(al, al[-1].hour + timedelta(hours=26))


def test_cusum_down_alarm_has_checklist() -> None:
    t0 = datetime(2026, 11, 11, 0, tzinfo=UTC)
    hours = [t0 + timedelta(hours=h) for h in range(12)]
    al = scan("X", hours, np.zeros(12), np.full(12, 4.0), 3.0)
    assert al[0].direction == "down" and al[0].checklist


def test_nb_llr_negative_under_forecast_positive_under_doubling() -> None:
    rng = np.random.default_rng(1)
    lam, k = np.full(20000, 1.5), 3.0
    p0 = k / (k + lam)
    y0 = rng.negative_binomial(k, p0).astype(float)
    y1 = rng.negative_binomial(k, k / (k + 2 * lam)).astype(float)
    assert nb_llr(y0, lam, np.full_like(lam, k), 2.0).mean() < 0
    assert nb_llr(y1, lam, np.full_like(lam, k), 2.0).mean() > 0


def test_product_detector_is_faster_than_one_variant_and_quiet_without_shock() -> None:
    rng = np.random.default_rng(3)
    lam = np.full((3, 24), 1.2)
    y = rng.poisson(lam).astype(float)
    assert not llr_cusum_pooled(y, lam, [3.0] * 3).alarms
    y[:, 8:] = rng.poisson(3.6, (3, 16))  # all variants ×3 from 08:00
    pooled = llr_cusum_pooled(y, lam, [3.0] * 3).alarms
    single = [cusum(y[v], lam[v], 3.0).alarms for v in range(3)]
    assert pooled and pooled[0][1] == "up" and 8 <= pooled[0][0] <= 10
    assert all(pooled[0][0] <= s[0][0] for s in single if s)


def test_scan_product_raises_on_variants_in_stock_and_merge_dedupes() -> None:
    t0 = datetime(2026, 11, 11, 0, tzinfo=UTC)
    hours = [t0 + timedelta(hours=h) for h in range(10)]
    lam = np.full((2, 10), 1.0)
    y = np.full((2, 10), 6.0)
    ins = np.ones((2, 10), dtype=bool)
    ins[1, :] = False  # B out of stock all day: its hours do not count, no alarm on B
    al = scan_product(["A", "B"], hours, y, lam, [3.0, 3.0], ins)
    assert al and {a.sku_id for a in al} == {"A"} and al[0].scope == "product"
    own = scan("A", hours, y[0], lam[0], 3.0)
    merged = merge_alarms(own, al)
    keys = [(a.sku_id, a.hour, a.direction) for a in merged]
    assert len(keys) == len(set(keys))
    assert merged[0].scope == "sku" or merged[0].hour < own[0].hour


# ---------------------------------------------------------------------- champions
def _r(model: str, bt: str, w: float | None, pb: float | None = 0.2) -> ScoreRow:
    return ScoreRow(model, bt, "on_dinh", "double_day", 10, 60, w, 0.0, 0.1, pb, 0.1, 0.8)


ORDER = {"b1": 0, "b2": 1, "b3": 2}


def test_champion_rule1_tie_uses_pinball() -> None:
    rows = [_r("a", "b1", 0.30, pb=0.30), _r("b", "b1", 0.31, pb=0.20)]
    champs, _ = select_champions(rows, ORDER)
    assert champs[("on_dinh", "double_day")] == "b"
    assert [r.model_id for r in rows if r.is_champion] == ["b"]


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reversed"])
@pytest.mark.parametrize(
    ("wape_a", "pinball_a", "wape_b", "pinball_b", "expected"),
    [
        (0.30, 0.20, 0.30, 0.0, "b"),
        (0.30, 0.20, 0.31, 0.0, "b"),
        (0.30, 0.0, 0.31, 0.20, "a"),
        (0.0, 0.20, 0.0, 0.0, "b"),
        (0.30, None, 0.30, 0.0, "b"),
        (0.30, None, 0.31, 0.0, "b"),
        (0.30, None, 0.30, 0.20, "b"),
        (0.30, 0.20, 0.40, 0.0, "a"),
        (None, 0.20, None, 0.0, "b"),
        (None, None, None, 0.0, "b"),
    ],
    ids=[
        "equal-wape-zero-pinball",
        "near-wape-zero-pinball-challenger",
        "near-wape-zero-pinball-leader",
        "zero-wape",
        "equal-wape-missing-versus-zero",
        "near-wape-missing-versus-zero",
        "missing-versus-positive",
        "outside-wape-tie",
        "sparse-zero-pinball",
        "sparse-missing-versus-zero",
    ],
)
def test_champion_preserves_zero_scores(
    wape_a: float | None,
    pinball_a: float | None,
    wape_b: float | None,
    pinball_b: float | None,
    expected: str,
    reverse: bool,
) -> None:
    rows = [_r("a", "b1", wape_a, pb=pinball_a), _r("b", "b1", wape_b, pb=pinball_b)]
    if reverse:
        rows.reverse()
    champions, changes = select_champions(rows, ORDER)
    assert champions[("on_dinh", "double_day")] == expected
    assert [row.model_id for row in rows if row.is_champion] == [expected]
    assert len(changes) == 1 and changes[0].new == expected


def test_challenger_needs_two_consecutive_wins() -> None:
    rows = [
        _r("a", "b1", 0.30),
        _r("b", "b1", 0.40),
        _r("a", "b2", 0.30),
        _r("b", "b2", 0.20),  # first win
        _r("a", "b3", 0.30),
        _r("b", "b3", 0.35),  # loses again → stays a
    ]
    champs, _ = select_champions(rows, ORDER)
    assert champs[("on_dinh", "double_day")] == "a"
    rows2 = [*rows[:4], _r("a", "b3", 0.30), _r("b", "b3", 0.25)]
    champs2, changes2 = select_champions(rows2, ORDER)
    assert champs2[("on_dinh", "double_day")] == "b"
    assert changes2[-1].backtest == "b3" and changes2[-1].old == "a"


def test_incumbent_from_previous_run_kept() -> None:
    rows = [_r("a", "b1", 0.30), _r("b", "b1", 0.25)]
    champs, _ = select_champions(rows, ORDER, {("on_dinh", "double_day"): "a"})
    assert champs[("on_dinh", "double_day")] == "a"


# ---------------------------------------------------------------------- PIT / floor
def _day_totals(lam: np.ndarray, k: float, rng: np.random.Generator, n: int) -> np.ndarray:
    """Day totals of 24 hourly NegBin(λ/24, k): ``[n, len(lam)]``."""
    g = rng.gamma(k, 1 / k, size=(n, len(lam), 24))
    return rng.poisson(lam[None, :, None] / 24 * g).sum(axis=2).astype(float)


def test_randomized_pit_is_80_percent_for_the_true_distribution() -> None:
    """Small counts: the old inclusive interval over-covers, the randomised PIT does not."""
    rng = np.random.default_rng(20261111)
    lam = rng.uniform(0.3, 4.0, size=4000)
    pred = _day_totals(lam, 3.0, rng, 1000)  # predictive = truth
    y = _day_totals(lam, 3.0, rng, 1)[0]
    u = randomized_pit(pred, y, rng)
    cov_pit = pit_coverage(u)
    q10, q90 = np.quantile(pred, [0.1, 0.9], axis=0)
    cov_old = coverage(y, q10, q90)
    assert cov_pit is not None and abs(cov_pit - 0.80) < 0.02
    assert cov_old is not None and cov_old > 0.86
    assert coverage_ok(cov_pit)


def test_wape_floor_matches_realised_wape_and_rejects_bad_forecasts() -> None:
    rng = np.random.default_rng(7)
    lam = rng.uniform(1.0, 15.0, size=3000)
    y = _day_totals(lam, 3.0, rng, 1)[0]

    def judge(lam_pred: np.ndarray) -> tuple[float, float, float]:
        pred = _day_totals(lam_pred, 3.0, rng, 800)
        p50 = np.median(pred, axis=0)
        fl = wape_floor(*floor_stats(pred, p50))
        cov = pit_coverage(randomized_pit(pred, y, rng))
        w = wape(y, p50)
        assert fl is not None and cov is not None and w is not None
        return w, fl, cov

    w, fl, cov = judge(lam)
    assert abs(w - fl) / fl < 0.05  # floor = realised WAPE of the correct model
    assert wape_ok(w, fl) and coverage_ok(cov)
    w2, fl2, cov2 = judge(lam * 1.5)  # level 50 % too high
    assert not (wape_ok(w2, fl2) and coverage_ok(cov2))
    assert not wape_ok(w2, fl2, WAPE_FLOOR_TOL)
    w3, fl3, cov3 = judge(np.full_like(lam, lam.mean()))  # ignores SKU differences
    assert not (wape_ok(w3, fl3) and coverage_ok(cov3))
