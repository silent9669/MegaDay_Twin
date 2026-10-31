"""Roles and the permission matrix (GOV-02, 10 §1).

Three fixed demo users (no password): ``u-ops-1`` Lan (operator), ``u-lead-1`` Hà (lead),
``u-view-1`` Minh (viewer). Ranks: viewer < operator < lead.

:data:`PERMISSIONS` maps an action name to the minimum role (one row per line of the 10 §1
table). Ownership rules come on top of the rank and are checked by :func:`check`:

* ``OWNER_ONLY`` — only the creator of the decision (submit, reopen, cancel);
* ``NOT_OWNER`` — never the creator, **even a lead** (approve, reject): nobody approves
  their own decision.

The API enforces rank with ``app.api.deps.require_permission(name)``; the governance services
call :func:`check` again with the owner, so the AI path gets the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core import messages_vi as vi
from app.core.errors import Forbidden

Role = Literal["viewer", "operator", "lead"]
ROLE_RANK: dict[str, int] = {"viewer": 0, "operator": 1, "lead": 2}
ROLE_LABEL_VI: dict[str, str] = {"viewer": "Xem", "operator": "Vận hành", "lead": "Trưởng ca"}

DEMO_USERS: dict[str, tuple[str, Role]] = {
    "u-ops-1": ("Lan", "operator"),
    "u-lead-1": ("Hà", "lead"),
    "u-view-1": ("Minh", "viewer"),
}

PERMISSIONS: dict[str, Role] = {
    "view": "viewer",  # every screen / number
    "ingest.upload": "operator",  # upload, confirm column / SKU mapping
    "scenario.run": "operator",
    "ai.ask": "operator",
    "decision.create": "operator",
    "decision.submit": "operator",
    "decision.reopen": "operator",
    "decision.cancel": "operator",
    "decision.approve": "lead",
    "decision.reject": "lead",
    "goal.edit": "lead",
    "parameter.edit.operator": "operator",  # editable_by = operator
    "parameter.edit.lead": "lead",  # editable_by = lead
    "action.send": "operator",  # simulated action after approval
    "action.verify": "operator",  # "Đánh dấu đã kiểm lại"
    "alert.ack": "operator",
    "alert.snooze": "operator",
    "alert.resolve": "operator",
    "alert.investigate": "operator",
    "demo.control": "lead",  # only when DEMO_MODE=true (checked by the live router)
    "audit.read": "lead",
}
OWNER_ONLY: frozenset[str] = frozenset({"decision.submit", "decision.reopen", "decision.cancel"})
NOT_OWNER: frozenset[str] = frozenset({"decision.approve", "decision.reject"})


@dataclass(frozen=True)
class Actor:
    """Who performs a governance action. ``via``: ``ui | ai | system``."""

    id: str
    role: Role
    via: str = "ui"
    request_id: str | None = None

    def at_least(self, role: Role) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]


SYSTEM = Actor(id="system", role="lead", via="system")


def min_role(permission: str) -> Role:
    try:
        return PERMISSIONS[permission]
    except KeyError as e:  # programming error
        raise KeyError(f"unknown permission {permission!r}") from e


def allowed(role: str, permission: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK[min_role(permission)]


def check(actor: Actor, permission: str, *, owner_id: str | None = None) -> None:
    """Raise ``Forbidden`` unless ``actor`` may do ``permission`` (rank + ownership)."""
    need = min_role(permission)
    if not actor.at_least(need):
        raise Forbidden(
            vi.ERR_FORBIDDEN
            + f" Cần vai trò {ROLE_LABEL_VI[need]}, bạn đang là {ROLE_LABEL_VI[actor.role]}.",
            required=need,
            role=actor.role,
            permission=permission,
        )
    if actor.via == "system":
        return
    if permission in OWNER_ONLY and owner_id is not None and actor.id != owner_id:
        raise Forbidden(
            "Chỉ người tạo quyết định mới làm được việc này.",
            permission=permission,
            reason="not_owner",
            owner=owner_id,
        )
    if permission in NOT_OWNER and owner_id is not None and actor.id == owner_id:
        raise Forbidden(
            "Không được tự duyệt hoặc từ chối quyết định của chính mình (kể cả lead);"
            " cần một lead khác.",
            permission=permission,
            reason="self_approval",
        )
