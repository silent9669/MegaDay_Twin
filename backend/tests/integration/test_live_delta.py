"""PNL-04 · ``ingest_live_delta`` on a simulated ``live_stream/`` (nho)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from app.core.events import DataUpdated, LiveTick
from app.ingest.pipeline import ingest_live_delta, read_live_status
from app.warehouse import repo
from eval.live_replay import write_live_stream
from tests.mockdata import load_mock
from tests.unit.panel.panel_compare import stored_vs_full

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[duckdb.DuckDBPyConnection, Path]]:
    root = tmp_path_factory.mktemp("live")
    conn = duckdb.connect(str(root / "live.duckdb"))
    load_mock(conn, "nho", build_panel=True)
    yield conn, root / "live_stream"
    conn.close()


def _hours(conn: duckdb.DuckDBPyConnection) -> int:
    row = conn.execute("SELECT count(DISTINCT hour_utc) FROM hourly_panel").fetchone()
    return int(row[0]) if row else 0


def test_hour_by_hour(live: tuple[duckdb.DuckDBPyConnection, Path]) -> None:
    conn, out = live
    write_live_stream("nho", out, 0)
    status = read_live_status(out)
    assert status is not None and status.ads_until is None  # "chưa có"
    r0 = ingest_live_delta(conn, out)
    assert r0.status == "loaded" and r0.new_rows["bao_cao_ads.csv"] == 0
    assert r0.ingest is not None
    types = [type(e) for e in r0.ingest.events]
    assert types == [DataUpdated, LiveTick]
    assert _hours(conn) == 2880 + 1
    n_orders = repo.count_rows(conn, "orders")

    assert ingest_live_delta(conn, out).status == "unchanged"

    write_live_stream("nho", out, 2)
    r2 = ingest_live_delta(conn, out)
    assert r2.status == "loaded"
    assert r2.new_rows["bao_cao_ads.csv"] > 0  # ads up to 00:59 (2 h lag)
    assert r2.tick is not None and r2.tick.ads_until is not None
    assert repo.count_rows(conn, "orders") > n_orders
    assert _hours(conn) == 2880 + 3
    snaps = conn.execute(
        "SELECT count(*) FROM inventory_snapshots WHERE granularity = 'hour'"
    ).fetchone()
    assert snaps is not None and snaps[0] == 29 * 3
    as_of = conn.execute(
        "SELECT max(as_of) FROM ingest_batches WHERE mode = 'live' AND source_type = 'orders'"
    ).fetchone()
    assert as_of is not None and as_of[0].isoformat() == "2026-11-10T20:00:00+00:00"  # 03:00 VN
    cursor = repo.fetch_all(conn, "SELECT file, last_row_count FROM live_cursor")
    assert len(cursor) == 4  # 3 CSV + trang_thai.json

    # Replay restarted from hour 0: live data removed and reloaded from row 0.
    write_live_stream("nho", out, 0)
    r = ingest_live_delta(conn, out)
    assert r.restarted and r.status == "loaded"
    assert repo.count_rows(conn, "orders") == n_orders
    assert _hours(conn) == 2880 + 1


def test_full_day_folder_without_status_file(tmp_path: Path) -> None:
    conn = duckdb.connect()
    _, delta = load_mock(conn, "nho", live=True, build_panel=False)
    assert delta is not None and delta.status == "loaded"
    assert delta.tick is not None and delta.tick.orders_inventory_until is not None
    assert ingest_live_delta(conn, Path(delta.live_dir)).status == "empty"
    conn.close()


def test_hour_by_hour_panel_equals_full_rebuild(tmp_path: Path) -> None:
    """PNL-04: after each live hour of 11.11 (window mode, touched SKUs only) the
    stored panel and prices equal a whole-history rebuild, cell by cell."""
    conn = duckdb.connect(str(tmp_path / "w.duckdb"))
    load_mock(conn, "nho", build_panel=True)
    out = tmp_path / "live_stream"
    for hour in range(24):
        write_live_stream("nho", out, hour)
        assert ingest_live_delta(conn, out).status == "loaded"
        cells, prices = stored_vs_full(conn)
        assert cells == {} and prices == 0, (hour, cells, prices)
    # Several hours in one delta (watcher was late) and a replay restart.
    write_live_stream("nho", out, 3)
    assert ingest_live_delta(conn, out).restarted
    write_live_stream("nho", out, 9)
    ingest_live_delta(conn, out)
    assert stored_vs_full(conn) == ({}, 0)
    conn.close()
