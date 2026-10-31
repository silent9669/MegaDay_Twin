"""Shared alert store (GOV-01, 10 §3 ``alerts``, 08 §3.11).

Every producer (orchestrator ``AlertEvaluator``, AI policy guard, ingest…) writes alerts
through :func:`create_alert`; the API (``app/api/alerts.py``) reads and changes them through
the other functions here. The functions only touch the database: **publishing events is the
caller's job** (``AlertRaised`` when :attr:`AlertUpsert.raised` is true, ``AlertResolved`` for
each id returned by :func:`resolve_alert` / :func:`resolve_by_key`).

Dedupe
------
``dedupe_key`` identifies "the same problem" (e.g. ``"stockout_risk:sku:NOICHIEN2L"``). While
an alert with that key is *live* (``open | acknowledged | snoozed``), ``create_alert`` updates
it in place (title, detail, severity, impact, ``last_seen_at``, ``occurrences += 1``) instead
of inserting a new row. It is re-raised (``raised = True``) only when:

* it is new (no live row with that key), or
* the live row was ``snoozed`` and ``snoozed_until`` has passed, or
* the new severity is higher than the stored one (an acknowledged alert goes back to ``open``).

After ``resolved`` a new occurrence creates a new row. Without ``dedupe_key`` the default key
is ``f"{kind}:{target_kind}:{target_id}"``.

Statuses: ``open | acknowledged | snoozed | resolved | hidden_by_budget`` (10 §3).
All timestamps are stored in UTC (``TIMESTAMPTZ``); the API serialises them with ``+07:00``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import duckdb
from pydantic import BaseModel

from app.core.errors import NotFound, StateConflict, ValidationFailed
from app.core.ids import new_id
from app.core.timeutil import utcnow

AlertKind = Literal[
    "stockout_risk",
    "sales_below_forecast",
    "sales_above_forecast",
    "drift",
    "budget_exhausted",
    "data_stale",
    "data_blocking",
    "recommendation_changed",
    "compute_failed",
    "ai_policy",
]
ALERT_KINDS: frozenset[str] = frozenset(AlertKind.__args__)  # type: ignore[attr-defined]
Severity = Literal["high", "medium", "low"]
SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
AlertStatus = Literal["open", "acknowledged", "snoozed", "resolved", "hidden_by_budget"]
LIVE_STATUSES: tuple[str, ...] = ("open", "acknowledged", "snoozed")
DEFAULT_SHOP = "shop-demo"

Scope = tuple[str | None, str | None] | str | None
"""Target of an alert: ``("sku", "NOICHIEN2L")``, ``("campaign", "CD002")``, the string
``"sku:NOICHIEN2L"`` (split on the first ``:``), ``"shop"`` or ``None`` (shop-wide)."""


class Alert(BaseModel):
    id: str
    shop_id: str | None = None
    kind: str
    severity: str
    target_kind: str | None = None
    target_id: str | None = None
    group_key: str | None = None
    title: str
    detail: Any = None
    impact_vnd: int | None = None
    status: str
    raised_at: datetime | None = None
    updated_at: datetime | None = None
    last_seen_at: datetime | None = None
    occurrences: int = 1
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    snoozed_until: datetime | None = None
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    source_event_id: str | None = None
    dedupe_key: str | None = None
    investigation: Any = None


@dataclass(frozen=True)
class AlertUpsert:
    """Result of :func:`create_alert`. ``raised``: the caller should publish
    ``AlertRaised(alert_id=alert.id)`` (new, re-opened after snooze, or escalated)."""

    alert: Alert
    created: bool
    raised: bool


_COLS = (
    "id, shop_id, kind, severity, target_kind, target_id, group_key, title, detail,"
    " impact_vnd, status, raised_at, updated_at, last_seen_at, occurrences, resolved_at,"
    " resolved_by, snoozed_until, acknowledged_by, acknowledged_at, source_event_id,"
    " dedupe_key, investigation"
)
_NAMES = [c.strip() for c in _COLS.split(",")]


def _load(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _row(r: tuple[Any, ...]) -> Alert:
    d = dict(zip(_NAMES, r, strict=True))
    d["detail"] = _load(d["detail"])
    d["investigation"] = _load(d["investigation"])
    d["occurrences"] = int(d["occurrences"] or 1)
    return Alert(**d)


def _dump(v: Any) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False, default=str)


def parse_scope(scope: Scope) -> tuple[str | None, str | None]:
    if scope is None:
        return None, None
    if isinstance(scope, str):
        if scope in ("", "shop"):
            return "shop", None
        head, _, tail = scope.partition(":")
        return head or None, tail or None
    return scope[0], scope[1]


def default_dedupe_key(kind: str, target_kind: str | None, target_id: str | None) -> str:
    return f"{kind}:{target_kind or 'shop'}:{target_id or '*'}"


# ---------------------------------------------------------------------- write
def create_alert(
    conn: duckdb.DuckDBPyConnection,
    kind: str,
    scope: Scope,
    severity: str,
    title: str,
    detail: Any = None,
    dedupe_key: str | None = None,
    *,
    impact_vnd: int | None = None,
    group_key: str | None = None,
    source_event_id: str | None = None,
    shop_id: str = DEFAULT_SHOP,
    now: datetime | None = None,
) -> AlertUpsert:
    """Raise (or refresh) an alert; dedupe on ``dedupe_key``. Never publishes events.

    Args:
        conn: DuckDB connection or cursor (the caller's write connection).
        kind: one of :data:`ALERT_KINDS` (``stockout_risk``, ``sales_below_forecast``, …);
            anything else → ``ValidationFailed``.
        scope: target, ``("sku", "NOICHIEN2L")`` / ``"campaign:CD002"`` / ``None`` (shop).
        severity: ``high | medium | low``.
        title: Vietnamese one-liner shown in the alert list.
        detail: JSON-serialisable dict (numbers, sources); stored as JSON.
        dedupe_key: "same problem" key; default ``f"{kind}:{target_kind}:{target_id}"``.
        impact_vnd: estimated money impact (sort key on the Cảnh báo screen).
        group_key: grouping on the UI (e.g. product id ``"AOGIUNHIET"``).
        source_event_id: id of the bus event that triggered it.
        shop_id: default ``"shop-demo"``.
        now: clock override (UTC-aware), for tests / data-clock callers.

    Returns:
        :class:`AlertUpsert` ``(alert, created, raised)``.
    """
    if kind not in ALERT_KINDS:
        raise ValidationFailed(f"Loại cảnh báo không hợp lệ: {kind}.", kind=kind)
    if severity not in SEVERITY_RANK:
        raise ValidationFailed(f"Mức độ không hợp lệ: {severity}.", severity=severity)
    target_kind, target_id = parse_scope(scope)
    key = dedupe_key or default_dedupe_key(kind, target_kind, target_id)
    ts = now or utcnow()
    row = conn.execute(
        f"SELECT {_COLS} FROM alerts WHERE dedupe_key = ? AND coalesce(shop_id, ?) = ?"
        " AND status IN ('open', 'acknowledged', 'snoozed', 'hidden_by_budget')"
        " ORDER BY raised_at DESC LIMIT 1",
        [key, shop_id, shop_id],
    ).fetchone()
    if row is None:
        aid = new_id("al")
        conn.execute(
            f"INSERT INTO alerts ({_COLS}) VALUES"
            " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, 1, NULL, NULL, NULL, NULL, NULL,"
            " ?, ?, NULL)",
            [
                aid,
                shop_id,
                kind,
                severity,
                target_kind,
                target_id,
                group_key,
                title,
                _dump(detail),
                impact_vnd,
                ts,
                ts,
                ts,
                source_event_id,
                key,
            ],
        )
        return AlertUpsert(get_alert(conn, aid), created=True, raised=True)

    old = _row(row)
    status = old.status
    raised = False
    if status == "snoozed" and (old.snoozed_until is None or old.snoozed_until <= ts):
        status, raised = "open", True
    if SEVERITY_RANK[severity] > SEVERITY_RANK.get(old.severity, 0):
        status, raised = "open", True
    conn.execute(
        "UPDATE alerts SET severity = ?, title = ?, detail = ?, impact_vnd = coalesce(?,"
        " impact_vnd), group_key = coalesce(?, group_key), status = ?, updated_at = ?,"
        " last_seen_at = ?, occurrences = coalesce(occurrences, 1) + 1,"
        " source_event_id = coalesce(?, source_event_id),"
        " snoozed_until = CASE WHEN ? = 'open' THEN NULL ELSE snoozed_until END WHERE id = ?",
        [
            severity,
            title,
            _dump(detail) if detail is not None else _dump(old.detail),
            impact_vnd,
            group_key,
            status,
            ts,
            ts,
            source_event_id,
            status,
            old.id,
        ],
    )
    return AlertUpsert(get_alert(conn, old.id), created=False, raised=raised)


def ack_alert(
    conn: duckdb.DuckDBPyConnection, alert_id: str, user_id: str, *, now: datetime | None = None
) -> Alert:
    """``open | snoozed`` → ``acknowledged`` (idempotent on ``acknowledged``)."""
    a = get_alert(conn, alert_id)
    if a.status == "acknowledged":
        return a
    if a.status not in ("open", "snoozed", "hidden_by_budget"):
        raise StateConflict(
            f"Cảnh báo đang ở trạng thái '{a.status}', không xác nhận được.", status=a.status
        )
    ts = now or utcnow()
    conn.execute(
        "UPDATE alerts SET status = 'acknowledged', acknowledged_by = ?, acknowledged_at = ?,"
        " snoozed_until = NULL, updated_at = ? WHERE id = ?",
        [user_id, ts, ts, alert_id],
    )
    return get_alert(conn, alert_id)


def snooze_alert(
    conn: duckdb.DuckDBPyConnection,
    alert_id: str,
    user_id: str,
    minutes: int,
    *,
    now: datetime | None = None,
) -> Alert:
    """Hide a live alert for ``minutes`` (1..1440); ``create_alert`` re-opens it afterwards."""
    if not 1 <= minutes <= 1440:
        raise ValidationFailed("Thời gian tạm ẩn phải từ 1 đến 1440 phút.", minutes=minutes)
    a = get_alert(conn, alert_id)
    if a.status not in LIVE_STATUSES and a.status != "hidden_by_budget":
        raise StateConflict(
            f"Cảnh báo đang ở trạng thái '{a.status}', không tạm ẩn được.", status=a.status
        )
    ts = now or utcnow()
    conn.execute(
        "UPDATE alerts SET status = 'snoozed', snoozed_until = ?, acknowledged_by ="
        " coalesce(acknowledged_by, ?), updated_at = ? WHERE id = ?",
        [ts + timedelta(minutes=minutes), user_id, ts, alert_id],
    )
    return get_alert(conn, alert_id)


def resolve_alert(
    conn: duckdb.DuckDBPyConnection,
    alert_id: str,
    by: str = "system",
    *,
    note: str | None = None,
    now: datetime | None = None,
) -> Alert:
    """Live → ``resolved`` (``by`` = user id or ``"system"``); idempotent on ``resolved``.
    ``note`` is kept in ``detail.resolution_note``."""
    a = get_alert(conn, alert_id)
    if a.status == "resolved":
        return a
    ts = now or utcnow()
    detail = a.detail
    if note:
        detail = {
            **(detail if isinstance(detail, dict) else {} if detail is None else {"value": detail}),
            "resolution_note": note,
        }
    conn.execute(
        "UPDATE alerts SET status = 'resolved', resolved_at = ?, resolved_by = ?, detail = ?,"
        " updated_at = ? WHERE id = ?",
        [ts, by, _dump(detail), ts, alert_id],
    )
    return get_alert(conn, alert_id)


def resolve_by_key(
    conn: duckdb.DuckDBPyConnection,
    dedupe_key: str,
    by: str = "system",
    *,
    shop_id: str = DEFAULT_SHOP,
    now: datetime | None = None,
) -> list[str]:
    """Resolve every live alert with ``dedupe_key`` (the condition cleared). Returns the
    resolved ids (publish ``AlertResolved`` for each)."""
    ids = [
        str(r[0])
        for r in conn.execute(
            "SELECT id FROM alerts WHERE dedupe_key = ? AND coalesce(shop_id, ?) = ?"
            " AND status IN ('open', 'acknowledged', 'snoozed', 'hidden_by_budget')",
            [dedupe_key, shop_id, shop_id],
        ).fetchall()
    ]
    for aid in ids:
        resolve_alert(conn, aid, by, now=now)
    return ids


def set_status(
    conn: duckdb.DuckDBPyConnection, alert_id: str, status: str, *, now: datetime | None = None
) -> Alert:
    """Low-level status change for the orchestrator (e.g. ``hidden_by_budget`` ↔ ``open``)."""
    if status not in ("open", "acknowledged", "snoozed", "resolved", "hidden_by_budget"):
        raise ValidationFailed(f"Trạng thái cảnh báo không hợp lệ: {status}.", status=status)
    get_alert(conn, alert_id)
    conn.execute(
        "UPDATE alerts SET status = ?, updated_at = ? WHERE id = ?",
        [status, now or utcnow(), alert_id],
    )
    return get_alert(conn, alert_id)


def set_investigation(conn: duckdb.DuckDBPyConnection, alert_id: str, investigation: Any) -> Alert:
    """Store the investigation agent result (07 §4.3) on the alert."""
    get_alert(conn, alert_id)
    conn.execute(
        "UPDATE alerts SET investigation = ?, updated_at = ? WHERE id = ?",
        [_dump(investigation), utcnow(), alert_id],
    )
    return get_alert(conn, alert_id)


# ---------------------------------------------------------------------- read
def get_alert(conn: duckdb.DuckDBPyConnection, alert_id: str) -> Alert:
    row = conn.execute(f"SELECT {_COLS} FROM alerts WHERE id = ?", [alert_id]).fetchone()
    if row is None:
        raise NotFound("Không có cảnh báo này.", alert_id=alert_id)
    return _row(row)


def list_alerts(
    conn: duckdb.DuckDBPyConnection,
    *,
    status: str | list[str] | None = None,
    kind: str | None = None,
    severity: str | None = None,
    target_kind: str | None = None,
    target_id: str | None = None,
    shop_id: str | None = None,
    since: datetime | None = None,
    now: datetime | None = None,
) -> list[Alert]:
    """Alerts, highest severity first, then biggest impact, then newest.

    ``status``: a status, a list, or ``"live"`` (= open/acknowledged/snoozed). A snoozed alert
    whose ``snoozed_until`` passed is reported as ``open`` (``now`` = clock, default wall)."""
    where: list[str] = []
    args: list[Any] = []
    if status is not None:
        statuses = (
            list(LIVE_STATUSES)
            if status == "live"
            else ([status] if isinstance(status, str) else list(status))
        )
        ts = now or utcnow()
        # a snoozed alert past its snooze reads as open
        where.append(
            "(CASE WHEN status = 'snoozed' AND snoozed_until <= ? THEN 'open' ELSE status END)"
            f" IN ({', '.join('?' for _ in statuses)})"
        )
        args += [ts, *statuses]
    for col, val in (
        ("kind", kind),
        ("severity", severity),
        ("target_kind", target_kind),
        ("target_id", target_id),
        ("shop_id", shop_id),
    ):
        if val is not None:
            where.append(f"{col} = ?")
            args.append(val)
    if since is not None:
        where.append("raised_at >= ?")
        args.append(since)
    sql = f"SELECT {_COLS} FROM alerts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += (
        " ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,"
        " coalesce(impact_vnd, 0) DESC, raised_at DESC, id"
    )
    out = [_row(r) for r in conn.execute(sql, args).fetchall()]
    ts = now or utcnow()
    for a in out:
        if a.status == "snoozed" and a.snoozed_until is not None and a.snoozed_until <= ts:
            a.status = "open"
    return out


def count_open(conn: duckdb.DuckDBPyConnection, *, target_id: str | None = None) -> int:
    sql = "SELECT count(*) FROM alerts WHERE status = 'open'"
    args: list[Any] = []
    if target_id is not None:
        sql += " AND target_id = ?"
        args.append(target_id)
    row = conn.execute(sql, args).fetchone()
    return int(row[0]) if row else 0
