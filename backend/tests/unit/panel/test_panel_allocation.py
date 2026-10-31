"""PNL-03 · campaign spend split over SKUs: Σ SKU spend = campaign spend every hour."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.panel import schema as ps
from app.panel.allocation import FLAG_GAP_DAY, FLAG_LAG, allocate_ad_spend, trailing_sum
from tests.unit.panel.conftest import Built


def test_trailing_sum_excludes_current_hour() -> None:
    u = np.array([[1, 2, 3, 4]], dtype=np.int64)
    assert trailing_sum(u, window=2).tolist() == [[0, 1, 3, 5]]


def test_allocation_synthetic() -> None:
    grid = pd.date_range("2026-11-01", periods=4, freq="h", tz="UTC")
    units = np.array([[3, 0, 0, 0], [0, 0, 0, 0]], dtype=np.int64)
    stockout = np.array([[False, False, True, True], [False, False, False, True]])
    ads = pd.DataFrame(
        {
            "campaign_id": ["C1", "C1", "C1"],
            "hour_utc": grid[:3],
            "status": ["running", "running", "paused"],
            "spend": [100, 50, 0],
            "attributed_orders": [2, 0, 0],
        }
    )
    a = allocate_ad_spend(
        grid, ["A", "B"], units, stockout, {"C1": ["A", "B"]}, ads, {"C1": grid[0]}
    )
    # hour 0: no history → equal; hour 1: A sold 3 → (3+1)/(4+1)
    assert np.allclose(a.share[:, 0], [0.5, 0.5])
    assert np.allclose(a.share[:, 1], [0.8, 0.2])
    # hour 2: A stocked out → all to B; hour 3: both out → equal
    assert np.allclose(a.share[:, 2], [0.0, 1.0])
    assert np.allclose(a.share[:, 3], [0.5, 0.5])
    assert np.allclose(a.spend[:, 0].sum(), 100) and np.allclose(a.spend[:, 1].sum(), 50)
    assert np.allclose(a.attributed[:, 0], [1, 1])
    # hour 3 has no row after the last report hour → unknown, NULL, ADS_LAG
    assert a.status[0, 3] == "unknown" and np.isnan(a.spend[:, 3]).all()
    assert (a.flag[:, 3] == FLAG_LAG).all()
    assert a.status[0, 2] == "paused"


def test_sku_spend_sums_to_campaign_spend(nho: Built) -> None:
    p = nho.panel
    ads = nho.conn.execute(
        "SELECT campaign_id, hour_utc, spend, attributed_orders FROM ad_hourly"
    ).df()
    ads["hour_utc"] = pd.DatetimeIndex(ads["hour_utc"]).tz_convert("UTC")
    sku_side = (
        p[p["campaign_id"].notna()]
        .groupby(["campaign_id", "hour_utc"])
        .agg(
            spend=("ad_spend_alloc", "sum"),
            share=("ad_alloc_share", "sum"),
            attr=("attributed_orders_alloc", "sum"),
        )
        .reset_index()
    )
    m = ads.merge(sku_side, on=["campaign_id", "hour_utc"], suffixes=("_camp", "_sku"))
    assert len(m) == len(ads)  # every report hour lands in the panel
    assert np.allclose(m["spend_sku"], m["spend_camp"], rtol=0, atol=1e-6)
    assert np.allclose(m["attr"], m["attributed_orders"], atol=1e-9)
    assert np.allclose(m["share"], 1.0)
    total_panel = p["ad_spend_alloc"].sum()
    assert abs(total_panel - ads["spend"].sum()) < 1e-3


def test_unknown_hours_gap_day_and_lag(nho: Built) -> None:
    p = nho.panel
    in_camp = p[p["campaign_id"].notna() & (p["ad_status"] != "none")]  # none = not started
    gap = in_camp[in_camp["day_local"] == date(2026, 9, 20)]
    assert gap["campaign_id"].nunique() >= 5
    assert (gap["ad_status"] == "unknown").all()
    assert gap["ad_spend_alloc"].isna().all()
    assert gap["data_flags"].map(lambda f: ps.FLAG_ADS_GAP_DAY in f).all()
    lag = in_camp[(in_camp["day_local"] == date(2026, 11, 10)) & (in_camp["hour_local"] >= 21)]
    assert len(lag) > 0 and (lag["ad_status"] == "unknown").all()
    assert lag["data_flags"].map(lambda f: ps.FLAG_ADS_LAG in f).all()
    before_lag = in_camp[
        (in_camp["day_local"] == date(2026, 11, 10)) & (in_camp["hour_local"] == 20)
    ]
    assert (before_lag["ad_status"] != "unknown").all()
    assert FLAG_GAP_DAY != FLAG_LAG


def test_no_campaign_means_no_spend(nho: Built) -> None:
    p = nho.panel
    free = p[p["campaign_id"].isna()]
    assert len(free) > 0
    assert (free["ad_status"] == "none").all()
    assert (free["ad_spend_alloc"] == 0).all() and free["ad_alloc_share"].isna().all()


def test_stocked_out_sku_gets_no_spend_unless_all_out(nho: Built) -> None:
    p = nho.panel
    c = p[p["campaign_id"].notna()].copy()
    grp = c.groupby(["campaign_id", "hour_utc"])["stockout"]
    c["n_out"], c["n"] = grp.transform("sum"), grp.transform("size")
    partial = c[c["stockout"] & (c["n_out"] < c["n"])]
    full = c[c["n_out"] == c["n"]]
    assert len(partial) > 0
    assert (partial["ad_alloc_share"] == 0).all()
    assert np.allclose(full["ad_alloc_share"], 1 / full["n"])


def test_allocation_close_to_true_split(nho: Built) -> None:
    """The simulator splits spend by demand; our proxy (7-day sales) should land each
    SKU's history total within 35% of the true one for SKUs with real spend."""
    p = nho.panel
    gd = nho.answer_daily()
    known_days = set(p.loc[p["ad_status"] != "unknown", "day_local"])
    gd = gd[gd["day_local"].isin(known_days) & (gd["day_local"] < date(2026, 11, 10))]
    truth = gd.groupby("sku")["chi_ads"].sum()
    ours = p[p["day_local"].isin(set(gd["day_local"]))].groupby("sku_id")["ad_spend_alloc"].sum()
    both = pd.concat([truth.rename("truth"), ours.rename("ours")], axis=1).dropna()
    big = both[both["truth"] > 0.05 * both["truth"].sum() / len(both)]
    rel = (big["ours"] - big["truth"]).abs() / big["truth"]
    assert rel.median() < 0.35, both
