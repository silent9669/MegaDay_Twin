"""ING-07 · cross-source quality rules on small synthetic warehouses."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from app.ingest.adapters.base import make_issue
from app.ingest.quality import (
    check_bundle_no_stock_row,
    check_missing_cost,
    check_spend_over_budget,
    check_stock_jump,
    check_unmatched_sku,
    derived_issue_id,
    refresh_derived_issues,
    write_batch_issues,
)
from app.warehouse import repo
from app.warehouse.db import migrate

NOW = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    migrate(c)
    for sku in ("A", "B", "C"):
        repo.ensure_sku(c, sku)
    c.execute(
        "INSERT INTO ingest_batches (batch_id, source_type, file_name, file_sha256, received_at, "
        "mode, status) VALUES ('ib-1', 'inventory', 'x', 'h1', ?, 'history', 'ok'), "
        "('ib-2', 'cost', 'y', 'h2', ?, 'history', 'ok'), "
        "('ib-3', 'ads', 'z', 'h3', ?, 'history', 'ok')",
        [NOW, NOW, NOW],
    )
    return c


def _order(c: duckdb.DuckDBPyConnection, oid: str, sku: str, at: datetime, qty: int,
           status: str = "completed") -> None:  # fmt: skip
    c.execute(
        "INSERT INTO orders (order_id, ordered_at, status, status_final, batch_id) "
        "VALUES (?, ?, ?, true, 'ib-1')",
        [oid, at, status],
    )
    c.execute(
        "INSERT INTO order_lines VALUES (?, 1, ?, ?, 100, 100, ?)", [oid, sku, qty, 100 * qty]
    )


def _snap(c: duckdb.DuckDBPyConnection, sku: str, day: date, on_hand: int | None) -> None:
    at = datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(days=1, hours=-7)
    c.execute(
        "INSERT INTO inventory_snapshots (sku_id, snapshot_at, on_hand, granularity, batch_id) "
        "VALUES (?, ?, ?, 'day', 'ib-1')",
        [sku, at, on_hand],
    )


def test_stock_jump_uses_sales_and_receipts(conn: duckdb.DuckDBPyConnection) -> None:
    d0 = date(2026, 10, 1)
    _snap(conn, "A", d0, 100)
    _snap(conn, "A", d0 + timedelta(days=1), 90)  # sold 10 → ok
    _snap(conn, "A", d0 + timedelta(days=2), 130)  # sold 0, received 40 → ok
    _snap(conn, "A", d0 + timedelta(days=3), None)  # unreadable: skipped
    _snap(conn, "A", d0 + timedelta(days=4), 100)  # 30 unexplained over two days → jump
    for i in range(10):
        _order(conn, f"o{i}", "A", datetime(2026, 10, 2, 5, i, tzinfo=UTC), 1)
    _order(conn, "ox", "A", datetime(2026, 10, 2, 6, tzinfo=UTC), 50, status="cancelled")
    conn.execute("INSERT INTO restock_receipts (sku_id, received_on, qty) VALUES ('A', ?, 40)",
                 [d0 + timedelta(days=2)])  # fmt: skip
    issues = check_stock_jump(conn)
    assert [i.detail["diff"] for i in issues] == [-30]
    assert issues[0].detail["snapshot_local"] == "2026-10-05"
    assert check_stock_jump(conn, since=datetime(2026, 10, 10, tzinfo=UTC)) == []


def test_combo_orders_consume_component_stock(conn: duckdb.DuckDBPyConnection) -> None:
    repo.declare_bundle(
        conn,
        repo.BundleDecl(bundle_sku_id="C", components=[repo.BundleComponent(sku_id="A", qty=2)]),
    )
    _snap(conn, "A", date(2026, 10, 1), 100)
    _snap(conn, "A", date(2026, 10, 2), 80)
    _order(conn, "c1", "C", datetime(2026, 10, 2, 3, tzinfo=UTC), 10)
    assert check_stock_jump(conn) == []


def test_missing_cost_and_bundle_rules(conn: duckdb.DuckDBPyConnection) -> None:
    _order(conn, "o1", "A", NOW, 1)
    _order(conn, "o2", "B", NOW, 1)
    _snap(conn, "A", date(2026, 10, 1), 5)
    conn.execute(
        "INSERT INTO cost_items (sku_id, valid_from, unit_cost, source) "
        "VALUES ('A', ?, 10, 'file')",
        [NOW],
    )
    conn.execute(
        "INSERT INTO sku_aliases (alias_id, source_type, raw_code, raw_name, norm_name, sku_id, "
        "score, status) VALUES ('sal-1', 'cost', '', 'b?', 'b', 'B', 0.8, 'pending')"
    )
    missing = check_missing_cost(conn)
    assert [(i.entity_id, i.detail["reason"]) for i in missing] == [("B", "cost_alias_pending")]
    assert missing[0].severity == "error"
    (unmatched,) = check_unmatched_sku(conn)
    assert unmatched.detail["suggested_sku"] == "B"
    assert [i.entity_id for i in check_bundle_no_stock_row(conn)] == ["B"]


def test_spend_over_budget(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("INSERT INTO campaigns VALUES ('CD1', 'x', NULL, 1000, NULL, true)")
    for h, spend in enumerate([500, 600]):
        conn.execute(
            "INSERT INTO ad_hourly (campaign_id, hour_utc, status, daily_budget, spend, batch_id) "
            "VALUES ('CD1', ?, 'running', 1000, ?, 'ib-3')",
            [datetime(2026, 10, 1, 3 + h, tzinfo=UTC), spend],
        )
    (issue,) = check_spend_over_budget(conn)
    assert issue.detail == {"date": "2026-10-01", "spend": 1100, "budget": 1000, "ratio": 1.1}


def test_refresh_keeps_user_decision_and_drops_resolved(conn: duckdb.DuckDBPyConnection) -> None:
    a = make_issue("MISSING_COST", entity_type="sku", entity_id="A")
    b = make_issue("MISSING_COST", entity_type="sku", entity_id="B")
    batches = {"MISSING_COST": "ib-2"}
    assert refresh_derived_issues(conn, [a, b], batches, NOW) == 2
    conn.execute(
        "UPDATE data_quality_issues SET status = 'dismissed' WHERE issue_id = ?",
        [derived_issue_id(b)],
    )
    refresh_derived_issues(conn, [b], batches, NOW)  # A fixed, B still there
    rows = repo.fetch_all(conn, "SELECT entity_id, status FROM data_quality_issues")
    assert rows == [{"entity_id": "B", "status": "dismissed"}]
    refresh_derived_issues(conn, [], batches, NOW)  # a dismissed issue is kept
    assert repo.count_rows(conn, "data_quality_issues") == 1


def test_batch_issues_replace_on_reprocess(conn: duckdb.DuckDBPyConnection) -> None:
    issues = [make_issue("NEG_STOCK", entity_id="X"), make_issue("DUP_ROW", entity_id="Y")]
    assert write_batch_issues(conn, "ib-1", issues, NOW) == 2
    assert write_batch_issues(conn, "ib-1", issues[:1], NOW) == 1
    assert repo.count_rows(conn, "data_quality_issues") == 1
    row = repo.fetch_one(conn, "SELECT severity FROM data_quality_issues")
    assert row == {"severity": "error"}
