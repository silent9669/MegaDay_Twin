"""PNL-01 · orders → units per SKU × hour, prices, calendar. Scored against the answer
key of mock-data ``nho`` (tests only)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.panel import schema as ps
from app.panel.build import build_hourly_panel, load_panel
from app.warehouse.repo import table_columns
from tests.unit.panel.conftest import Built

LAST_FINAL_DAY = date(2026, 11, 8)  # after this, "Chờ lấy hàng" orders cancel later (02 §8)


def _daily(b: Built) -> pd.DataFrame:
    p = b.panel
    d = (
        p.groupby(["sku_id", "day_local"])
        .agg(
            units_gross=("units_gross", "sum"),
            units_cancelled=("units_cancelled", "sum"),
            revenue_net=("revenue_net", "sum"),
        )
        .reset_index()
    )
    gd = b.answer_daily()
    return gd.merge(d, left_on=["sku", "day_local"], right_on=["sku_id", "day_local"])


def _awaiting_pickup_units(b: Built) -> pd.DataFrame:
    df = b.conn.execute(
        """
        SELECT ol.sku_id, CAST(timezone('Asia/Ho_Chi_Minh', o.ordered_at) AS DATE) AS day_local,
               SUM(ol.qty) AS pickup
        FROM orders o JOIN order_lines ol USING (order_id)
        WHERE o.status = 'awaiting_pickup' GROUP BY 1, 2
        """
    ).df()
    df["day_local"] = pd.to_datetime(df["day_local"]).dt.date
    return df


def test_column_contract_matches_ddl(nho: Built) -> None:
    assert list(ps.COLUMN_NAMES) == table_columns(nho.conn, "hourly_panel")


def test_one_row_per_sku_hour(nho: Built) -> None:
    p = nho.panel
    n_skus = len(nho.answer("sku_that.csv"))
    hours = 120 * 24  # 14/07 → 10/11
    assert p["sku_id"].nunique() == n_skus
    assert len(p) == n_skus * hours
    assert not p.duplicated(["sku_id", "hour_utc"]).any()
    assert p["hour_utc"].min() == pd.Timestamp("2026-07-13 17:00", tz="UTC")
    assert p["hour_utc"].max() == pd.Timestamp("2026-11-10 16:00", tz="UTC")
    assert nho.result.rows_written == len(p)


def test_daily_units_match_answer(nho: Built) -> None:
    """units_gross (every order except cancelled, returns kept) vs ``ban_rong``:
    exact up to 08/11; the last two days may be higher because orders still
    "Chờ lấy hàng" get cancelled after the export: the excess must be fully explained
    by those orders (02 §8 allows 2% on demo; on the 30-SKU ``nho`` a handful of orders
    is ~4% of one day). Whole history ±1%."""
    m = _daily(nho)
    gd = nho.answer_daily()
    assert len(m) == (gd["day_local"] < date(2026, 11, 11)).sum()
    final = m[m["day_local"] <= LAST_FINAL_DAY]
    bad = final[final["units_gross"] != final["ban_rong"]]
    assert bad.empty, bad[["sku", "day_local", "units_gross", "ban_rong"]].head()
    tail = m[m["day_local"] > LAST_FINAL_DAY]
    pickup = _awaiting_pickup_units(nho)
    t = tail.merge(pickup, on=["sku_id", "day_local"], how="left").fillna({"pickup": 0})
    excess = t["units_gross"] - t["ban_rong"]
    assert (excess >= 0).all() and (excess <= t["pickup"]).all()
    total, truth = m["units_gross"].sum(), m["ban_rong"].sum()
    assert abs(total - truth) / truth <= 0.01


def test_gross_plus_cancelled_is_everything_sold(nho: Built) -> None:
    """``ban_duoc`` (sold incl. later-cancelled) = units_gross + units_cancelled, every day."""
    m = _daily(nho)
    assert (m["units_gross"] + m["units_cancelled"] == m["ban_duoc"]).all()


def test_revenue_net_excludes_returns(nho: Built) -> None:
    """``doanh_thu_rong`` keeps returns; revenue_net removes them (02 §6)."""
    m = _daily(nho)
    returned = nho.conn.execute(
        """
        SELECT ol.sku_id, CAST(timezone('Asia/Ho_Chi_Minh', o.ordered_at) AS DATE) AS day_local,
               SUM(ol.unit_price * ol.qty) AS ret
        FROM orders o JOIN order_lines ol USING (order_id)
        WHERE o.status = 'returned' GROUP BY 1, 2
        """
    ).df()
    returned["day_local"] = pd.to_datetime(returned["day_local"]).dt.date
    m = m.merge(returned, on=["sku_id", "day_local"], how="left").fillna({"ret": 0})
    final = m[m["day_local"] <= LAST_FINAL_DAY]
    assert (final["revenue_net"] + final["ret"] == final["doanh_thu_rong"]).all()


def test_provisional_only_in_last_48_hours(nho: Built) -> None:
    p = nho.panel
    prov = p[p["units_provisional"] > 0]
    assert not prov.empty
    assert prov["hour_utc"].min() >= pd.Timestamp("2026-11-08 17:00", tz="UTC")
    assert prov["data_flags"].map(lambda f: ps.FLAG_PROVISIONAL in f).all()


def test_effective_price_matches_answer_on_selling_days(nho: Built) -> None:
    gh = nho.answer_hourly()
    p = nho.panel
    sold_days = p.groupby(["sku_id", "day_local"])["units_gross"].transform("sum") > 0
    h = gh.merge(p[sold_days], left_on=["sku", "hour_utc"], right_on=["sku_id", "hour_utc"])
    assert len(h) > 1000
    assert (h["eff_price"].astype(int) == h["gia_hieu_luc"]).all()
    assert (h["discount_pct"].between(0, 1)).all()


def test_price_events_written(nho: Built) -> None:
    pe = nho.conn.execute(
        "SELECT source, count(*) FROM price_events GROUP BY 1 ORDER BY 1"
    ).fetchall()
    sources = dict(pe)
    assert sources["orders"] > 0 and sources["carried_forward"] > 0
    # list price comes from the orders (Giá gốc), equal to the catalogue price
    lp = nho.conn.execute(
        "SELECT count(*) FROM price_events pe JOIN skus s USING (sku_id) "
        "WHERE pe.list_price <> s.list_price"
    ).fetchall()[0][0]
    assert lp == 0


def test_calendar_flags(nho: Built) -> None:
    p = nho.panel
    day = p.drop_duplicates("day_local").set_index("day_local")
    assert day.loc[date(2026, 10, 10), "is_double_day"]
    assert day.loc[date(2026, 10, 9), "is_pre_double_day"]
    assert day.loc[date(2026, 10, 11), "is_post_double_day"]
    assert not day.loc[date(2026, 10, 12), "is_double_day"]
    assert day.loc[date(2026, 9, 25), "is_payday"]
    assert (
        day.loc[date(2026, 11, 10), "days_to_tet"] == (date(2027, 2, 6) - date(2026, 11, 10)).days
    )
    assert day.loc[date(2026, 10, 15), "days_to_gift_peak"] == 5
    assert day.loc[date(2026, 10, 25), "days_to_gift_peak"] == -5
    assert day.loc[date(2026, 11, 10), "days_to_gift_peak"] == 10
    assert p["hour_local"].between(0, 23).all()
    sunday = p[p["day_local"] == date(2026, 11, 8)]
    assert (sunday["dow_local"] == 0).all()
    assert not p["in_flash_window"].any()  # flash windows are on 11.11 only


def test_flash_window_on_live_day(nho_live: Built) -> None:
    p = nho_live.panel
    d = p[p["day_local"] == date(2026, 11, 11)].drop_duplicates("hour_local")
    hours = set(d.loc[d["in_flash_window"], "hour_local"])
    assert hours == {0, 1, 12, 13}
    # 11.11 price = committed price where the program has one
    noichien = p[(p["sku_id"] == "NOICHIEN2L") & (p["day_local"] == date(2026, 11, 11))]
    assert (noichien["eff_price"] == 531000).all()


def test_incremental_rebuild_is_idempotent(nho: Built) -> None:
    conn = nho.conn
    before = conn.execute("SELECT count(*), sum(units_gross) FROM hourly_panel").fetchone()
    start = pd.Timestamp("2026-11-01 00:00", tz="Asia/Ho_Chi_Minh").to_pydatetime()
    res = build_hourly_panel(conn, sku_ids=["NOICHIEN2L"], start=start)
    # closure: combo, its other component and campaign co-members are rebuilt together
    assert res.n_skus >= 3
    after = conn.execute("SELECT count(*), sum(units_gross) FROM hourly_panel").fetchone()
    assert before == after
    again = load_panel(conn, sku_ids=["NOICHIEN2L", "COMBO-NOICHIEN"], start=start)
    ref = nho.panel[
        nho.panel["sku_id"].isin(["NOICHIEN2L", "COMBO-NOICHIEN"])
        & (nho.panel["hour_utc"] >= pd.Timestamp(start))
    ].reset_index(drop=True)
    cols = [c for c in ps.COLUMN_NAMES if c != "panel_version"]
    pd.testing.assert_frame_equal(again[cols], ref[cols], check_dtype=False)


@pytest.mark.parametrize("col", ["units_gross", "stock_begin", "ad_spend_alloc"])
def test_validate_on_loaded_panel(nho: Built, col: str) -> None:
    assert col in ps.validate_panel(nho.panel).columns
