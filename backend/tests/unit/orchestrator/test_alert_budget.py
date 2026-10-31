"""ORC-05 units: dedupe through the governance store, alert budget (8 open), event alerts
(``ComputeFailed``, ``RecommendationChanged``)."""

from __future__ import annotations

import duckdb
import pytest

from app.core.events import AlertRaised, ComputeFailed, RecommendationChanged
from app.orchestrator.alerts_eval import AlertEvaluator
from app.warehouse.db import Database


@pytest.fixture
def conn(db: Database) -> duckdb.DuckDBPyConnection:
    db.migrate()
    return db.write


def _count(conn: duckdb.DuckDBPyConnection, status: str) -> int:
    row = conn.execute("SELECT count(*) FROM alerts WHERE status = ?", [status]).fetchone()
    return int(row[0]) if row else 0


def test_budget_dedupe_and_promotion(conn: duckdb.DuckDBPyConnection) -> None:
    ev = AlertEvaluator()
    raised: list[str] = []
    for i in range(10):
        raised += ev._upsert(
            conn,
            "stockout_risk",
            ("sku", f"S{i}"),
            "high" if i < 3 else "medium",
            f"t{i}",
            {"data_hour": "13:59"},
            key=f"k{i}",
            impact_vnd=i * 1000,
        )
    assert len(raised) == 10
    events = ev._finish(conn, raised, [])
    assert _count(conn, "open") == 8 and _count(conn, "hidden_by_budget") == 2
    # the 2 hidden ones: medium with the smallest impact (k3, k4)
    hidden = {
        str(r[0])
        for r in conn.execute(
            "SELECT dedupe_key FROM alerts WHERE status = 'hidden_by_budget'"
        ).fetchall()
    }
    assert hidden == {"k3", "k4"}
    assert len([e for e in events if isinstance(e, AlertRaised)]) == 8
    # a repeat of an open alert refreshes it (no new row, no new AlertRaised)
    assert ev._upsert(conn, "stockout_risk", ("sku", "S9"), "medium", "t9", {}, key="k9") == []
    # a repeat of a hidden one stays hidden, no duplicate row
    assert ev._upsert(conn, "stockout_risk", ("sku", "S3"), "medium", "t3", {}, key="k3") == []
    row = conn.execute("SELECT count(*) FROM alerts").fetchone()
    assert row is not None and row[0] == 10
    # severity never goes down on a refresh; first_data_hour kept
    later = {"data_hour": "15:59"}
    ev._upsert(conn, "stockout_risk", ("sku", "S0"), "medium", "t0", later, key="k0")
    sev, detail = conn.execute(
        "SELECT severity, detail->>'first_data_hour' FROM alerts WHERE dedupe_key = 'k0'"
    ).fetchone() or (None, None)
    assert sev == "high" and detail == "13:59"
    # condition cleared → resolved → a hidden one comes back
    resolved = ev._resolve(conn, "k9")
    events = ev._finish(conn, [], resolved)
    assert _count(conn, "open") == 8 and _count(conn, "hidden_by_budget") == 1
    promoted = [e for e in events if isinstance(e, AlertRaised)]
    assert len(promoted) == 1


def test_event_alerts(conn: duckdb.DuckDBPyConnection) -> None:
    ev = AlertEvaluator()
    out = ev.handle(conn, ComputeFailed(target="campaign:CD002", stage="simulate", error="x"))
    assert [type(e) for e in out] == [AlertRaised]
    # same failure again: deduped
    again = ComputeFailed(target="campaign:CD002", stage="simulate", error="y")
    assert ev.handle(conn, again) == []
    # our own evaluator failure never loops
    assert ev.handle(conn, ComputeFailed(target="alerts", stage="evaluate", error="z")) == []
    first = RecommendationChanged(campaign_id="CD002", old=None, new={"best": None})
    assert ev.handle(conn, first) == []  # first recommendation is not a change
    change = RecommendationChanged(
        campaign_id="CD002",
        old={"best": {"kind": "continue"}, "status": "recommend"},
        new={"best": {"kind": "pause"}, "status": "recommend", "recommendation_id": "rec-1"},
    )
    out = ev.handle(conn, change)
    assert len(out) == 1
    title = conn.execute(
        "SELECT title FROM alerts WHERE kind = 'recommendation_changed'"
    ).fetchone()
    assert title is not None and "Chạy tiếp → Tạm dừng" in title[0]
