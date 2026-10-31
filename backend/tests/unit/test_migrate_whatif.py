"""Migration 3 (sửa lỗi nhỏ M3): ``recommendations.what_if`` + ``scenarios.request``. On a
database written before it, a what-if run (with overrides) that superseded the standard
recommendation is marked ``what_if`` and the standard one becomes active again."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from app.warehouse.db import Database, migrate, schema_sql

T0 = datetime(2026, 10, 30, 7, tzinfo=UTC)
# the two columns added by migration 3, with the comma that ends the line before them
NEW_COLS = r",(\s*--[^\n]*)\n\s*(request|what_if)\s+(JSON|BOOLEAN)[^\n]*"


def _scenario(
    conn: duckdb.DuckDBPyConnection, sid: str, ov: dict[str, float], at: datetime
) -> None:
    conn.execute(
        "INSERT INTO scenarios (scenario_id, campaign_id, goal_id, start_at, horizon_hours,"
        " param_version, overrides, inputs_ref, samples, seed, requested_by, created_at)"
        " VALUES (?, 'CD002', 'g', ?, 24, '{}', ?, '{}', 300, 1, 'user', ?)",
        [sid, T0, json.dumps(ov), at],
    )


def _rec(
    conn: duckdb.DuckDBPyConnection, rid: str, sid: str, sup: str | None, at: datetime
) -> None:
    conn.execute(
        "INSERT INTO recommendations (recommendation_id, scenario_id, campaign_id, status,"
        " confidence, superseded_by, created_at) VALUES (?, ?, 'CD002', 'recommend',"
        " 'reference', ?, ?)",
        [rid, sid, sup, at],
    )


def test_migration_3_restores_the_standard_recommendation(tmp_path: Path) -> None:
    db = Database(tmp_path / "old.duckdb")
    conn = db.write
    # schema as it was before migration 3 (DuckDB cannot drop a column of a table that
    # other tables reference, so the two columns are cut from schema.sql instead)
    old = re.sub(NEW_COLS, r"\1", schema_sql())
    assert "what_if" not in old and "request       JSON" not in old
    conn.execute(old)
    for v in (1, 2):
        conn.execute("INSERT INTO schema_version VALUES (?, ?, 'old')", [v, T0])
    _scenario(conn, "scn-old", {}, T0)
    _scenario(conn, "scn-std", {}, T0 + timedelta(minutes=1))
    _scenario(conn, "scn-wi", {"leftover_value_per_unit": 9e6}, T0 + timedelta(minutes=2))
    _rec(conn, "rec-old", "scn-old", "rec-std", T0)
    _rec(conn, "rec-std", "scn-std", "rec-wi", T0 + timedelta(minutes=1))
    _rec(conn, "rec-wi", "scn-wi", None, T0 + timedelta(minutes=2))
    migrate(conn)
    rows = conn.execute(
        "SELECT recommendation_id, superseded_by, what_if FROM recommendations ORDER BY 1"
    ).fetchall()
    assert rows == [
        ("rec-old", "rec-std", False),
        ("rec-std", None, False),  # active again
        ("rec-wi", None, True),  # kept, never active
    ]
    cols = {r[0] for r in conn.execute("DESCRIBE scenarios").fetchall()}
    assert "request" in cols
    db.close()
