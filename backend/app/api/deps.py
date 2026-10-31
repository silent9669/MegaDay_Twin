"""Request dependencies: user/role from headers (API-01, GOV-02), DB cursor, events, audit.

Auth (08 §1, 10 §1): ``X-User: <user_id>`` + ``X-Role: viewer|operator|lead``. No password
(hackathon). Missing ``X-User`` → 401 ``UNAUTHENTICATED``; unknown role → 401 too (the
client must pick one of the three); a role below the minimum → 403 ``FORBIDDEN``.

Role ranks: ``viewer`` (read) < ``operator`` (upload, run scenarios, drafts, edit
``editable_by=operator`` parameters) < ``lead`` (goals, ``editable_by=lead`` parameters).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Annotated, Any

import anyio.from_thread
import duckdb
from fastapi import Depends, Header, Request
from pydantic import BaseModel

from app.core import messages_vi as vi
from app.core.errors import Forbidden, Unauthenticated
from app.core.events import Event, EventBus
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.timeutil import utcnow
from app.governance.audit import current_request_id
from app.governance.roles import ROLE_LABEL_VI as ROLE_LABEL_VI
from app.governance.roles import ROLE_RANK as ROLE_RANK
from app.governance.roles import Actor, min_role
from app.governance.roles import Role as Role
from app.warehouse.db import Database

log = get_logger(__name__)

# Roles + permission matrix live in app/governance/roles.py (GOV-02); re-exported here.


class User(BaseModel):
    id: str
    role: Role

    def at_least(self, role: Role) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]

    def actor(self, request: Request | None = None, via: str = "ui") -> Actor:
        """Governance actor for this user (``request_id`` from ``X-Request-ID``)."""
        rid = request.headers.get("X-Request-ID") if request is not None else None
        return Actor(id=self.id, role=self.role, via=via, request_id=rid)


def current_user(
    x_user: Annotated[str | None, Header(alias="X-User")] = None,
    x_role: Annotated[str | None, Header(alias="X-Role")] = None,
) -> User:
    uid = (x_user or "").strip()
    if not uid:
        raise Unauthenticated()
    role = (x_role or "viewer").strip().lower()
    if role not in ROLE_RANK:
        raise Unauthenticated(
            "Vai trò không hợp lệ (X-Role phải là viewer, operator hoặc lead).", role=role
        )
    return User(id=uid, role=role)


def require_role(minimum: Role) -> Callable[..., User]:
    """FastAPI dependency: the caller's role must be ≥ ``minimum`` (10 §1)."""

    def dep(user: Annotated[User, Depends(current_user)]) -> User:
        if not user.at_least(minimum):
            raise Forbidden(
                vi.ERR_FORBIDDEN
                + f" Cần vai trò {ROLE_LABEL_VI[minimum]}, bạn đang là {ROLE_LABEL_VI[user.role]}.",
                required=minimum,
                role=user.role,
            )
        return user

    return dep


def require_permission(permission: str) -> Callable[..., User]:
    """FastAPI dependency: rank check for a named permission of the matrix
    (``app.governance.roles.PERMISSIONS``). Ownership rules (creator only / never the
    creator) are checked by the governance service, which knows the owner."""
    return require_role(min_role(permission))


Viewer = Annotated[User, Depends(require_role("viewer"))]
Operator = Annotated[User, Depends(require_role("operator"))]
Lead = Annotated[User, Depends(require_role("lead"))]


def db_conn(request: Request) -> Iterator[duckdb.DuckDBPyConnection]:
    """One DuckDB cursor per request (cursors are independent connections on the single
    process-wide database, 01 §3)."""
    db: Database = request.app.state.db
    with db.reader() as cur:
        yield cur


Conn = Annotated[duckdb.DuckDBPyConnection, Depends(db_conn)]


def publish(request: Request, *events: Event) -> None:
    """Put events on the app bus from a sync endpoint (worker thread) or the loop."""
    bus: EventBus | None = getattr(request.app.state, "bus", None)
    if bus is None:
        return
    for ev in events:
        try:
            anyio.from_thread.run_sync(bus.publish_nowait, ev)
        except RuntimeError:  # already on the event loop thread
            bus.publish_nowait(ev)


def audit(
    conn: duckdb.DuckDBPyConnection,
    request: Request,
    user: User,
    action: str,
    target_kind: str,
    target_id: str,
    *,
    before: Any = None,
    after: Any = None,
) -> None:
    """One ``audit_log`` row per write (10 §9, GOV-07 minimal). Never fails the request."""
    try:
        settings = getattr(request.app.state, "settings", None)
        conn.execute(
            'INSERT INTO audit_log (id, "at", shop_id, actor, actor_role, via, action,'
            " target_kind, target_id, before, after, request_id)"
            " VALUES (?, ?, ?, ?, ?, 'ui', ?, ?, ?, ?, ?, ?)",
            [
                new_id("aud"),
                utcnow(),
                getattr(settings, "shop_id", None),
                user.id,
                user.role,
                action,
                target_kind,
                target_id,
                None if before is None else json.dumps(before, ensure_ascii=False, default=str),
                None if after is None else json.dumps(after, ensure_ascii=False, default=str),
                request.headers.get("X-Request-ID") or current_request_id(),
            ],
        )
    except duckdb.Error as e:  # pragma: no cover - audit must not break the action
        log.warning("audit_failed", action=action, error=str(e))
