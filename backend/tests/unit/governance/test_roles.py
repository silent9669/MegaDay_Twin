"""Permission matrix (GOV-02, 10 §1): one assertion per cell of the table."""

from __future__ import annotations

import pytest

from app.core.errors import Forbidden
from app.governance.roles import PERMISSIONS, Actor, allowed, check

# (permission, viewer, operator, lead) — 10 §1
MATRIX = [
    ("view", True, True, True),
    ("ingest.upload", False, True, True),
    ("scenario.run", False, True, True),
    ("ai.ask", False, True, True),
    ("decision.create", False, True, True),
    ("decision.submit", False, True, True),
    ("decision.cancel", False, True, True),
    ("decision.approve", False, False, True),
    ("decision.reject", False, False, True),
    ("goal.edit", False, False, True),
    ("parameter.edit.operator", False, True, True),
    ("parameter.edit.lead", False, False, True),
    ("action.send", False, True, True),
    ("action.verify", False, True, True),
    ("demo.control", False, False, True),
    ("audit.read", False, False, True),
]


@pytest.mark.parametrize(("perm", "viewer", "operator", "lead"), MATRIX)
def test_matrix(perm: str, viewer: bool, operator: bool, lead: bool) -> None:
    assert allowed("viewer", perm) is viewer
    assert allowed("operator", perm) is operator
    assert allowed("lead", perm) is lead
    for role, ok in (("viewer", viewer), ("operator", operator), ("lead", lead)):
        actor = Actor(f"u-{role}", role)  # type: ignore[arg-type]
        if ok:
            check(actor, perm)
        else:
            with pytest.raises(Forbidden):
                check(actor, perm)


def test_matrix_covers_catalogue() -> None:
    covered = {m[0] for m in MATRIX}
    assert covered <= set(PERMISSIONS)
    assert allowed("nobody", "view") is False


def test_self_approval_forbidden_even_for_lead() -> None:
    lead = Actor("u-lead-1", "lead")
    for perm in ("decision.approve", "decision.reject"):
        with pytest.raises(Forbidden) as e:
            check(lead, perm, owner_id="u-lead-1")
        assert e.value.details["reason"] == "self_approval"
        check(lead, perm, owner_id="u-ops-1")


def test_owner_only() -> None:
    lead = Actor("u-lead-1", "lead")
    with pytest.raises(Forbidden):
        check(lead, "decision.submit", owner_id="u-ops-1")
    check(Actor("u-ops-1", "operator"), "decision.cancel", owner_id="u-ops-1")
