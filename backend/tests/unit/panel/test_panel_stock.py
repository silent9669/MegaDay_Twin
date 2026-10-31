"""PNL-02 · hourly stock, stockout flags, combo. Synthetic unit cases + scoring on
mock-data ``nho`` against its answer key (tests only)."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from app.panel import schema as ps
from app.panel.stock import (
    SOURCE_INTERPOLATED,
    SOURCE_RECONSTRUCTED,
    SOURCE_SNAPSHOT_HOUR,
    SkuStockInput,
    combo_stock,
    reconstruct_sku,
    stockout_flags,
)
from tests.mockdata import answer_dir
from tests.unit.panel.conftest import Built

COMBO, COMPONENTS = "COMBO-NOICHIEN", ("NOICHIEN2L", "GIAYLOT50")


def _arr(*vals: int, n: int = 24) -> np.ndarray:
    a = np.zeros(n, dtype=np.int64)
    a[: len(vals)] = vals
    return a


# ------------------------------------------------------------------ synthetic
def test_walk_between_two_daily_snapshots_is_exact() -> None:
    cons = _arr(0, 2, 0, 3)
    st = reconstruct_sku(SkuStockInput(consumed=cons, need=cons, anchors={0: 10, 24: 5}))
    assert st.begin[:5].tolist() == [10, 10, 8, 8, 5]
    assert st.end[23] == 5
    assert set(st.source) == {SOURCE_RECONSTRUCTED}
    assert not st.jump.any()


def test_small_error_is_spread_and_big_error_is_a_jump() -> None:
    cons = _arr(1, 1, 1, 1)
    small = reconstruct_sku(SkuStockInput(consumed=cons, need=cons, anchors={0: 100, 24: 94}))
    assert small.end[23] == 94 and not small.jump.any()
    assert small.end[3] == 94  # −2 spread over the 4 selling hours
    big = reconstruct_sku(SkuStockInput(consumed=cons, need=cons, anchors={0: 100, 24: 50}))
    assert big.end[23] == 50 and big.jump.all()
    assert big.begin[4] == 96  # the walk is kept, the snapshot wins at the end


def test_receipt_default_10h_and_moved_by_sales() -> None:
    cons = _arr(n=24)
    cons[15] = 2
    # shelf has stock: receipt assumed at 10:00
    st = reconstruct_sku(
        SkuStockInput(consumed=cons, need=cons, anchors={0: 5, 24: 53}, receipts=[(0, 50)])
    )
    assert st.restock[10] == 50 and st.begin[9] == 5 and st.begin[10] == 55
    # shelf empty: arrival = first hour that needed stock
    st = reconstruct_sku(
        SkuStockInput(consumed=cons, need=cons, anchors={0: 0, 24: 48}, receipts=[(0, 50)])
    )
    assert st.restock[15] == 50
    assert stockout_flags(st.begin, cons)[10:15].all()
    # sale at 07:00 with an empty shelf proves an earlier arrival
    cons2 = _arr(0, 0, 0, 0, 0, 0, 0, 1)
    st = reconstruct_sku(
        SkuStockInput(consumed=cons2, need=cons2, anchors={0: 0, 24: 49}, receipts=[(0, 50)])
    )
    assert st.restock[7] == 50 and not stockout_flags(st.begin, cons2)[7]


def test_invalid_snapshot_is_interpolated_and_first_day_backfilled() -> None:
    cons = np.ones(72, dtype=np.int64)
    st = reconstruct_sku(
        SkuStockInput(consumed=cons, need=cons, anchors={24: 100, 48: None, 72: 52})
    )
    assert st.begin[0] == 124  # backed out from the first snapshot
    assert st.end[47] == 76 and st.end[71] == 52
    assert set(st.source[24:72]) == {SOURCE_INTERPOLATED}
    assert st.interpolated[24:72].all() and not st.interpolated[:24].any()


def test_hourly_snapshots_are_used_directly() -> None:
    cons = _arr(3, 0, 1, n=3)
    st = reconstruct_sku(
        SkuStockInput(consumed=cons, need=cons, anchors={0: 10, 1: 7, 2: 30, 3: 29}, receipts=[])
    )
    assert st.begin.tolist() == [10, 7, 30]
    assert st.end.tolist() == [7, 30, 29]
    assert set(st.source) == {SOURCE_SNAPSHOT_HOUR}


def test_stockout_rule() -> None:
    begin = np.array([0.0, 3.0, 3.0, 5.0, np.nan])
    taken = np.array([0, 3, 2, 0, 9])
    assert stockout_flags(begin, taken).tolist() == [True, True, False, False, False]


def test_combo_is_min_of_components_after_their_own_sales() -> None:
    a = reconstruct_sku(
        SkuStockInput(consumed=_arr(2, n=2), need=_arr(2, n=2), anchors={0: 5, 2: 3})
    )
    b = reconstruct_sku(
        SkuStockInput(consumed=_arr(0, n=2), need=_arr(0, n=2), anchors={0: 9, 2: 9})
    )
    cb = combo_stock([(a, _arr(2, n=2), 1), (b, _arr(0, n=2), 2)])
    assert cb.begin.tolist() == [4, 3]  # min(5, 9//2), min(3, 9//2)
    assert cb.available.tolist() == [3, 3]  # component A sold 2 first


# ------------------------------------------------------------------ mock-data
def _end_of_day(b: Built) -> pd.DataFrame:
    p = b.panel
    last = p[p["hour_local"] == 23][["sku_id", "day_local", "stock_end", "stockout"]]
    gd = b.answer_daily()
    return gd.merge(last, left_on=["sku", "day_local"], right_on=["sku_id", "day_local"])


def test_end_of_day_stock_matches_answer(nho: Built) -> None:
    m = _end_of_day(nho)
    m = m[m["ton_cuoi_ngay"].notna()]
    manifest = json.loads((answer_dir("nho") / "manifest.json").read_text(encoding="utf-8"))
    neg = next(x for x in manifest["loi_cai_san"] if x["loai"] == "ton_am")["chi_tiet"]
    assert len(neg) == 3
    bad = m[m["stock_end"] != m["ton_cuoi_ngay"]]
    assert len(bad) <= len(neg), bad[["sku", "day_local", "stock_end", "ton_cuoi_ngay"]]
    rel = (bad["stock_end"] - bad["ton_cuoi_ngay"]).abs() / bad["ton_cuoi_ngay"].clip(lower=1)
    assert (rel <= 0.05).all()


def test_negative_snapshot_hours_marked_interpolated(nho: Built) -> None:
    neg_days = nho.conn.execute(
        "SELECT sku_id, snapshot_at FROM inventory_snapshots WHERE quality_flag = 'negative'"
    ).df()
    assert len(neg_days) == 3
    p = nho.panel
    for sku, at in zip(neg_days["sku_id"], neg_days["snapshot_at"], strict=True):
        just_before = p[
            (p["sku_id"] == sku) & (p["hour_utc"] == pd.Timestamp(at) - pd.Timedelta("1h"))
        ]
        assert just_before["stock_source"].iat[0] == SOURCE_INTERPOLATED
        assert just_before["stockout_conf"].iat[0] == 0.5
        assert ps.FLAG_STOCK_INTERPOLATED in just_before["data_flags"].iat[0]


def _hourly(b: Built) -> pd.DataFrame:
    gh = b.answer_hourly()
    return gh.merge(b.panel, left_on=["sku", "hour_utc"], right_on=["sku_id", "hour_utc"])


def test_stockout_hours_match_answer(nho: Built) -> None:
    """The answer key flags an hour ``het_hang`` only when demand > stock, which the
    app cannot observe; the panel flags every hour where demand *may* be censored
    (stock_begin ≤ 0, or the hour took all that was left — 02 §6.1), including
    empty-shelf hours where nobody came. Comparable definition built from the key:
    ``het_hang`` or (shelf empty after the hour and every demand served).
    Counts must agree ±1%; every true censored hour must be flagged."""
    h = _hourly(nho)
    assert len(h) > 20_000
    observable = (h["het_hang"] == 1) | (
        (h["ban_duoc"] == h["nhu_cau_that"]) & (h["ton_cuoi_gio"] <= 0)
    )
    ours = h["stockout"]
    assert abs(int(ours.sum()) - int(observable.sum())) <= 0.01 * observable.sum()
    assert (ours != observable).sum() <= 0.01 * observable.sum()
    # recall on the key's own definition
    truth = h["het_hang"] == 1
    assert (ours & truth).sum() >= 0.99 * truth.sum()
    # extra flags are (almost all) empty shelf with zero demand
    extra = ours & ~truth
    explained = extra & (h["nhu_cau_that"] == 0) & (h["stock_begin"] <= 0)
    assert (extra & ~explained).sum() <= 0.01 * truth.sum()


def test_daily_stockout_hours_never_below_answer(nho: Built) -> None:
    p = nho.panel
    d = p.groupby(["sku_id", "day_local"])["stockout"].sum().rename("ours").reset_index()
    gd = nho.answer_daily().merge(d, left_on=["sku", "day_local"], right_on=["sku_id", "day_local"])
    short = gd[gd["ours"] < gd["so_gio_het_hang"]]
    assert len(short) <= 0.01 * (gd["so_gio_het_hang"] > 0).sum()


def test_hourly_stock_close_to_answer(nho: Built) -> None:
    """``ton_cuoi_gio`` of a component is logged before the combo of the same hour
    takes its share; add it back. Remaining differences come from the unknown
    receipt hour and from orders cancelled after the export."""
    h = _hourly(nho)
    combo = h[h["sku"] == COMBO][["hour_utc", "units_gross"]].rename(
        columns={"units_gross": "combo_units"}
    )
    h = h.merge(combo, on="hour_utc", how="left")
    truth = np.where(
        h["sku"].isin(COMPONENTS), h["ton_cuoi_gio"] - h["combo_units"], h["ton_cuoi_gio"]
    )
    mismatch = (h["stock_end"].astype(float) != truth).sum()
    assert mismatch <= 0.005 * len(h)


def test_combo_stock_and_components(nho: Built) -> None:
    p = nho.panel.set_index(["sku_id", "hour_utc"])
    combo = p.loc[COMBO]
    comps = [p.loc[c] for c in COMPONENTS]
    expected = np.minimum(comps[0]["stock_begin"], comps[1]["stock_begin"])
    assert (combo["stock_begin"] == expected).all()
    assert combo["stock_source"].notna().all()
    assert ps.FLAG_BUNDLE_NO_STOCK_ROW not in combo["data_flags"].iat[0]
    # components lose stock for combo orders too
    comp = comps[0]
    no_receipt = comp["restock_arrived"] == 0
    drop = comp["stock_begin"] - comp["stock_end"]
    sold = comp["units_gross"] + combo["units_gross"]
    same = (drop == sold) | ~no_receipt
    assert same.mean() > 0.99
    # combo stockout vs the key: every censored combo hour is flagged
    h = _hourly(nho)
    hc = h[h["sku"] == COMBO]
    assert hc["het_hang"].sum() > 0
    assert (hc["stockout"] | (hc["het_hang"] == 0)).all()


def test_live_day_uses_hourly_snapshots(nho_live: Built) -> None:
    p = nho_live.panel
    live = p[p["day_local"] == date(2026, 11, 11)]
    assert len(live) == 30 * 24
    stocked = live[live["sku_id"] != COMBO]
    assert set(stocked["stock_source"]) == {SOURCE_SNAPSHOT_HOUR}
    assert (stocked["stockout_conf"] == 1.0).all()
    snaps = nho_live.conn.execute(
        "SELECT sku_id, snapshot_at, on_hand FROM inventory_snapshots WHERE granularity = 'hour'"
    ).df()
    snaps["hour_utc"] = pd.DatetimeIndex(snaps["snapshot_at"]).tz_convert("UTC") - pd.Timedelta(
        "1h"
    )
    m = snaps.merge(stocked, on=["sku_id", "hour_utc"])
    assert len(m) == len(stocked)
    assert (m["stock_end"] == m["on_hand"]).all()
    # AOGIUNHIET-M: 80 shirts removed at 10:00 then sold out while ads run (persona)
    ao = live[live["sku_id"] == "AOGIUNHIET-M"]
    assert ao["stockout"].any()
    assert ao.loc[ao["stockout"], "hour_local"].min() >= 10
