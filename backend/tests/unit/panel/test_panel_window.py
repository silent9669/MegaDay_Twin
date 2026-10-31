"""PNL-04 · window mode of ``compute_hourly_panel``: rows written from ``start`` on are
identical to a build over the whole history, while only a few days are computed."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta

import duckdb
import pandas as pd
import pytest

from app.core.timeutil import VN_TZ
from app.panel.build import build_hourly_panel, compute_hourly_panel
from tests.mockdata import load_mock, require
from tests.unit.panel.panel_compare import (
    frame_mismatches,
    full_frames,
    price_mismatches,
    stored_vs_full,
)

pytestmark = pytest.mark.integration

STARTS = [
    datetime(2026, 7, 20, 3, tzinfo=VN_TZ),  # < 7 days of history: no window possible
    datetime(2026, 9, 20, 0, tzinfo=VN_TZ),  # inside the ads gap day (ADS_GAP_DAY)
    datetime(2026, 9, 21, 5, tzinfo=VN_TZ),
    datetime(2026, 10, 15, 10, tzinfo=VN_TZ),  # receipt hour
    datetime(2026, 11, 10, 21, tzinfo=VN_TZ),  # last history hours (ads lag)
]


def _window_vs_full(
    conn: duckdb.DuckDBPyConnection, start: datetime, sku_ids: list[str] | None = None
) -> tuple[dict[str, int], int, int, int]:
    win = compute_hourly_panel(conn, sku_ids, start=start)
    full = compute_hourly_panel(conn, sku_ids, start=start, window=False)
    assert win is not None and full is not None
    return (
        frame_mismatches(win.panel, full.panel),
        price_mismatches(win.price_events, full.price_events),
        len(win.grid),
        len(full.grid),
    )


@pytest.mark.parametrize("start", STARTS, ids=lambda d: d.strftime("%m%d-%H"))
def test_window_equals_full_history(loaded_db: duckdb.DuckDBPyConnection, start: datetime) -> None:
    cells, prices, n_win, n_full = _window_vs_full(loaded_db, start)
    assert cells == {} and prices == 0
    if start.month >= 9:
        # Computed from about 7–8 days before ``start``, not from 14/07.
        skipped = n_full - n_win
        history_start = pd.Timestamp("2026-07-14", tz=VN_TZ)
        hours_before = int((pd.Timestamp(start) - history_start) / pd.Timedelta(hours=1))
        assert skipped >= hours_before - 9 * 24


def test_window_on_a_sku_subset(loaded_db: duckdb.DuckDBPyConnection) -> None:
    sku = str(loaded_db.execute("SELECT min(sku_id) FROM skus").fetchall()[0][0])
    cells, prices, _, _ = _window_vs_full(loaded_db, datetime(2026, 11, 3, 8, tzinfo=VN_TZ), [sku])
    assert cells == {} and prices == 0


@pytest.fixture(scope="module")
def edited() -> Iterator[duckdb.DuckDBPyConnection]:
    """``nho`` with state that crosses the window start: a committed price before the
    window, an ads hole of 30 hours over it and a receipt the day it opens."""
    require("nho")
    conn = duckdb.connect()
    load_mock(conn, "nho", build_panel=False)
    sku = str(
        conn.execute(
            "SELECT min(cs.sku_id) FROM campaign_skus cs JOIN skus s USING (sku_id) "
            "WHERE NOT s.is_bundle"
        ).fetchall()[0][0]
    )
    cid = str(
        conn.execute(
            "SELECT min(campaign_id) FROM campaign_skus WHERE sku_id = ?", [sku]
        ).fetchall()[0][0]
    )
    conn.execute(
        "INSERT INTO sale_commitments (commitment_id, program, day_local, kind, sku_id, "
        "value_int) VALUES ('t-com', 'test', DATE '2026-10-25', 'committed_price', ?, 12345)",
        [sku],
    )
    hole = datetime(2026, 10, 27, 10, tzinfo=VN_TZ)
    conn.execute(
        "DELETE FROM ad_hourly WHERE campaign_id = ? AND hour_utc >= ? AND hour_utc < ?",
        [cid, hole, hole + timedelta(hours=30)],
    )
    conn.execute(
        "INSERT OR REPLACE INTO restock_receipts (sku_id, received_on, qty) VALUES (?, ?, 40)",
        [sku, date(2026, 10, 28)],
    )
    yield conn
    conn.close()


@pytest.mark.parametrize(
    "start",
    [
        datetime(2026, 11, 4, 0, tzinfo=VN_TZ),  # window opens inside the ads hole
        datetime(2026, 11, 4, 20, tzinfo=VN_TZ),
        datetime(2026, 11, 8, 6, tzinfo=VN_TZ),
    ],
    ids=lambda d: d.strftime("%m%d-%H"),
)
def test_state_crossing_the_window_start(
    edited: duckdb.DuckDBPyConnection, start: datetime
) -> None:
    cells, prices, _, _ = _window_vs_full(edited, start)
    assert cells == {} and prices == 0


def test_partial_rebuild_written_equals_full(edited: duckdb.DuckDBPyConnection) -> None:
    build_hourly_panel(edited)
    build_hourly_panel(edited, start=datetime(2026, 11, 6, 13, tzinfo=VN_TZ))
    cells, prices = stored_vs_full(edited)
    assert cells == {} and prices == 0


def test_edited_state_really_crosses_the_window(edited: duckdb.DuckDBPyConnection) -> None:
    win = compute_hourly_panel(edited, start=datetime(2026, 11, 4, 0, tzinfo=VN_TZ))
    assert win is not None
    g0 = win.grid[0]
    assert pd.Timestamp(datetime(2026, 10, 27, 10, tzinfo=VN_TZ)) < g0  # hole began before
    assert g0 < pd.Timestamp(datetime(2026, 10, 28, 16, tzinfo=VN_TZ))  # and ends inside
    full = full_frames(edited).panel
    hole = full[(full["hour_utc"] >= g0) & (full["hour_utc"] < g0 + pd.Timedelta(hours=3))]
    assert hole["data_flags"].map(lambda f: "ADS_GAP_DAY" in f).any()
