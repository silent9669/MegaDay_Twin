"""Pipeline on ``mock-data/out/nho`` (ING-07, EVAL-01): row counts, idempotence,
no PII, quality issues, events. Answer key read here only (tests may)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from app.core.events import DataUpdated
from app.ingest.pipeline import IngestReport, ingest_files
from app.ingest.quality import DERIVED_CODES
from app.warehouse import repo
from tests.mockdata import answer_dir, load_mock, mock_dir

pytestmark = pytest.mark.integration

PII_TEXT = ("Khách Test", "Đường Mẫu", "Phường Thử Nghiệm")


def _raw_orders(size: str = "nho") -> pd.DataFrame:
    return pd.read_csv(
        mock_dir(size) / "don_hang_seller_center.csv", encoding="utf-8-sig", dtype=str
    )


def _manifest(size: str = "nho") -> dict[str, list[dict[str, object]]]:
    data: dict[str, list[dict[str, object]]] = json.loads(
        (answer_dir(size) / "manifest.json").read_text(encoding="utf-8")
    )
    return data


def _planted(kind: str) -> dict[str, object]:
    return next(e for e in _manifest()["loi_cai_san"] if e["loai"] == kind)


def test_six_files_loaded_in_dependency_order(loaded_ingest: IngestReport) -> None:
    sources = [f.source_type for f in loaded_ingest.files]
    assert sources == ["orders", "campaigns", "sale_program", "ads", "inventory", "cost"]
    assert all(f.status == "loaded" for f in loaded_ingest.files)
    # The answer key and the live folder are sub-directories: never listed.
    assert not any("ground_truth" in f.path or "live_11_11" in f.path for f in loaded_ingest.files)


def test_row_counts(loaded_db: duckdb.DuckDBPyConnection) -> None:
    raw = _raw_orders()
    unique_orders = raw.drop_duplicates()["Mã đơn hàng"].nunique()
    assert repo.count_rows(loaded_db, "orders") == unique_orders
    assert repo.count_rows(loaded_db, "order_lines") == len(raw.drop_duplicates())
    assert repo.count_rows(loaded_db, "skus") == 30
    n_inv = len(pd.read_csv(mock_dir("nho") / "ton_kho_google_sheet.csv", encoding="utf-8-sig"))
    assert repo.count_rows(loaded_db, "inventory_snapshots") == n_inv
    assert repo.count_rows(loaded_db, "ad_hourly") > 0
    # Filler rows of the ads adapter (hours missing in the report) are not stored.
    assert repo.count_rows(loaded_db, "ad_hourly") == conn_count(
        loaded_db, "SELECT count(*) FROM ad_hourly WHERE status <> 'unknown'"
    )
    assert repo.count_rows(loaded_db, "hourly_panel") == 30 * 2880
    batches = repo.fetch_all(loaded_db, "SELECT source_type, status, as_of FROM ingest_batches")
    assert len(batches) == 6
    assert {b["source_type"] for b in batches if b["as_of"] is not None} >= {"orders", "inventory"}


def conn_count(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row else 0


def test_sku_ids_mapped_before_upsert(loaded_db: duckdb.DuckDBPyConnection) -> None:
    for table in ("inventory_snapshots", "restock_plans", "restock_receipts", "cost_items"):
        assert conn_count(loaded_db, f"SELECT count(*) FROM {table} WHERE sku_id IS NULL") == 0
    truth = pd.read_csv(answer_dir("nho") / "sku_that.csv", encoding="utf-8-sig", dtype=str)
    code_to_sku = dict(zip(truth["ma_noi_bo"], truth["sku"], strict=True))
    rows = repo.fetch_all(
        loaded_db,
        "SELECT raw_code, sku_id FROM sku_aliases WHERE source_type = 'inventory' "
        "AND status = 'auto_accepted'",
    )
    assert rows and all(code_to_sku[r["raw_code"]] == r["sku_id"] for r in rows)


def test_planted_issues_found(loaded_db: duckdb.DuckDBPyConnection) -> None:
    def codes(code: str) -> list[dict[str, object]]:
        return repo.fetch_all(
            loaded_db,
            "SELECT entity_id, CAST(detail AS VARCHAR) AS detail FROM data_quality_issues "
            "WHERE code = ?",
            [code],
        )

    assert len(codes("DUP_ORDER")) == _planted("trung_don")["so_dong"]
    neg = {
        (json.loads(str(r["detail"]))["date"], json.loads(str(r["detail"]))["raw_code"])
        for r in codes("NEG_STOCK")
    }
    want = {(e["ngay"], e["ma_noi_bo"]) for e in _planted("ton_am")["chi_tiet"]}  # type: ignore[attr-defined]
    assert neg == want
    missing = {str(r["entity_id"]) for r in codes("MISSING_COST")}
    assert set(_planted("thieu_gia_von")["sku"]) <= missing  # type: ignore[arg-type]
    tz = json.loads(str(codes("ADS_TZ_SHIFT")[0]["detail"]))
    assert tz["proposed_offset_hours"] == 7
    gap = json.loads(str(codes("ADS_GAP_DAY")[0]["detail"]))
    assert gap["local_dates"] == ["2026-09-20"]
    lag = json.loads(str(codes("ADS_LAG")[0]["detail"]))
    assert lag["lag_hours"] * lag["campaigns"] == _planted("do_tre_ads")["so_dong_bi_giu"]
    flagged = {
        str(r[0])
        for r in loaded_db.execute("SELECT order_id FROM orders WHERE note_flagged").fetchall()
    }
    assert flagged == {_planted("prompt_injection")["ma_don"]}
    # Every stored code is a known one (02 §3.1 + the 4 adapter codes).
    from app.ingest.adapters.base import ISSUE_SEVERITY

    stored = {
        str(r[0])
        for r in loaded_db.execute("SELECT DISTINCT code FROM data_quality_issues").fetchall()
    }
    assert stored <= set(ISSUE_SEVERITY)
    assert set(DERIVED_CODES) <= set(ISSUE_SEVERITY)


def test_no_pii_in_duckdb(loaded_db: duckdb.DuckDBPyConnection) -> None:
    dropped = loaded_db.execute(
        "SELECT dropped_columns FROM ingest_batches WHERE source_type = 'orders'"
    ).fetchall()[0][0]
    assert set(dropped) >= {"Người mua", "Số điện thoại", "Địa chỉ nhận hàng"}
    cols = loaded_db.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'main' AND (data_type LIKE 'VARCHAR%' OR data_type = 'JSON')"
    ).fetchall()
    phone = re.compile(r"(^|[^0-9A-Za-z])0[0-9]{9}([^0-9A-Za-z]|$)")
    for table, col in cols:
        values = loaded_db.execute(
            f'SELECT DISTINCT CAST("{col}" AS VARCHAR) FROM "{table}" WHERE "{col}" IS NOT NULL'
        ).fetchall()
        for (v,) in values:
            assert not any(t in v for t in PII_TEXT), (table, col, v)
            assert not phone.search(v), (table, col, v)


def test_app_code_never_reads_answer_key() -> None:
    app_dir = Path(__file__).resolve().parents[2] / "app"
    src = "\n".join(p.read_text(encoding="utf-8") for p in app_dir.rglob("*.py"))
    assert "ground_truth" not in src.lower().replace("-", "_")


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    conn = duckdb.connect(str(tmp_path / "x.duckdb"))
    first, _ = load_mock(conn, "nho", build_panel=False)
    counts = dict(first.table_rows)
    again = ingest_files(conn, mock_dir("nho"), build_panel=False)
    assert [f.status for f in again.files] == ["skipped"] * 6
    assert again.table_rows == counts
    assert again.events == []
    forced = ingest_files(conn, mock_dir("nho"), force=True, build_panel=False)
    assert all(f.status == "loaded" for f in forced.files)
    assert {f.batch_id for f in forced.files} == {f.batch_id for f in first.files}
    assert forced.table_rows == counts  # upsert on natural keys: nothing doubles
    conn.close()


def test_data_updated_event_published(tmp_path: Path) -> None:
    from app.core.events import EventBus

    bus = EventBus()
    conn = duckdb.connect(str(tmp_path / "x.duckdb"))
    from app.warehouse.db import migrate

    migrate(conn)
    repo.seed_bundles(conn)
    report = ingest_files(conn, mock_dir("nho") / "don_hang_seller_center.csv", bus=bus)
    assert bus.pending == 1
    (event,) = report.events
    assert isinstance(event, DataUpdated)
    assert event.sources == ["orders"] and len(event.skus_changed) == 30
    assert event.as_of is not None
    conn.close()


def test_unknown_file_is_a_failed_batch(tmp_path: Path) -> None:
    odd = tmp_path / "la.csv"
    odd.write_text("a,b\n1,2\n", encoding="utf-8")
    conn = duckdb.connect()
    report = ingest_files(conn, odd)
    (f,) = report.files
    assert f.status == "failed" and f.batch_status == "failed"
    assert report.issues_by_code == {"UNKNOWN_SOURCE": 1}
