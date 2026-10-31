"""Decision state machine (GOV-01, 10 §2): every valid transition, invalid ones → 409,
snapshot, freshness gate, self-approval, simulated action + manual verify."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta

import duckdb
import pytest

from app.core.errors import (
    ConstraintViolation,
    DataQualityError,
    Forbidden,
    NotFound,
    StaleDataError,
    StateConflict,
    ValidationFailed,
)
from app.governance import decisions as gov
from app.governance.roles import SYSTEM, Actor
from app.warehouse.db import migrate
from tests.unit.governance.seed import (
    AS_OF,
    CONTINUE,
    PAUSE,
    REDUCE,
    add_batch,
    add_blocking_issue,
    seed_scenario,
)

OPS = Actor("u-ops-1", "operator")
OPS2 = Actor("u-ops-2", "operator")
LEAD = Actor("u-lead-1", "lead")
LEAD2 = Actor("u-lead-2", "lead")
VIEW = Actor("u-view-1", "viewer")


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    migrate(c)
    add_batch(c, "orders")
    yield c
    c.close()


def _draft(conn: duckdb.DuckDBPyConnection, actor: Actor = OPS, **kw: object) -> gov.Decision:
    sid, rid = seed_scenario(conn)
    args: dict[str, object] = {"scenario_id": sid, "recommendation_id": rid, "action": CONTINUE}
    args.update(kw)
    return gov.create_decision(conn, actor, **args)  # type: ignore[arg-type]


def test_create_stores_full_snapshot(conn: duckdb.DuckDBPyConnection) -> None:
    d = _draft(conn, rationale="  giữ ngân sách  ")
    assert d.state == "draft" and d.kind == "ad_action"
    assert d.target_kind == "campaign" and d.target_id == "CD002"
    assert d.created_by == "u-ops-1" and d.created_via == "ui"
    assert d.rationale == "giữ ngân sách"
    assert d.recommendation_id and d.scenario_id and d.input_hash
    assert d.recommended_action == REDUCE
    assert d.override_of_recommendation is True  # chose continue, best = reduce 30 %
    assert d.data_as_of["scenario"] == AS_OF.isoformat()
    assert "orders" in d.data_as_of
    assert d.expected_outcome["objective"]["p50"] == 1_000_000.0
    assert d.expected_outcome["seed"] == 20261111
    assert d.goal_snapshot == {"goal_id": "goal-1"}
    assert d.model_versions["forecast_ids"] == ["fc-1"]
    assert d.parameters_snapshot == {}
    assert d.open_issues == []
    audit = conn.execute("SELECT action, via FROM audit_log WHERE target_id = ?", [d.id]).fetchall()
    assert audit == [("decision.create", "ui")]


def test_create_from_recommendation_only(conn: duckdb.DuckDBPyConnection) -> None:
    _sid, rid = seed_scenario(conn)
    d = gov.create_decision(conn, OPS, recommendation_id=rid)
    assert d.action == REDUCE
    assert d.override_of_recommendation is False
    assert d.action_label and "30%" in d.action_label


def test_create_rejections(conn: duckdb.DuckDBPyConnection) -> None:
    sid, _ = seed_scenario(conn)
    with pytest.raises(ConstraintViolation):
        gov.create_decision(conn, OPS, scenario_id=sid, action=PAUSE)
    with pytest.raises(ValidationFailed):
        gov.create_decision(
            conn, OPS, scenario_id=sid, action={"kind": "reduce_budget", "pct": 0.5}
        )
    with pytest.raises(ValidationFailed):
        gov.create_decision(conn, OPS, scenario_id=sid, campaign_id="CD999", action=CONTINUE)
    with pytest.raises(NotFound):
        gov.create_decision(conn, OPS, scenario_id="scn-x", action=CONTINUE)
    with pytest.raises(ValidationFailed):
        gov.create_decision(conn, OPS, action=CONTINUE)
    what_if, _ = seed_scenario(conn, overrides={"restock_delay_days": 1})
    with pytest.raises(ValidationFailed):
        gov.create_decision(conn, OPS, scenario_id=what_if, action=CONTINUE)
    with pytest.raises(Forbidden):
        gov.create_decision(conn, VIEW, scenario_id=sid, action=CONTINUE)


def test_happy_path_to_verified(conn: duckdb.DuckDBPyConnection) -> None:
    d = _draft(conn)
    d = gov.submit(conn, d.id, OPS)
    assert d.state == "pending_approval" and d.submitted_at is not None
    d = gov.approve(conn, d.id, LEAD, note="ok")
    assert d.state == "approved" and d.approved_by == "u-lead-1" and d.approval_note == "ok"
    assert d.valid_until is not None and d.approved_at is not None
    assert d.valid_until - d.approved_at == timedelta(hours=gov.DECISION_VALIDITY_HOURS)
    d, act = gov.send_action(conn, d.id, OPS)
    assert d.state == "sent" and act.state == "sent" and act.mode == "simulated"
    assert act.payload["simulated"] is True
    d, act = gov.verify_action(conn, act.id, OPS, result="verified", note="đã xem trên sàn")
    assert d.state == "verified" and act.state == "verified"
    assert act.verified_by_human is True and act.verified_by == "u-ops-1"
    actions = [
        r[0]
        for r in conn.execute(
            'SELECT action FROM audit_log WHERE target_id = ? ORDER BY "at", id', [d.id]
        ).fetchall()
    ]
    assert actions == [
        "decision.create",
        "decision.submit",
        "decision.approve",
        "decision.send",
        "decision.verify",
    ]


def test_reject_needs_reason_and_reopen_cancel(conn: duckdb.DuckDBPyConnection) -> None:
    d = gov.submit(conn, _draft(conn).id, OPS)
    with pytest.raises(ValidationFailed):
        gov.reject(conn, d.id, LEAD, reason="  ")
    r = gov.reject(conn, d.id, LEAD, reason="chưa đủ căn cứ")
    assert r.state == "rejected" and r.reject_reason == "chưa đủ căn cứ"
    d2 = gov.submit(conn, _draft(conn).id, OPS)
    d2 = gov.transition(conn, d2.id, "reopen", OPS)
    assert d2.state == "draft" and d2.submitted_at is None
    assert gov.cancel(conn, d2.id, OPS).state == "cancelled"
    d3 = gov.submit(conn, _draft(conn).id, OPS)
    assert gov.cancel(conn, d3.id, OPS).state == "cancelled"


# every (state, event) pair that is NOT in the table must raise StateConflict
_EVENTS = ("submit", "reopen", "approve", "reject", "cancel")


def _to_state(conn: duckdb.DuckDBPyConnection, state: str) -> gov.Decision:
    d = _draft(conn)
    if state == "draft":
        return d
    d = gov.submit(conn, d.id, OPS)
    if state == "pending_approval":
        return d
    if state == "rejected":
        return gov.reject(conn, d.id, LEAD, "x")
    if state == "cancelled":
        return gov.cancel(conn, d.id, OPS)
    d = gov.approve(conn, d.id, LEAD)
    if state == "approved":
        return d
    if state == "expired":
        gov.expire_overdue(conn, now=d.valid_until)  # type: ignore[arg-type]
        return gov.get_decision(conn, d.id)
    d, act = gov.send_action(conn, d.id, OPS)
    if state == "sent":
        return d
    res = "verified" if state == "verified" else "failed"
    d, _ = gov.verify_action(conn, act.id, OPS, result=res, note="n")  # type: ignore[arg-type]
    return d


@pytest.mark.parametrize(
    "state",
    ["draft", "pending_approval", "approved", "rejected", "cancelled", "expired", "sent",
     "verified", "failed"],
)  # fmt: skip
def test_invalid_transitions_conflict(conn: duckdb.DuckDBPyConnection, state: str) -> None:
    for ev in _EVENTS:
        froms = gov.TRANSITIONS[ev][0]
        if state in froms:
            continue
        d = _to_state(conn, state)
        assert d.state == state
        actor = LEAD2 if ev in ("approve", "reject") else OPS
        with pytest.raises(StateConflict):
            gov.transition(conn, d.id, ev, actor, reason="r")
    if state != "approved":
        d = _to_state(conn, state)
        with pytest.raises(StateConflict):
            gov.send_action(conn, d.id, OPS)


def test_lead_cannot_approve_own_decision(conn: duckdb.DuckDBPyConnection) -> None:
    d = gov.submit(conn, _draft(conn, actor=LEAD).id, LEAD)
    with pytest.raises(Forbidden) as e:
        gov.approve(conn, d.id, LEAD)
    assert e.value.details["reason"] == "self_approval"
    with pytest.raises(Forbidden):
        gov.reject(conn, d.id, LEAD, "x")
    assert gov.approve(conn, d.id, LEAD2).state == "approved"


def test_ownership_and_rank(conn: duckdb.DuckDBPyConnection) -> None:
    d = _draft(conn)
    with pytest.raises(Forbidden):
        gov.submit(conn, d.id, OPS2)  # not the creator
    with pytest.raises(Forbidden):
        gov.cancel(conn, d.id, LEAD)  # creator only, even for a lead
    d = gov.submit(conn, d.id, OPS)
    with pytest.raises(Forbidden):
        gov.approve(conn, d.id, OPS2)  # operator cannot approve
    with pytest.raises(Forbidden):
        gov.transition(conn, d.id, "reopen", OPS2)
    with pytest.raises(Forbidden):
        gov.submit(conn, _draft(conn).id, VIEW)


def test_stale_input_blocks_submit_and_approve(conn: duckdb.DuckDBPyConnection) -> None:
    d = _draft(conn)
    p = gov.submit(conn, _draft(conn).id, OPS)
    add_batch(conn, "inventory", AS_OF + timedelta(hours=1))
    with pytest.raises(StaleDataError) as e:
        gov.submit(conn, d.id, OPS)
    assert e.value.code == "STALE_INPUT"
    with pytest.raises(StaleDataError):
        gov.approve(conn, p.id, LEAD)
    assert gov.get_decision(conn, d.id).state == "draft"


def test_blocking_issue_blocks_submit(conn: duckdb.DuckDBPyConnection) -> None:
    d = _draft(conn)
    add_blocking_issue(conn, "CD002")
    with pytest.raises(DataQualityError):
        gov.submit(conn, d.id, OPS)


def test_expire_then_send_conflicts(conn: duckdb.DuckDBPyConnection) -> None:
    d = gov.approve(conn, gov.submit(conn, _draft(conn).id, OPS).id, LEAD)
    assert d.valid_until is not None
    expired = gov.expire_overdue(conn, now=d.valid_until + timedelta(seconds=1))
    assert expired == [d.id]
    with pytest.raises(StateConflict):
        gov.send_action(conn, d.id, OPS)
    assert gov.get_decision(conn, d.id).state == "expired"
    with pytest.raises(ValidationFailed):
        gov.transition(conn, d.id, "expire", LEAD)  # system only
    assert gov.transition(conn, _to_state(conn, "approved").id, "expire", SYSTEM).state == (
        "expired"
    )


def test_send_only_simulated(conn: duckdb.DuckDBPyConnection) -> None:
    d = _to_state(conn, "approved")
    with pytest.raises(ValidationFailed):
        gov.send_action(conn, d.id, OPS, mode="live")
    with pytest.raises(Forbidden):
        gov.send_action(conn, d.id, VIEW)


def test_verify_failed_needs_note_and_observed_compare(conn: duckdb.DuckDBPyConnection) -> None:
    d, act = gov.send_action(conn, _to_state(conn, "approved").id, OPS)
    with pytest.raises(ValidationFailed):
        gov.verify_action(conn, act.id, OPS, result="failed")
    d, act = gov.verify_action(conn, act.id, OPS, result="failed", note="sàn báo lỗi")
    assert d.state == "failed" and act.failure_reason == "sàn báo lỗi"
    with pytest.raises(StateConflict):
        gov.verify_action(conn, act.id, OPS, result="verified")
    # observed: continue + "Đang chạy" → verified
    d2, act2 = gov.send_action(conn, _to_state(conn, "approved").id, OPS)
    d2, act2 = gov.verify_action(conn, act2.id, OPS, observed={"observed_status": "Đang chạy"})
    assert d2.state == "verified" and act2.observed == {"observed_status": "Đang chạy"}


def test_compare_observed_rules() -> None:
    assert gov.compare_observed(PAUSE, {"observed_status": "Tạm dừng"})[0]
    assert not gov.compare_observed(PAUSE, {"observed_status": "Đang chạy"})[0]
    assert gov.compare_observed(PAUSE, {"observed_spend_vnd": 0})[0]
    obs = {"observed_budget_vnd": 700_000, "expected_budget_vnd": 1_000_000}
    assert gov.compare_observed(REDUCE, obs)[0]
    obs = {"observed_budget_vnd": 900_000, "expected_budget_vnd": 1_000_000}
    assert not gov.compare_observed(REDUCE, obs)[0]
    assert not gov.compare_observed(CONTINUE, {"observed_status": "Tạm dừng"})[0]


def test_handoff_note(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValidationFailed):
        gov.create_decision(conn, OPS, kind="handoff_note", rationale=" ")
    n = gov.create_decision(conn, OPS, kind="handoff_note", rationale="Ca sau theo dõi CD002")
    assert n.kind == "handoff_note" and n.action is None and n.state == "draft"
    n = gov.approve(conn, gov.submit(conn, n.id, OPS).id, LEAD)
    with pytest.raises(StateConflict):
        gov.send_action(conn, n.id, OPS)


def test_ai_created_via(conn: duckdb.DuckDBPyConnection) -> None:
    d = _draft(conn, actor=Actor("u-ops-1", "operator", via="ai"))
    assert d.created_via == "ai"
    row = conn.execute("SELECT via FROM audit_log WHERE target_id = ?", [d.id]).fetchone()
    assert row == ("ai",)


def test_allowed_events(conn: duckdb.DuckDBPyConnection) -> None:
    d = _draft(conn)
    assert gov.allowed_events(d, OPS) == ["submit", "cancel"]
    assert gov.allowed_events(d, OPS2) == []
    p = gov.submit(conn, d.id, OPS)
    assert gov.allowed_events(p, OPS) == ["reopen", "cancel"]
    assert gov.allowed_events(p, LEAD) == ["approve", "reject"]
    assert gov.allowed_events(p, VIEW) == []


def test_list_filters(conn: duckdb.DuckDBPyConnection) -> None:
    a = _draft(conn)
    b = gov.submit(conn, _draft(conn, actor=OPS2).id, OPS2)
    assert {d.id for d in gov.list_decisions(conn)} == {a.id, b.id}
    assert [d.id for d in gov.list_decisions(conn, state="pending_approval")] == [b.id]
    assert [d.id for d in gov.list_decisions(conn, created_by="u-ops-1")] == [a.id]
    assert json.loads(json.dumps(gov.list_decisions(conn)[0].model_dump(mode="json")))
