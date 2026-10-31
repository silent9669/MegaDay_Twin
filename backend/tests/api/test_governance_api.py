"""API-06: decisions / actions / alerts / audit endpoints + GOV-02 role checks over HTTP.

Runs on an empty temp database (``tests/conftest.py::client``) with a seeded scenario, so it
needs no mock data."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.governance.alerts import create_alert
from app.warehouse.db import Database
from tests.api.helpers import LEAD, OPS, VIEWER, err, is_local_iso, ok
from tests.unit.governance.seed import AS_OF, PAUSE, add_batch, add_blocking_issue, seed_scenario

LEAD2 = {"X-User": "u-lead-2", "X-Role": "lead"}
OPS2 = {"X-User": "u-ops-2", "X-Role": "operator"}
V1 = "/api/v1"


@pytest.fixture
def seeded(client: TestClient, db: Database) -> tuple[TestClient, Database, str, str]:
    add_batch(db.write, "orders")
    sid, rid = seed_scenario(db.write)
    return client, db, sid, rid


def _create(c: TestClient, rid: str, headers: dict[str, str] = OPS, **body: Any) -> dict[str, Any]:
    payload = {"recommendation_id": rid, "rationale": "theo đề xuất", **body}
    return ok(c.post(f"{V1}/decisions", json=payload, headers=headers), 201)["data"]


def test_create_list_get(seeded: tuple[TestClient, Database, str, str]) -> None:
    c, _db, sid, rid = seeded
    d = _create(c, rid)
    assert d["state"] == "draft" and d["created_by"] == "u-ops-1" and d["created_via"] == "ui"
    assert d["scenario_id"] == sid and d["recommendation_id"] == rid
    assert d["input_hash"] == "hash-" + sid
    assert d["action"]["kind"] == "reduce_budget" and d["override_of_recommendation"] is False
    assert d["expected_outcome"]["objective"]["p50"] == 1_200_000.0
    assert is_local_iso(d["created_at"])
    assert d["can"] == ["submit", "cancel"] and d["actions"] == []
    body = ok(c.get(f"{V1}/decisions", headers=VIEWER))
    assert [x["id"] for x in body["data"]] == [d["id"]] and body["meta"]["total"] == 1
    assert body["next_cursor"] is None
    one = ok(c.get(f"{V1}/decisions/{d['id']}", headers=VIEWER))
    assert one["data"]["can"] == []  # viewer
    assert {s["kind"] for s in one["meta"]["sources"]} == {"decision", "scenario", "recommendation"}
    err(c.get(f"{V1}/decisions/dec-nope", headers=VIEWER), 404, "NOT_FOUND")
    filt = ok(c.get(f"{V1}/decisions?state=approved,pending_approval", headers=VIEWER))
    assert filt["data"] == []


def test_create_with_target_and_action(seeded: tuple[TestClient, Database, str, str]) -> None:
    c, _db, sid, _rid = seeded
    body = {
        "target": {"campaign_id": "CD002"},
        "action": {"kind": "continue"},
        "scenario_id": sid,
        "rationale": "giữ nguyên",
        "override_of_recommendation": False,  # ignored: computed
    }
    d = ok(c.post(f"{V1}/decisions", json=body, headers=OPS), 201)["data"]
    assert d["override_of_recommendation"] is True
    body["action"] = PAUSE
    err(c.post(f"{V1}/decisions", json=body, headers=OPS), 409, "CONSTRAINT_VIOLATION")
    body["action"] = {"kind": "reduce_budget"}  # pct missing
    err(c.post(f"{V1}/decisions", json=body, headers=OPS), 400, "VALIDATION_ERROR")


def test_auth_and_roles(seeded: tuple[TestClient, Database, str, str]) -> None:
    c, _db, _sid, rid = seeded
    err(c.get(f"{V1}/decisions"), 401, "UNAUTHENTICATED")
    err(
        c.post(f"{V1}/decisions", json={"recommendation_id": rid}, headers=VIEWER), 403, "FORBIDDEN"
    )
    d = _create(c, rid)
    err(c.post(f"{V1}/decisions/{d['id']}/submit", headers=VIEWER), 403, "FORBIDDEN")
    err(c.post(f"{V1}/decisions/{d['id']}/submit", headers=OPS2), 403, "FORBIDDEN")
    ok(c.post(f"{V1}/decisions/{d['id']}/submit", headers=OPS))
    err(c.post(f"{V1}/decisions/{d['id']}/approve", headers=OPS), 403, "FORBIDDEN")
    err(c.get(f"{V1}/audit", headers=OPS), 403, "FORBIDDEN")


def test_lead_cannot_self_approve(seeded: tuple[TestClient, Database, str, str]) -> None:
    c, _db, _sid, rid = seeded
    d = _create(c, rid, headers=LEAD)
    ok(c.post(f"{V1}/decisions/{d['id']}/submit", headers=LEAD))
    e = err(c.post(f"{V1}/decisions/{d['id']}/approve", headers=LEAD), 403, "FORBIDDEN")
    assert e["error"]["details"]["reason"] == "self_approval"
    got = ok(c.get(f"{V1}/decisions/{d['id']}", headers=LEAD))["data"]
    assert got["state"] == "pending_approval" and got["can"] == ["reopen", "cancel"]
    a = ok(c.post(f"{V1}/decisions/{d['id']}/approve", json={"note": "ok"}, headers=LEAD2))
    assert a["data"]["state"] == "approved" and a["data"]["approved_by"] == "u-lead-2"


def test_full_flow_and_verify(seeded: tuple[TestClient, Database, str, str]) -> None:
    c, _db, _sid, rid = seeded
    d = _create(c, rid)
    did = d["id"]
    ok(c.post(f"{V1}/decisions/{did}/submit", headers=OPS))
    ap = ok(c.post(f"{V1}/decisions/{did}/approve", headers=LEAD))["data"]  # no body
    assert ap["state"] == "approved" and is_local_iso(ap["valid_until"])
    assert ap["can"] == ["send"]  # lead ≥ operator may send
    sent = ok(c.post(f"{V1}/decisions/{did}/actions", json={"mode": "simulated"}, headers=OPS), 201)
    act = sent["data"]["action"]
    assert sent["data"]["decision"]["state"] == "sent" and act["mode"] == "simulated"
    assert act["mode_label"] == "Thao tác mô phỏng" and is_local_iso(act["sent_at"])
    assert sent["data"]["decision"]["can"] == ["verify"]
    err(c.post(f"{V1}/decisions/{did}/actions", headers=OPS), 409, "STATE_CONFLICT")
    ok(c.get(f"{V1}/actions/{act['id']}", headers=VIEWER))
    assert len(ok(c.get(f"{V1}/actions?state=sent", headers=VIEWER))["data"]) == 1
    err(c.post(f"{V1}/actions/{act['id']}/verify", json={}, headers=OPS), 400, "VALIDATION_ERROR")
    err(
        c.post(f"{V1}/actions/{act['id']}/verify", json={"result": "verified"}, headers=VIEWER),
        403,
        "FORBIDDEN",
    )
    v = ok(
        c.post(
            f"{V1}/decisions/{did}/verify",
            json={"result": "verified", "note": "đã xem Seller Center"},
            headers=OPS,
        )
    )["data"]
    assert v["decision"]["state"] == "verified" and v["action"]["verified_by_human"] is True
    assert v["decision"]["actions"][0]["state"] == "verified"
    err(c.post(f"{V1}/decisions/{did}/verify", json={"result": "verified"}, headers=OPS), 409,
        "STATE_CONFLICT")  # fmt: skip
    audit = ok(c.get(f"{V1}/audit?target_id={did}", headers=LEAD))["data"]
    assert [r["action"] for r in reversed(audit)] == [
        "decision.create", "decision.submit", "decision.approve", "decision.send",
        "decision.verify",
    ]  # fmt: skip
    assert all(is_local_iso(r["at"]) for r in audit)


def test_reject_cancel_reopen_conflicts(seeded: tuple[TestClient, Database, str, str]) -> None:
    c, _db, _sid, rid = seeded
    d = _create(c, rid)
    err(c.post(f"{V1}/decisions/{d['id']}/approve", headers=LEAD), 409, "STATE_CONFLICT")
    ok(c.post(f"{V1}/decisions/{d['id']}/submit", headers=OPS))
    err(c.post(f"{V1}/decisions/{d['id']}/reject", json={}, headers=LEAD), 400, "VALIDATION_ERROR")
    r = ok(c.post(f"{V1}/decisions/{d['id']}/reject", json={"reason": "thiếu số"}, headers=LEAD))
    assert r["data"]["state"] == "rejected" and r["data"]["reject_reason"] == "thiếu số"
    err(c.post(f"{V1}/decisions/{d['id']}/cancel", headers=OPS), 409, "STATE_CONFLICT")
    d2 = _create(c, rid)
    ok(c.post(f"{V1}/decisions/{d2['id']}/submit", headers=OPS))
    assert ok(c.post(f"{V1}/decisions/{d2['id']}/reopen", headers=OPS))["data"]["state"] == "draft"
    assert ok(c.post(f"{V1}/decisions/{d2['id']}/cancel", headers=OPS))["data"]["state"] == (
        "cancelled"
    )


def test_stale_and_blocking(seeded: tuple[TestClient, Database, str, str]) -> None:
    c, db, _sid, rid = seeded
    d = _create(c, rid)
    add_batch(db.write, "orders", AS_OF + timedelta(hours=1))
    e = err(c.post(f"{V1}/decisions/{d['id']}/submit", headers=OPS), 409, "STALE_INPUT")
    assert "current_data_as_of" in e["error"]["details"]
    _sid2, rid2 = seed_scenario(db.write, data_as_of=AS_OF + timedelta(hours=1))
    d2 = _create(c, rid2)
    add_blocking_issue(db.write, "CD002")
    err(c.post(f"{V1}/decisions/{d2['id']}/submit", headers=OPS), 422, "BLOCKING_DATA_ISSUE")


def test_handoff_note(seeded: tuple[TestClient, Database, str, str]) -> None:
    c, _db, _sid, _rid = seeded
    body = {"kind": "handoff_note", "campaign_id": "CD002", "rationale": "Ca sau theo dõi CD002"}
    d = ok(c.post(f"{V1}/decisions", json=body, headers=OPS), 201)["data"]
    assert d["kind"] == "handoff_note" and d["action"] is None
    assert len(ok(c.get(f"{V1}/decisions?kind=handoff_note", headers=VIEWER))["data"]) == 1


# ---------------------------------------------------------------------- alerts
def test_alerts_endpoints(client: TestClient, db: Database) -> None:
    c = client
    a = create_alert(
        db.write, "sales_below_forecast", ("sku", "AOGIUNHIET-M"), "high",
        "Áo giữ nhiệt bán thấp hơn dự báo", {"hours": 3}, impact_vnd=1_850_000,
        group_key="AOGIUNHIET",
    ).alert  # fmt: skip
    b = create_alert(db.write, "budget_exhausted", "campaign:CD002", "low", "Hết ngân sách").alert
    body = ok(c.get(f"{V1}/alerts?status=open", headers=VIEWER))
    assert [x["id"] for x in body["data"]] == [a.id, b.id] and body["meta"]["total"] == 2
    first = body["data"][0]
    assert first["target"]["sku"] == "AOGIUNHIET-M" and first["impact_vnd"] == 1_850_000
    assert first["group_key"] == "AOGIUNHIET" and is_local_iso(first["raised_at"])
    assert first["investigation"] is None and first["status"] == "open"
    assert body["data"][1]["target"]["campaign_id"] == "CD002"
    assert [
        x["id"] for x in ok(c.get(f"{V1}/alerts?sku=AOGIUNHIET-M", headers=VIEWER))["data"]
    ] == [a.id]
    ok(c.get(f"{V1}/alerts/{a.id}", headers=VIEWER))
    err(c.get(f"{V1}/alerts/al-nope", headers=VIEWER), 404, "NOT_FOUND")
    err(c.post(f"{V1}/alerts/{a.id}/ack", headers=VIEWER), 403, "FORBIDDEN")
    ack = ok(c.post(f"{V1}/alerts/{a.id}/ack", headers=OPS))["data"]
    assert ack["status"] == "acknowledged" and ack["acknowledged_by"] == "u-ops-1"
    sn = ok(c.post(f"{V1}/alerts/{b.id}/snooze", json={"minutes": 30}, headers=OPS))["data"]
    assert sn["status"] == "snoozed" and is_local_iso(sn["snoozed_until"])
    err(c.post(f"{V1}/alerts/{b.id}/snooze", json={"minutes": 0}, headers=OPS), 400,
        "VALIDATION_ERROR")  # fmt: skip
    assert ok(c.get(f"{V1}/alerts?status=open", headers=VIEWER))["data"] == []
    assert len(ok(c.get(f"{V1}/alerts?status=live", headers=VIEWER))["data"]) == 2
    res = ok(c.post(f"{V1}/alerts/{a.id}/resolve", json={"note": "đã nhập hàng"}, headers=OPS))
    assert res["data"]["status"] == "resolved" and res["data"]["resolved_by"] == "u-ops-1"
    err(c.post(f"{V1}/alerts/{a.id}/ack", headers=OPS), 409, "STATE_CONFLICT")
    audit = ok(c.get(f"{V1}/audit?target_kind=alert", headers=LEAD))["data"]
    assert {r["action"] for r in audit} == {"alert.ack", "alert.snooze", "alert.resolve"}


# ---------------------------------------------------------------------- GOV-02 over HTTP
@pytest.mark.parametrize(
    ("method", "path", "headers"),
    [
        ("post", "/ingest/files", VIEWER),  # upload: operator
        ("post", "/ingest/batches/b-1/confirm", VIEWER),
        ("post", "/scenarios/run", VIEWER),  # run simulation: operator
        ("post", "/ai/chat", VIEWER),  # ask AI: operator
        ("post", "/decisions", VIEWER),
        ("post", "/decisions/d-1/approve", OPS),  # approve: lead
        ("post", "/decisions/d-1/reject", OPS),
        ("post", "/goals", OPS),  # goals: lead
        ("post", "/decisions/d-1/actions", VIEWER),
        ("post", "/actions/a-1/verify", VIEWER),
        ("post", "/alerts/a-1/ack", VIEWER),
        ("get", "/audit", OPS),
    ],
)
def test_forbidden_cells(
    client: TestClient, method: str, path: str, headers: dict[str, str]
) -> None:
    resp = getattr(client, method)(f"{V1}{path}", headers=headers)
    err(resp, 403, "FORBIDDEN")
