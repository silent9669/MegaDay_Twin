"""M1 gate on ``mock-data/out/demo`` (12 · M1): 6 history files in < 60 s, SKU
matching ≥ 90 % automatic with ≤ 1 % wrong, ``hourly_panel`` 300 SKU × 2.880 h,
no PII; live hours of 11.11 keep the panel equal to a whole-history rebuild (PNL-04).
The full scoring report is ``python -m eval.eval_data --size demo``."""

from __future__ import annotations

import time
from pathlib import Path

import duckdb
import pytest

from app.ingest.pipeline import ingest_live_delta
from app.warehouse import repo
from eval.eval_data import scan_pii, score_matching
from eval.live_replay import write_live_stream
from tests.mockdata import load_mock, require
from tests.unit.panel.panel_compare import stored_vs_full

pytestmark = pytest.mark.e2e


def test_demo_ingest_meets_m1(tmp_path: Path) -> None:
    require("demo")
    conn = duckdb.connect(str(tmp_path / "demo.duckdb"))
    t0 = time.perf_counter()
    report, _ = load_mock(conn, "demo", build_panel=True)
    seconds = time.perf_counter() - t0
    assert all(f.status == "loaded" for f in report.files)
    assert seconds < 60, seconds
    shape = conn.execute(
        "SELECT count(*), count(DISTINCT sku_id), count(DISTINCT hour_utc) FROM hourly_panel"
    ).fetchone()
    assert shape == (300 * 2880, 300, 2880)
    hard = score_matching(conn, "demo")["inventory+cost"]
    assert hard["auto_rate"] >= 0.90 and hard["wrong_rate"] <= 0.01
    assert scan_pii(conn)["hits"] == 0
    assert repo.count_rows(conn, "orders") > 100_000

    # PNL-04: a few live hours (window mode) == whole-history rebuild, cell by cell.
    out = tmp_path / "live_stream"
    for hour in (0, 1, 2, 5):
        write_live_stream("demo", out, hour)
        assert ingest_live_delta(conn, out).status == "loaded"
        assert stored_vs_full(conn) == ({}, 0), hour
    conn.close()


@pytest.mark.perf
def test_demo_live_hour_under_5_seconds(tmp_path: Path) -> None:
    """12 · PNL-04: one live hour < 5 s (measured on every hour of 11.11)."""
    require("demo")
    conn = duckdb.connect(str(tmp_path / "demo.duckdb"))
    load_mock(conn, "demo", build_panel=True)
    out = tmp_path / "live_stream"
    seconds = []
    for hour in range(24):
        write_live_stream("demo", out, hour)
        t0 = time.perf_counter()
        ingest_live_delta(conn, out)
        seconds.append(time.perf_counter() - t0)
    conn.close()
    assert max(seconds) < 5, seconds
