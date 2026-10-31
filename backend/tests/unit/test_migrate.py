"""INF-03 / WH-01 / WH-02 · migrate() creates every table of the plan, is
idempotent, and the bundle seed declares COMBO-NOICHIEN."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.warehouse import repo
from app.warehouse.db import MIGRATIONS, Database, expected_tables, list_tables

# Every table named in docs/dev-plan (02, 03, 04, 05, 06, 07, 10).
PLAN_TABLES = {
    # 02
    "ingest_batches",
    "data_quality_issues",
    "products",
    "skus",
    "sku_aliases",
    "bundle_components",
    "orders",
    "order_lines",
    "inventory_snapshots",
    "restock_plans",
    "restock_receipts",
    "campaigns",
    "campaign_skus",
    "ad_hourly",
    "cost_items",
    "price_events",
    "sale_commitments",
    "hourly_panel",
    # 03
    "model_registry",
    "model_scores",
    "forecasts",
    # 04
    "iroas_estimates",
    # 05
    "parameters",
    "goals",
    "scenarios",
    "scenario_results",
    "recommendations",
    # 06
    "event_log",
    "sku_compute_state",
    "live_cursor",
    # 07
    "ai_sessions",
    "ai_tool_calls",
    # 10
    "decisions",
    "actions",
    "alerts",
    "audit_log",
}


def test_migrate_creates_all_plan_tables(db: Database) -> None:
    tables = set(db.migrate())
    missing = PLAN_TABLES - tables
    assert not missing, f"missing tables: {sorted(missing)}"
    assert tables == set(expected_tables())
    assert "schema_version" in tables


def test_migrate_is_idempotent(db: Database) -> None:
    first = db.migrate()
    second = db.migrate()
    assert first == second
    rows = db.write.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    assert rows == [(1,)] + [(v,) for v, _, _ in sorted(MIGRATIONS)]


def test_migrate_persists_on_disk(db: Database) -> None:
    db.migrate()
    db.close()
    reopened = Database(db.path)
    try:
        assert set(list_tables(reopened.write)) >= PLAN_TABLES
    finally:
        reopened.close()


def test_reset_drops_data(db: Database) -> None:
    db.migrate()
    repo.ensure_sku(db.write, "NOICHIEN2L")
    assert repo.count_rows(db.write, "skus") == 1
    db.reset()
    assert repo.count_rows(db.write, "skus") == 0


@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("hourly_panel", {"sku_id", "hour_utc", "units_gross", "stockout", "panel_version"}),
        ("sku_aliases", {"alias_id", "source_type", "raw_code", "raw_name", "norm_name", "status"}),
        ("order_lines", {"order_id", "line_no", "sku_id", "qty", "unit_price", "line_total"}),
        ("ad_hourly", {"campaign_id", "hour_utc", "spend", "attributed_revenue"}),
        ("scenario_results", {"scenario_id", "action", "objective", "per_sku"}),
        ("decisions", {"id", "state", "input_hash", "expected_outcome"}),
    ],
)
def test_contract_columns(db: Database, table: str, columns: set[str]) -> None:
    db.migrate()
    assert columns <= set(repo.table_columns(db.write, table))


def test_timestamptz_roundtrip_is_utc(db: Database) -> None:
    db.migrate()
    conn = db.write
    ts = datetime(2026, 11, 11, 7, 0, tzinfo=UTC)
    conn.execute("INSERT INTO event_log VALUES ('e1', 'X', '{}', ?)", [ts])
    with db.reader() as cur:
        (got,) = cur.execute('SELECT "at" FROM event_log').fetchone() or (None,)
    assert got == ts
    assert got.utcoffset().total_seconds() == 0


def test_upsert_rows_idempotent_and_updates(db: Database) -> None:
    db.migrate()
    conn = db.write
    rows = [
        {"product_id": "BINHNUOC500", "name": "Bình nước"},
        {"product_id": "BINHNUOC500", "name": "Bình nước giữ nhiệt"},  # same key: last wins
    ]
    assert repo.upsert_rows(conn, "products", rows, key=["product_id"]) == 1
    repo.upsert_rows(conn, "products", rows[:1], key=["product_id"])
    assert repo.fetch_all(conn, "SELECT * FROM products") == [
        {"product_id": "BINHNUOC500", "name": "Bình nước"}
    ]


def test_upsert_df(db: Database) -> None:
    import pandas as pd

    db.migrate()
    conn = db.write
    df = pd.DataFrame({"product_id": ["A", "B", "A"], "name": ["a1", "b", "a2"]})
    assert repo.upsert_df(conn, "products", df, key=["product_id"]) == 2
    got = repo.fetch_all(conn, "SELECT * FROM products ORDER BY product_id")
    assert got == [{"product_id": "A", "name": "a2"}, {"product_id": "B", "name": "b"}]


def test_upsert_rejects_bad_identifier(db: Database) -> None:
    db.migrate()
    with pytest.raises(ValueError):
        repo.upsert_rows(db.write, "products; DROP TABLE skus", [{"a": 1}], key=["a"])


def test_seed_bundle_combo_noichien(db: Database) -> None:
    db.migrate()
    conn = db.write
    assert repo.seed_bundles(conn) == 2
    assert repo.seed_bundles(conn) == 2  # idempotent
    comps = repo.get_bundle_components(conn, "COMBO-NOICHIEN")
    assert {c.sku_id: c.qty for c in comps["COMBO-NOICHIEN"]} == {"NOICHIEN2L": 1, "GIAYLOT50": 1}
    assert repo.count_rows(conn, "bundle_components") == 2
    sql = "SELECT is_bundle, product_id FROM skus WHERE sku_id = ?"
    assert repo.fetch_one(conn, sql, ["COMBO-NOICHIEN"]) == {
        "is_bundle": True,
        "product_id": "COMBO-NOICHIEN",
    }
    assert repo.fetch_one(conn, sql, ["NOICHIEN2L"]) == {
        "is_bundle": False,
        "product_id": "NOICHIEN2L",
    }


def test_declare_bundle_rejects_self_reference(db: Database) -> None:
    db.migrate()
    decl = repo.BundleDecl(
        bundle_sku_id="COMBO-X", components=[repo.BundleComponent(sku_id="COMBO-X")]
    )
    with pytest.raises(ValueError):
        repo.declare_bundle(db.write, decl)


def test_derive_product_id() -> None:
    assert repo.derive_product_id("BINHNUOC500-XANH") == "BINHNUOC500"
    assert repo.derive_product_id("NOICHIEN2L") == "NOICHIEN2L"
    assert repo.derive_product_id("COMBO-NOICHIEN", is_bundle=True) == "COMBO-NOICHIEN"
