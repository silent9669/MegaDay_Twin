"""``audit_log`` writer shared by the API, the governance services and the AI layer (10 §9).

One row per write: ``write_audit(conn, actor, "decision.approve", "decision", id, before=…,
after=…)``. Never raises (an audit failure must not undo the action); no PII, no keys.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import duckdb
import structlog

from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.timeutil import utcnow
from app.governance.roles import Actor

log = get_logger(__name__)


def current_request_id() -> str | None:
    """Request id bound by the ``request_context`` middleware (``app/main.py``)."""
    v = structlog.contextvars.get_contextvars().get("request_id")
    return str(v) if v else None


def _dump(v: Any) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False, default=str)


def write_audit(
    conn: duckdb.DuckDBPyConnection,
    actor: Actor,
    action: str,
    target_kind: str,
    target_id: str,
    *,
    before: Any = None,
    after: Any = None,
    shop_id: str | None = "shop-demo",
) -> None:
    try:
        conn.execute(
            'INSERT INTO audit_log (id, "at", shop_id, actor, actor_role, via, action,'
            " target_kind, target_id, before, after, request_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                new_id("aud"),
                utcnow(),
                shop_id,
                actor.id,
                actor.role,
                actor.via,
                action,
                target_kind,
                target_id,
                _dump(before),
                _dump(after),
                actor.request_id or current_request_id(),
            ],
        )
    except duckdb.Error as e:  # pragma: no cover - audit must not break the action
        log.warning("audit_failed", action=action, error=str(e))


def list_audit(
    conn: duckdb.DuckDBPyConnection,
    *,
    target_kind: str | None = None,
    target_id: str | None = None,
    action_prefix: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    where: list[str] = []
    args: list[Any] = []
    if target_kind:
        where.append("target_kind = ?")
        args.append(target_kind)
    if target_id:
        where.append("target_id = ?")
        args.append(target_id)
    if action_prefix:
        where.append("starts_with(action, ?)")
        args.append(action_prefix)
    sql = (
        'SELECT id, "at", shop_id, actor, actor_role, via, action, target_kind, target_id,'
        " before, after, request_id FROM audit_log"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += ' ORDER BY "at" DESC, id DESC LIMIT ?'
    args.append(limit)
    cols = [
        "id", "at", "shop_id", "actor", "actor_role", "via", "action", "target_kind",
        "target_id", "before", "after", "request_id",
    ]  # fmt: skip
    out = []
    for r in conn.execute(sql, args).fetchall():
        d = dict(zip(cols, r, strict=True))
        for k in ("before", "after"):
            if isinstance(d[k], str):
                with contextlib.suppress(ValueError):
                    d[k] = json.loads(d[k])
        out.append(d)
    return out
