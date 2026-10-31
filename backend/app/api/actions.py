"""Simulated actions (API-06, 08 §3.10, 10 §2 "Kiểm lại thao tác").

* ``GET /actions`` / ``GET /actions/{id}`` (viewer).
* ``POST /actions/{id}/verify`` (operator): ``{"result": "verified"}`` = "Đánh dấu đã kiểm
  lại" (``verified_by_human = true``); ``{"result": "failed", "note": "..."}``; or observed
  values (``observed_status``, ``observed_budget_vnd``…) compared with the action.

Sending (approved → sent) is ``POST /decisions/{id}/actions`` (app/api/decisions.py).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.common import MAX_LIMIT, data_as_of, paginate
from app.api.decisions import ActionOut, CanVerify, SentOut, VerifyIn, verify_action_impl
from app.api.deps import Conn, Viewer
from app.api.schemas import Envelope, Meta, Page, SourceRef
from app.core.timeutil import utcnow
from app.governance import decisions as gov

router = APIRouter(tags=["actions"])


@router.get("/actions", response_model=Page[ActionOut], summary="Thao tác đã gửi (mô phỏng)")
def list_actions(
    conn: Conn,
    _user: Viewer,
    state: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    cursor: str | None = None,
) -> Page[ActionOut]:
    items = gov.list_actions(conn, state=state)
    page, nxt = paginate(items, limit, cursor)
    return Page[ActionOut](
        data=[ActionOut.model_validate(a.model_dump()) for a in page],
        meta=Meta(data_as_of=data_as_of(conn), computed_at=utcnow(), total=len(items)),
        next_cursor=nxt,
    )


@router.get("/actions/{action_id}", response_model=Envelope[ActionOut], summary="Một thao tác")
def get_action(action_id: str, conn: Conn, _user: Viewer) -> Envelope[ActionOut]:
    a = gov.get_action(conn, action_id)
    return Envelope[ActionOut](
        data=ActionOut.model_validate(a.model_dump()),
        meta=Meta(
            data_as_of=data_as_of(conn),
            computed_at=utcnow(),
            sources=[SourceRef(id=a.decision_id, kind="decision", label="Quyết định")],
        ),
    )


@router.post(
    "/actions/{action_id}/verify",
    response_model=Envelope[SentOut],
    summary="Kiểm lại thao tác",
)
def verify(
    action_id: str, body: VerifyIn, conn: Conn, user: CanVerify, request: Request
) -> Envelope[SentOut]:
    return verify_action_impl(action_id, body, conn, user, request)
