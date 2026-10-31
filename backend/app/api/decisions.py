"""Decisions + audit log (API-06, 08 §3.10, 10 §2–3, 10 §9).

* ``POST /decisions`` (operator): draft from a stored scenario. ``action`` omitted → the
  recommendation's best action ("tạo quyết định từ đề xuất"); ``scenario_id`` omitted →
  the recommendation's scenario. 201 with the full snapshot.
* ``GET /decisions`` / ``GET /decisions/{id}`` (viewer): the Nhật ký. Detail adds the
  simulated ``actions`` and ``can`` = events the caller may try now (UI buttons).
* Transitions: ``submit`` / ``reopen`` / ``cancel`` (creator), ``approve`` / ``reject``
  (lead, never the creator → 403), ``actions`` (approved → sent, simulated), ``verify``
  (sent → verified / failed, "Đánh dấu đã kiểm lại"). Invalid state → 409
  ``STATE_CONFLICT``; stale inputs → 409 ``STALE_INPUT``; blocking data issue → 422.
* ``GET /audit`` (lead): ``audit_log`` rows, newest first.

Every change publishes ``DecisionRecorded``; the governance service writes the audit row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.common import MAX_LIMIT, data_as_of, paginate
from app.api.deps import Conn, User, Viewer, publish, require_permission
from app.api.schemas import Envelope, LocalDatetime, Meta, Page, SourceRef
from app.core.errors import StateConflict, ValidationFailed
from app.core.events import DecisionRecorded
from app.core.timeutil import utcnow
from app.governance import decisions as gov
from app.governance.audit import list_audit
from app.governance.decisions import ActionRecord, Decision

router = APIRouter(tags=["decisions"])

CanCreate = Annotated[User, Depends(require_permission("decision.create"))]
CanSubmit = Annotated[User, Depends(require_permission("decision.submit"))]
CanApprove = Annotated[User, Depends(require_permission("decision.approve"))]
CanSend = Annotated[User, Depends(require_permission("action.send"))]
CanVerify = Annotated[User, Depends(require_permission("action.verify"))]
CanAudit = Annotated[User, Depends(require_permission("audit.read"))]


# ---------------------------------------------------------------------- models
class DecisionOut(Decision):
    created_at: LocalDatetime | None = None
    submitted_at: LocalDatetime | None = None
    approved_at: LocalDatetime | None = None
    valid_until: LocalDatetime | None = None


class ActionOut(ActionRecord):
    sent_at: LocalDatetime | None = None
    verified_at: LocalDatetime | None = None


class DecisionDetail(DecisionOut):
    actions: list[ActionOut] = Field(default_factory=list)
    can: list[str] = Field(default_factory=list, description="Sự kiện người gọi được thử")


class SentOut(BaseModel):
    decision: DecisionDetail
    action: ActionOut


class Target(BaseModel):
    campaign_id: str | None = None


class ActionIn(BaseModel):
    kind: Literal["continue", "reduce_budget", "schedule_hours", "pause", "pause_until_restock"]
    pct: float | None = None
    windows_local: list[tuple[int, int]] | None = None


class DecisionCreate(BaseModel):
    target: Target | None = None
    campaign_id: str | None = None  # [DEV] flat alternative to target.campaign_id
    kind: Literal["ad_action", "handoff_note"] = "ad_action"
    action: ActionIn | None = None
    scenario_id: str | None = None
    recommendation_id: str | None = None
    rationale: str | None = Field(default=None, max_length=4000)
    override_of_recommendation: bool | None = Field(
        default=None, description="Bỏ qua: backend tự tính (hành động chọn ≠ đề xuất)"
    )


class ApproveIn(BaseModel):
    note: str | None = Field(default=None, max_length=2000)


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class SendIn(BaseModel):
    mode: str = "simulated"


class VerifyIn(BaseModel):
    """Manual mark (``result``) or observed values compared with the action."""

    result: Literal["verified", "failed"] | None = None
    note: str | None = Field(default=None, max_length=2000)
    observed_status: str | None = None
    observed_budget_vnd: int | None = None
    observed_spend_vnd: int | None = None
    expected_budget_vnd: int | None = None

    def observed(self) -> dict[str, Any] | None:
        d = self.model_dump(
            include={
                "observed_status",
                "observed_budget_vnd",
                "observed_spend_vnd",
                "expected_budget_vnd",
            },
            exclude_none=True,
        )
        return d or None


class AuditOut(BaseModel):
    id: str
    at: LocalDatetime | None = None
    shop_id: str | None = None
    actor: str | None = None
    actor_role: str | None = None
    via: str | None = None
    action: str | None = None
    target_kind: str | None = None
    target_id: str | None = None
    before: Any = None
    after: Any = None
    request_id: str | None = None


# ---------------------------------------------------------------------- helpers
def _shop(request: Request) -> str:
    settings = getattr(request.app.state, "settings", None)
    return str(getattr(settings, "shop_id", None) or "shop-demo")


def _out(d: Decision) -> DecisionOut:
    return DecisionOut.model_validate(d.model_dump())


def detail(conn: Any, d: Decision, user: User) -> DecisionDetail:
    return DecisionDetail(
        **d.model_dump(),
        actions=[ActionOut.model_validate(a.model_dump()) for a in gov.actions_of(conn, d.id)],
        can=gov.allowed_events(d, user.actor()),
    )


def _meta(conn: Any, d: Decision | None = None) -> Meta:
    sources = []
    if d is not None:
        sources.append(SourceRef(id=d.id, kind="decision", label=f"Quyết định {d.state_label}"))
        if d.scenario_id:
            sources.append(SourceRef(id=d.scenario_id, kind="scenario", label="Kịch bản mô phỏng"))
        if d.recommendation_id:
            sources.append(
                SourceRef(id=d.recommendation_id, kind="recommendation", label="Đề xuất")
            )
    return Meta(data_as_of=data_as_of(conn), computed_at=utcnow(), sources=sources)


def _changed(request: Request, d: Decision) -> None:
    publish(request, DecisionRecorded(decision_id=d.id))


# ---------------------------------------------------------------------- endpoints
@router.post(
    "/decisions",
    response_model=Envelope[DecisionDetail],
    status_code=201,
    summary="Tạo bản nháp quyết định",
)
def create_decision(
    body: DecisionCreate, conn: Conn, user: CanCreate, request: Request
) -> Envelope[DecisionDetail]:
    campaign_id = (body.target.campaign_id if body.target else None) or body.campaign_id
    d = gov.create_decision(
        conn,
        user.actor(request),
        campaign_id=campaign_id,
        scenario_id=body.scenario_id,
        action=body.action.model_dump() if body.action else None,
        recommendation_id=body.recommendation_id,
        rationale=body.rationale,
        kind=body.kind,
        shop_id=_shop(request),
    )
    _changed(request, d)
    return Envelope[DecisionDetail](data=detail(conn, d, user), meta=_meta(conn, d))


@router.get("/decisions", response_model=Page[DecisionOut], summary="Nhật ký quyết định")
def list_decisions(
    conn: Conn,
    _user: Viewer,
    state: Annotated[list[str] | None, Query()] = None,
    campaign_id: str | None = None,
    created_by: str | None = None,
    kind: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    cursor: str | None = None,
) -> Page[DecisionOut]:
    gov.expire_overdue(conn)
    states: list[str] = []
    for s in state or []:
        states += [x for x in s.split(",") if x]
    items = gov.list_decisions(
        conn,
        state=states or None,
        target_id=campaign_id,
        created_by=created_by,
        kind=kind,
        since=from_,
        until=to,
    )
    page, nxt = paginate(items, limit, cursor)
    meta = _meta(conn)
    meta.total = len(items)
    return Page[DecisionOut](data=[_out(d) for d in page], meta=meta, next_cursor=nxt)


@router.get("/decisions/{decision_id}", response_model=Envelope[DecisionDetail], summary="Chi tiết")
def get_decision(decision_id: str, conn: Conn, user: Viewer) -> Envelope[DecisionDetail]:
    gov.expire_overdue(conn)
    d = gov.get_decision(conn, decision_id)
    return Envelope[DecisionDetail](data=detail(conn, d, user), meta=_meta(conn, d))


def _transition(
    conn: Any,
    request: Request,
    user: User,
    decision_id: str,
    event: str,
    **kw: Any,
) -> Envelope[DecisionDetail]:
    d = gov.transition(conn, decision_id, event, user.actor(request), shop_id=_shop(request), **kw)
    _changed(request, d)
    return Envelope[DecisionDetail](data=detail(conn, d, user), meta=_meta(conn, d))


@router.post(
    "/decisions/{decision_id}/submit",
    response_model=Envelope[DecisionDetail],
    summary="Gửi duyệt",
)
def submit(
    decision_id: str, conn: Conn, user: CanSubmit, request: Request
) -> Envelope[DecisionDetail]:
    return _transition(conn, request, user, decision_id, "submit")


@router.post(
    "/decisions/{decision_id}/reopen",
    response_model=Envelope[DecisionDetail],
    summary="Rút về bản nháp",
)
def reopen(
    decision_id: str, conn: Conn, user: CanSubmit, request: Request
) -> Envelope[DecisionDetail]:
    return _transition(conn, request, user, decision_id, "reopen")


@router.post(
    "/decisions/{decision_id}/cancel",
    response_model=Envelope[DecisionDetail],
    summary="Hủy",
)
def cancel(
    decision_id: str, conn: Conn, user: CanSubmit, request: Request
) -> Envelope[DecisionDetail]:
    return _transition(conn, request, user, decision_id, "cancel")


@router.post(
    "/decisions/{decision_id}/approve",
    response_model=Envelope[DecisionDetail],
    summary="Duyệt (lead, không phải người tạo)",
)
def approve(
    decision_id: str,
    conn: Conn,
    user: CanApprove,
    request: Request,
    body: ApproveIn | None = None,
) -> Envelope[DecisionDetail]:
    return _transition(
        conn, request, user, decision_id, "approve", note=body.note if body else None
    )


@router.post(
    "/decisions/{decision_id}/reject",
    response_model=Envelope[DecisionDetail],
    summary="Từ chối (bắt buộc lý do)",
)
def reject(
    decision_id: str, body: RejectIn, conn: Conn, user: CanApprove, request: Request
) -> Envelope[DecisionDetail]:
    return _transition(conn, request, user, decision_id, "reject", reason=body.reason)


@router.post(
    "/decisions/{decision_id}/actions",
    response_model=Envelope[SentOut],
    status_code=201,
    summary="Gửi thao tác (mô phỏng)",
)
def send(
    decision_id: str,
    conn: Conn,
    user: CanSend,
    request: Request,
    body: SendIn | None = None,
) -> Envelope[SentOut]:
    d, act = gov.send_action(
        conn,
        decision_id,
        user.actor(request),
        mode=body.mode if body else "simulated",
        shop_id=_shop(request),
    )
    _changed(request, d)
    return Envelope[SentOut](
        data=SentOut(
            decision=detail(conn, d, user), action=ActionOut.model_validate(act.model_dump())
        ),
        meta=_meta(conn, d),
    )


@router.post(
    "/decisions/{decision_id}/verify",
    response_model=Envelope[SentOut],
    summary="Đánh dấu đã kiểm lại (thao tác mới nhất)",
)
def verify_latest(
    decision_id: str, body: VerifyIn, conn: Conn, user: CanVerify, request: Request
) -> Envelope[SentOut]:
    sent = [a for a in gov.actions_of(conn, decision_id) if a.state == "sent"]
    if not sent:
        gov.get_decision(conn, decision_id)  # 404 first
        raise StateConflict("Quyết định chưa có thao tác đã gửi để kiểm lại.")
    return verify_action_impl(sent[-1].id, body, conn, user, request)


def verify_action_impl(
    action_id: str, body: VerifyIn, conn: Any, user: User, request: Request
) -> Envelope[SentOut]:
    if body.result is None and body.observed() is None:
        raise ValidationFailed("Cần 'result' (verified/failed) hoặc trạng thái quan sát.")
    d, act = gov.verify_action(
        conn,
        action_id,
        user.actor(request),
        result=body.result,
        note=body.note,
        observed=body.observed(),
        shop_id=_shop(request),
    )
    _changed(request, d)
    return Envelope[SentOut](
        data=SentOut(
            decision=detail(conn, d, user), action=ActionOut.model_validate(act.model_dump())
        ),
        meta=_meta(conn, d),
    )


@router.get("/audit", response_model=Page[AuditOut], summary="Nhật ký thao tác (lead)")
def audit_log(
    conn: Conn,
    _user: CanAudit,
    target_kind: str | None = None,
    target_id: str | None = None,
    action: Annotated[str | None, Query(description="Tiền tố, vd 'decision.'")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    cursor: str | None = None,
) -> Page[AuditOut]:
    rows = list_audit(
        conn, target_kind=target_kind, target_id=target_id, action_prefix=action, limit=5000
    )
    page, nxt = paginate(rows, limit, cursor)
    return Page[AuditOut](
        data=[AuditOut(**r) for r in page],
        meta=Meta(computed_at=utcnow(), total=len(rows)),
        next_cursor=nxt,
    )
