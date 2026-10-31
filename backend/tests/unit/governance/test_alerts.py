"""Shared alert store (GOV-01): create/dedupe/escalate/snooze/ack/resolve."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import duckdb
import pytest

from app.core.errors import NotFound, StateConflict, ValidationFailed
from app.core.timeutil import utcnow
from app.governance import alerts as al
from app.warehouse.db import migrate


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    migrate(c)
    yield c
    c.close()


def test_create_and_dedupe(conn: duckdb.DuckDBPyConnection) -> None:
    r = al.create_alert(
        conn, "stockout_risk", ("sku", "NOICHIEN2L"), "medium", "Sắp hết", {"hours": 3},
        impact_vnd=500_000, group_key="NOICHIEN2L",
    )  # fmt: skip
    assert r.created and r.raised
    a = r.alert
    assert a.status == "open" and a.target_kind == "sku" and a.target_id == "NOICHIEN2L"
    assert a.dedupe_key == "stockout_risk:sku:NOICHIEN2L" and a.detail == {"hours": 3}
    assert a.shop_id == "shop-demo" and a.occurrences == 1
    r2 = al.create_alert(conn, "stockout_risk", "sku:NOICHIEN2L", "medium", "Sắp hết (2)")
    assert not r2.created and not r2.raised and r2.alert.id == a.id
    assert r2.alert.occurrences == 2 and r2.alert.title == "Sắp hết (2)"
    assert r2.alert.detail == {"hours": 3}  # detail kept when not given
    assert r2.alert.impact_vnd == 500_000
    assert len(al.list_alerts(conn)) == 1


def test_custom_key_and_shop_scope(conn: duckdb.DuckDBPyConnection) -> None:
    a = al.create_alert(conn, "data_stale", None, "low", "Ads trễ", dedupe_key="stale:ads").alert
    assert a.target_kind is None and a.dedupe_key == "stale:ads"
    b = al.create_alert(conn, "data_stale", "shop", "low", "x", dedupe_key="stale:orders").alert
    assert b.target_kind == "shop" and b.id != a.id


def test_escalation_reopens_acknowledged(conn: duckdb.DuckDBPyConnection) -> None:
    a = al.create_alert(conn, "drift", ("sku", "X"), "low", "t").alert
    al.ack_alert(conn, a.id, "u-ops-1")
    same = al.create_alert(conn, "drift", ("sku", "X"), "low", "t")
    assert not same.raised and same.alert.status == "acknowledged"
    up = al.create_alert(conn, "drift", ("sku", "X"), "high", "t")
    assert up.raised and up.alert.status == "open" and up.alert.severity == "high"


def test_snooze_expiry_reraises(conn: duckdb.DuckDBPyConnection) -> None:
    a = al.create_alert(conn, "budget_exhausted", ("campaign", "CD002"), "medium", "t").alert
    now = utcnow()
    s = al.snooze_alert(conn, a.id, "u-ops-1", 30, now=now)
    assert s.status == "snoozed" and s.snoozed_until == now + timedelta(minutes=30)
    assert [x.id for x in al.list_alerts(conn, status="open", now=now)] == []
    later = now + timedelta(minutes=31)
    assert [x.id for x in al.list_alerts(conn, status="open", now=later)] == [a.id]
    r = al.create_alert(conn, "budget_exhausted", ("campaign", "CD002"), "medium", "t", now=later)
    assert r.raised and r.alert.status == "open" and r.alert.snoozed_until is None
    with pytest.raises(ValidationFailed):
        al.snooze_alert(conn, a.id, "u", 0)


def test_resolve_then_new_row(conn: duckdb.DuckDBPyConnection) -> None:
    a = al.create_alert(conn, "compute_failed", ("sku", "X"), "high", "t").alert
    assert al.resolve_by_key(conn, a.dedupe_key or "") == [a.id]
    assert al.resolve_by_key(conn, a.dedupe_key or "") == []
    res = al.get_alert(conn, a.id)
    assert res.status == "resolved" and res.resolved_by == "system" and res.resolved_at
    with pytest.raises(StateConflict):
        al.ack_alert(conn, a.id, "u")
    again = al.create_alert(conn, "compute_failed", ("sku", "X"), "high", "t")
    assert again.created and again.alert.id != a.id
    r = al.resolve_alert(conn, again.alert.id, "u-ops-1", note="đã chạy lại")
    assert r.detail == {"resolution_note": "đã chạy lại"} and r.resolved_by == "u-ops-1"


def test_validation_and_not_found(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValidationFailed):
        al.create_alert(conn, "nope", None, "high", "t")
    with pytest.raises(ValidationFailed):
        al.create_alert(conn, "drift", None, "urgent", "t")
    with pytest.raises(NotFound):
        al.get_alert(conn, "al-x")


def test_list_order_and_filters(conn: duckdb.DuckDBPyConnection) -> None:
    low = al.create_alert(conn, "drift", ("sku", "A"), "low", "a", impact_vnd=9_000_000).alert
    hi1 = al.create_alert(conn, "stockout_risk", ("sku", "B"), "high", "b", impact_vnd=10).alert
    hi2 = al.create_alert(conn, "stockout_risk", ("sku", "C"), "high", "c", impact_vnd=99).alert
    assert [a.id for a in al.list_alerts(conn)] == [hi2.id, hi1.id, low.id]
    assert [a.id for a in al.list_alerts(conn, kind="drift")] == [low.id]
    assert [a.id for a in al.list_alerts(conn, target_id="B")] == [hi1.id]
    al.ack_alert(conn, hi1.id, "u")
    assert {a.id for a in al.list_alerts(conn, status="live")} == {low.id, hi1.id, hi2.id}
    assert al.count_open(conn) == 2
    al.set_investigation(conn, low.id, {"cause": "stockout"})
    assert al.get_alert(conn, low.id).investigation == {"cause": "stockout"}
    assert al.set_status(conn, low.id, "hidden_by_budget").status == "hidden_by_budget"
