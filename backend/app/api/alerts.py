"""Alerts (API-06, 08 §3.11): read and handle the rows written by
:func:`app.governance.alerts.create_alert` (orchestrator ``AlertEvaluator``, AI guard…).

* ``GET /alerts?status=open|live|acknowledged|snoozed|resolved&kind=&severity=&sku=&campaign_id=``
  (viewer): highest severity, then impact, then newest. ``status=live`` = open +
  acknowledged + snoozed; a snoozed alert past ``snoozed_until`` reads as ``open``.
* ``GET /alerts/{id}`` (viewer).
* ``POST /alerts/{id}/ack`` · ``/snooze`` ``{"minutes": 60}`` · ``/resolve`` ``{"note": ...}``
  (operator). ``resolve`` publishes ``AlertResolved``.

``POST /alerts/{id}/investigate`` (operator, 07 §4.3): added by the AI layer, runs
:func:`app.api.ai.investigate_alert` (checklist + summary, stored on the alert).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.ai import ChatTurn, investigate_alert
from app.api.common import MAX_LIMIT, data_as_of, paginate
from app.api.deps import Conn, User, Viewer, publish, require_permission
from app.api.schemas import Envelope, LocalDatetime, Meta, Page, SourceRef
from app.core.events import AlertResolved
from app.core.timeutil import utcnow
from app.governance import alerts as gov
from app.governance.alerts import Alert

router = APIRouter(tags=["alerts"])

CanAck = Annotated[User, Depends(require_permission("alert.ack"))]
CanSnooze = Annotated[User, Depends(require_permission("alert.snooze"))]
CanResolve = Annotated[User, Depends(require_permission("alert.resolve"))]


class AlertTarget(BaseModel):
    kind: str | None = None
    id: str | None = None
    sku: str | None = None
    campaign_id: str | None = None
    product: str | None = None


class AlertOut(Alert):
    target: AlertTarget = Field(default_factory=AlertTarget)
    raised_at: LocalDatetime | None = None
    updated_at: LocalDatetime | None = None
    last_seen_at: LocalDatetime | None = None
    resolved_at: LocalDatetime | None = None
    snoozed_until: LocalDatetime | None = None
    acknowledged_at: LocalDatetime | None = None


class SnoozeIn(BaseModel):
    minutes: int = Field(default=60, ge=1, le=1440)


class ResolveIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


def _product_names(conn: Any, skus: list[str]) -> dict[str, str]:
    if not skus:
        return {}
    try:
        rows = conn.execute(
            "SELECT s.sku_id, p.name FROM skus s JOIN products p USING (product_id)"
            f" WHERE s.sku_id IN ({', '.join('?' for _ in skus)})",
            skus,
        ).fetchall()
    except Exception:  # schema drift must not break the alert list
        return {}
    return {str(r[0]): str(r[1]) for r in rows}


def _out(alerts: list[Alert], conn: Any) -> list[AlertOut]:
    names = _product_names(
        conn, sorted({a.target_id for a in alerts if a.target_kind == "sku" and a.target_id})
    )
    out = []
    for a in alerts:
        t = AlertTarget(kind=a.target_kind, id=a.target_id)
        if a.target_kind == "sku":
            t.sku = a.target_id
            t.product = names.get(a.target_id or "")
        elif a.target_kind == "campaign":
            t.campaign_id = a.target_id
        out.append(AlertOut(**a.model_dump(), target=t))
    return out


def _meta(conn: Any, a: Alert | None = None) -> Meta:
    src = [SourceRef(id=a.id, kind="alert", label=a.title)] if a else []
    return Meta(data_as_of=data_as_of(conn), computed_at=utcnow(), sources=src)


@router.get("/alerts", response_model=Page[AlertOut], summary="Cảnh báo")
def list_alerts(
    conn: Conn,
    _user: Viewer,
    status: str | None = None,
    kind: str | None = None,
    severity: str | None = None,
    sku: str | None = None,
    campaign_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    cursor: str | None = None,
) -> Page[AlertOut]:
    statuses: str | list[str] | None = status
    if status and "," in status:
        statuses = [s for s in status.split(",") if s]
    target_kind = "sku" if sku else "campaign" if campaign_id else None
    items = gov.list_alerts(
        conn,
        status=statuses,
        kind=kind,
        severity=severity,
        target_kind=target_kind,
        target_id=sku or campaign_id,
    )
    page, nxt = paginate(items, limit, cursor)
    meta = _meta(conn)
    meta.total = len(items)
    return Page[AlertOut](data=_out(page, conn), meta=meta, next_cursor=nxt)


@router.get("/alerts/{alert_id}", response_model=Envelope[AlertOut], summary="Một cảnh báo")
def get_alert(alert_id: str, conn: Conn, _user: Viewer) -> Envelope[AlertOut]:
    a = gov.get_alert(conn, alert_id)
    return Envelope[AlertOut](data=_out([a], conn)[0], meta=_meta(conn, a))


def _audit(
    conn: Any, request: Request, user: User, action: str, before: Alert, after: Alert
) -> None:
    from app.governance.audit import write_audit

    write_audit(
        conn,
        user.actor(request),
        action,
        "alert",
        after.id,
        before={"status": before.status},
        after={"status": after.status},
    )


@router.post("/alerts/{alert_id}/ack", response_model=Envelope[AlertOut], summary="Đã xem")
def ack(alert_id: str, conn: Conn, user: CanAck, request: Request) -> Envelope[AlertOut]:
    before = gov.get_alert(conn, alert_id)
    a = gov.ack_alert(conn, alert_id, user.id)
    _audit(conn, request, user, "alert.ack", before, a)
    return Envelope[AlertOut](data=_out([a], conn)[0], meta=_meta(conn, a))


@router.post("/alerts/{alert_id}/snooze", response_model=Envelope[AlertOut], summary="Tạm ẩn")
def snooze(
    alert_id: str,
    conn: Conn,
    user: CanSnooze,
    request: Request,
    body: SnoozeIn | None = None,
) -> Envelope[AlertOut]:
    before = gov.get_alert(conn, alert_id)
    a = gov.snooze_alert(conn, alert_id, user.id, (body or SnoozeIn()).minutes)
    _audit(conn, request, user, "alert.snooze", before, a)
    return Envelope[AlertOut](data=_out([a], conn)[0], meta=_meta(conn, a))


@router.post("/alerts/{alert_id}/resolve", response_model=Envelope[AlertOut], summary="Đã xử lý")
def resolve(
    alert_id: str,
    conn: Conn,
    user: CanResolve,
    request: Request,
    body: ResolveIn | None = None,
) -> Envelope[AlertOut]:
    before = gov.get_alert(conn, alert_id)
    a = gov.resolve_alert(conn, alert_id, user.id, note=body.note if body else None)
    _audit(conn, request, user, "alert.resolve", before, a)
    if before.status != "resolved":
        publish(request, AlertResolved(alert_id=a.id))
    return Envelope[AlertOut](data=_out([a], conn)[0], meta=_meta(conn, a))


# AI layer (M4 "AI ra API"): investigation agent on an alert.
CanInvestigate = Annotated[User, Depends(require_permission("alert.investigate"))]


@router.post(
    "/alerts/{alert_id}/investigate",
    response_model=Envelope[ChatTurn],
    summary="Điều tra cảnh báo (checklist 6 bước + tóm tắt)",
)
def investigate(
    alert_id: str, conn: Conn, user: CanInvestigate, request: Request
) -> Envelope[ChatTurn]:
    return investigate_alert(conn, request, user, alert_id)
