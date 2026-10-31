"""Exclusion rules and hour rhythm (ADS-01, ADS-02; plan 04 §4, §5.1)."""

from __future__ import annotations

import numpy as np

from app.ads_effect import dataset as ds
from tests.unit.ads_effect.synth import B_TRUE, make_panel


def _reasons(status: list[str], low: list[int] | None = None, launch_n: int = 0) -> list[str]:
    n = len(status)
    main_low = np.zeros(n, bool) if low is None else np.array(low, bool)
    launch = np.zeros(n, bool)
    launch[:launch_n] = True
    out = ds.exclusion_reasons(
        status=np.array(status), main_low=main_low, launch=launch, apply_stock_rules=True
    )
    return out.tolist()


def test_unknown_hours_and_their_carry_tail() -> None:
    st = ["running"] * 3 + ["unknown"] * 2 + ["running"] * 10
    r = _reasons(st)
    assert r[:3] == ["", "", ""]
    assert r[3:11] == ["unknown"] * 8  # 2 unknown + 6 h whose adstock is unknown
    assert r[11:] == [""] * 4


def test_stock_pause_run_and_tail() -> None:
    #       0..9 running, low stock at 8, pause 10..14, running after
    st = ["running"] * 10 + ["paused"] * 5 + ["running"] * 12
    low = [0] * 8 + [1] + [0] * 18
    r = _reasons(st, low)
    assert r[8] == "low_stock"
    assert r[10:21] == ["stock_pause"] * 11  # the pause + 6 h after
    assert r[21:] == [""] * 6
    assert r[:8] == [""] * 8


def test_pause_without_low_stock_is_kept() -> None:
    st = ["running"] * 10 + ["paused"] * 5 + ["running"] * 5
    low = [0] * 20
    assert _reasons(st, low) == [""] * 20


def test_low_stock_lookback_is_six_hours() -> None:
    st = ["running"] * 10 + ["paused"] * 3 + ["running"] * 2
    low = [0] * 3 + [1] + [0] * 11  # low at hour 3, the pause starts at 10 (7 h later)
    r = _reasons(st, low)
    assert r[10:13] == ["", "", ""]


def test_launch_priority_and_counts() -> None:
    st = ["unknown"] + ["running"] * 5
    r = _reasons(st, launch_n=4)
    assert r[0] == "unknown" and r[1:4] == ["unknown"] * 3  # unknown wins over launch
    assert r[4:6] == ["unknown", "unknown"]


def test_rules_can_be_switched_off() -> None:
    st = np.array(["running"] * 5 + ["paused"] * 5 + ["running"] * 5)
    low = np.ones(15, bool)
    r = ds.exclusion_reasons(
        status=st, main_low=low, launch=np.zeros(15, bool), apply_stock_rules=False
    )
    assert set(r.tolist()) == {""}


def test_build_campaign_hours_counts_reasons() -> None:
    syn = make_panel(1, days=30, stock_trap=True)
    ch = ds.build_campaign_hours(syn.frame, "C1", syn.started_at)
    assert ch is not None
    ex = ch.excluded_hours()
    assert set(ex) == set(ds.EXCLUSION_REASONS)
    assert ex["launch"] == 72  # first 3 days of the campaign
    assert ex["low_stock"] > 0 and ex["stock_pause"] > 0
    assert ex["unknown"] == 0
    assert int(ch.keep.sum()) + sum(ex.values()) == ch.y.size
    # no kept hour is a stockout hour of the (single, main) SKU
    out = syn.frame["stockout"].to_numpy()
    assert not np.any(ch.keep & out)
    assert ch.sku_shares == {"SKU-A": 1.0}
    assert ch.n_clean_off_hours > 0


def test_main_sku_threshold() -> None:
    a = make_panel(2, days=10, sku="BIG", base_per_day=100).frame
    b = make_panel(3, days=10, sku="SMALL", base_per_day=10, stock_trap=True).frame
    b["ad_status"] = a["ad_status"].to_numpy()
    b["ad_spend_alloc"] = 0.0
    frame = __import__("pandas").concat([a, b], ignore_index=True)
    ch = ds.build_campaign_hours(frame, "C1", None)
    assert ch is not None
    # SMALL has < 30 % of units: its stockouts do not exclude campaign hours
    assert ch.excluded_hours()["low_stock"] == 0


def test_rhythm_pools_weekdays_when_homogeneous() -> None:
    rng = np.random.default_rng(0)
    counts = np.zeros((ds.N_CTX, 24))
    for c in range(7):
        counts[c] = rng.poisson(B_TRUE * 400)
    counts[ds.CTX_DOUBLE_DAY] = rng.poisson(B_TRUE[::-1] * 400)
    table, p, pooled = ds.rhythm_from_counts(counts)
    assert pooled and p is not None and p > ds.WEEKDAY_TEST_P
    np.testing.assert_allclose(table[:7].mean(axis=1), 1.0)
    assert np.allclose(table[0], table[6])
    assert np.max(np.abs(table[0] / B_TRUE - 1)) < 0.15
    assert not np.allclose(table[ds.CTX_DOUBLE_DAY], table[0])  # own shape kept


def test_rhythm_keeps_weekday_shapes_when_they_differ() -> None:
    counts = np.tile(B_TRUE * 2000, (ds.N_CTX, 1))
    counts[5] = B_TRUE[::-1] * 2000
    _, p, pooled = ds.rhythm_from_counts(counts)
    assert not pooled and p is not None and p < 1e-6
