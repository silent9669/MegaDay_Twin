"""ECO-01 · contribution per unit, combo, missing cost blocks (05 §2)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb
import pytest

from app.economics.contribution import (
    MISSING_COST_REASON,
    bundle_contribution,
    contribution,
    load_contributions,
    load_costs,
)
from app.warehouse.db import migrate

DAY = date(2026, 11, 11)
AT = datetime(2026, 11, 10, 18, tzinfo=UTC)


def test_noichien_1111() -> None:
    assert contribution(531_000, 455_000, 3_000, 0.12) == pytest.approx(9_280)


def test_noichien_normal_day() -> None:
    assert contribution(690_000, 455_000, 3_000, 0.12) == pytest.approx(149_200)


def test_combo() -> None:
    m = bundle_contribution(574_000, [(455_000, 1), (18_000, 1)], 3_000, 0.12)
    assert m == pytest.approx(574_000 - 473_000 - 68_880 - 3_000)  # 29.120 ≈ 29.100 (05 §10)


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    migrate(c)
    for sku, price, bundle in [
        ("NOICHIEN2L", 690_000, False),
        ("GIAYLOT50", 45_000, False),
        ("COMBO-NOICHIEN", 719_000, True),
        ("NOCOST", 10_000, False),
    ]:
        c.execute("INSERT INTO products VALUES (?, ?)", [sku, sku])
        c.execute(
            "INSERT INTO skus (sku_id, product_id, list_price, is_bundle) VALUES (?, ?, ?, ?)",
            [sku, sku, price, bundle],
        )
    c.execute(
        "INSERT INTO bundle_components VALUES ('COMBO-NOICHIEN', 'NOICHIEN2L', 1),"
        " ('COMBO-NOICHIEN', 'GIAYLOT50', 1)"
    )
    for sku, uc, pc in [("NOICHIEN2L", 455_000, 3_000), ("GIAYLOT50", 18_000, 3_000)]:
        c.execute(
            "INSERT INTO cost_items VALUES (?, ?, ?, ?, NULL, 'file')",
            [sku, datetime(2026, 10, 1, tzinfo=UTC), uc, pc],
        )
    c.execute(
        "INSERT INTO cost_items VALUES ('COMBO-NOICHIEN', ?, 473000, 3000, NULL, 'file')",
        [datetime(2026, 10, 1, tzinfo=UTC)],
    )
    for sku, price in [("NOICHIEN2L", 531_000), ("COMBO-NOICHIEN", 574_000)]:
        c.execute(
            "INSERT INTO sale_commitments (commitment_id, program, day_local, kind, sku_id,"
            " value_int) VALUES (?, '11.11', ?, 'committed_price', ?, ?)",
            [f"c-{sku}", DAY, sku, price],
        )
    c.execute(
        "INSERT INTO price_events VALUES ('NOICHIEN2L', ?, 690000, 690000, 0, 'orders')",
        [date(2026, 11, 10)],
    )
    return c


def test_load_contributions(conn: duckdb.DuckDBPyConnection) -> None:
    t = load_contributions(
        conn, ["NOICHIEN2L", "GIAYLOT50", "COMBO-NOICHIEN"], [DAY, date(2026, 11, 12)], AT
    )
    assert not t.blocked
    e = t.get("NOICHIEN2L", DAY)
    assert e.price_source == "commitment" and e.m == pytest.approx(9_280)
    assert t.m("NOICHIEN2L", date(2026, 11, 12)) == pytest.approx(149_200)  # back to normal
    combo = t.get("COMBO-NOICHIEN", DAY)
    assert combo.is_bundle and combo.unit_cost == 473_000
    assert combo.m == pytest.approx(29_120)
    assert t.get("GIAYLOT50", DAY).price_source == "list_price"
    assert "9.280đ/sp" in e.describe()


def test_missing_cost_blocks(conn: duckdb.DuckDBPyConnection) -> None:
    t = load_contributions(conn, ["NOICHIEN2L", "NOCOST"], [DAY], AT)
    assert t.blocked
    assert t.missing_cost == ["NOCOST"]
    assert t.reasons() == [f"{MISSING_COST_REASON}: NOCOST"]


def test_combo_missing_component_cost_blocks(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("DELETE FROM cost_items WHERE sku_id = 'GIAYLOT50'")
    t = load_contributions(conn, ["COMBO-NOICHIEN"], [DAY], AT)
    assert t.blocked and "COMBO-NOICHIEN" in t.missing_cost and "GIAYLOT50" in t.missing_cost


def test_cost_version_by_time(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        "INSERT INTO cost_items VALUES ('NOICHIEN2L', ?, 470000, 3000, NULL, 'user')",
        [datetime(2026, 11, 5, tzinfo=UTC)],
    )
    assert load_costs(conn, ["NOICHIEN2L"], AT)["NOICHIEN2L"].unit_cost == 470_000
    before = datetime(2026, 10, 15, tzinfo=UTC)
    assert load_costs(conn, ["NOICHIEN2L"], before)["NOICHIEN2L"].unit_cost == 455_000
    # every row received after ``at``: the earliest known cost is used
    early = datetime(2026, 9, 1, tzinfo=UTC)
    assert load_costs(conn, ["NOICHIEN2L"], early)["NOICHIEN2L"].unit_cost == 455_000
