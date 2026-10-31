"""``GET /me``, ``GET /freshness`` (API-01, 08 §3.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.api.common import SourceFreshness, data_as_of, freshness, reference_now
from app.api.deps import ROLE_LABEL_VI, Conn, Viewer
from app.api.schemas import Envelope, LocalDatetime, Meta
from app.core.config import Settings
from app.core.timeutil import to_utc, utcnow

router = APIRouter(tags=["system"])


class Me(BaseModel):
    id: str
    role: str
    role_label: str
    can_run_scenarios: bool
    can_edit_goals: bool


@router.get("/me", response_model=Envelope[Me], summary="Người dùng hiện tại")
def me(user: Viewer) -> Envelope[Me]:
    return Envelope[Me](
        data=Me(
            id=user.id,
            role=user.role,
            role_label=ROLE_LABEL_VI[user.role],
            can_run_scenarios=user.at_least("operator"),
            can_edit_goals=user.at_least("lead"),
        ),
        meta=Meta(computed_at=utcnow()),
    )


class Freshness(BaseModel):
    now: LocalDatetime
    clock: str  # "data" (demo: now = latest data mark) | "wall" | "query" (?now=)
    data_as_of: LocalDatetime | None
    sources: list[SourceFreshness]


@router.get("/freshness", response_model=Envelope[Freshness], summary="Độ mới từng nguồn")
def get_freshness(
    request: Request,
    conn: Conn,
    _user: Viewer,
    now: Annotated[
        datetime | None, Query(description="Mốc so sánh (mặc định: đồng hồ dữ liệu khi demo)")
    ] = None,
) -> Envelope[Freshness]:
    settings: Settings = request.app.state.settings
    ref = to_utc(now) if now is not None else reference_now(conn, settings.demo_mode)
    items = freshness(conn, ref)
    as_of = data_as_of(conn)
    return Envelope[Freshness](
        data=Freshness(
            now=ref,
            clock="query" if now is not None else ("data" if settings.demo_mode else "wall"),
            data_as_of=as_of,
            sources=items,
        ),
        meta=Meta(data_as_of=as_of, computed_at=utcnow(), compute_state="fresh"),
    )
