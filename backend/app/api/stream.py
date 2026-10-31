"""``GET /stream`` — one SSE connection per app (API-07, 08 §4; role viewer, GOV-02).

``Last-Event-ID`` (header, or ``?last_event_id=`` for clients that cannot set headers)
replays the missed events still in the 500-event ring. A ``heartbeat`` every 15 s keeps
proxies from closing the connection. ``?limit=`` / ``?idle_timeout=`` end the stream early
(curl, tests).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import Viewer
from app.core.errors import StateConflict
from app.orchestrator.runtime import Runtime

router = APIRouter(tags=["stream"])


@router.get(
    "/stream",
    summary="Luồng sự kiện thời gian thực (SSE)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream(
    request: Request,
    _user: Viewer,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id_q: Annotated[int | None, Query(alias="last_event_id", ge=0)] = None,
    limit: Annotated[int | None, Query(ge=1, le=10_000)] = None,
    idle_timeout: Annotated[float | None, Query(gt=0, le=3600)] = None,
) -> StreamingResponse:
    rt: Runtime | None = getattr(request.app.state, "runtime", None)
    if rt is None:  # pragma: no cover
        raise StateConflict("Luồng sự kiện chưa khởi động.")
    last: int | None = last_event_id_q
    if last_event_id and last_event_id.strip().isdigit():
        last = int(last_event_id.strip())
    gen = rt.hub.subscribe(last, limit=limit, idle_timeout=idle_timeout)
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
