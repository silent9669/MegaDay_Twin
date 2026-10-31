"""``GET /live/status``, ``POST /live/control`` (API-07, 08 §3.13; roles GOV-02).

``/live/control`` only works with ``DEMO_MODE=true`` (409 otherwise) and needs the
``demo.control`` permission (lead). Commands:

* ``{"command": "start", "from_hour": 9, "interval_seconds": 10}`` — replay 11.11 hour by
  hour into ``LIVE_DIR`` (Python twin of ``replay.js``);
* ``{"command": "stop"}``;
* ``{"command": "jump", "hour": 13}`` — data up to ``13:59`` at once, then recompute
  (``"wait": true`` waits for the recompute and the alerts before answering);
* ``{"command": "reset"}`` — remove every live batch (history only).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.common import data_as_of
from app.api.deps import Conn, User, Viewer, audit, require_permission
from app.api.schemas import Envelope, LocalDatetime, Meta
from app.core.config import Settings
from app.core.errors import StateConflict, ValidationFailed
from app.core.timeutil import to_local, utcnow
from app.orchestrator.runtime import Runtime

router = APIRouter(tags=["live"])
DemoLead = Annotated[User, Depends(require_permission("demo.control"))]


class LiveStatus(BaseModel):
    enabled: bool
    watching: bool
    day: date | None
    orders_until: str | None  # "14:59"
    ads_until: str | None
    last_tick_at: LocalDatetime | None  # wall clock of the last load
    replay_running: bool
    replay_hour: int | None
    from_hour: int | None
    interval_seconds: float | None
    data_as_of: LocalDatetime | None
    recompute_busy: bool
    recompute_runs: int
    live_dir: str
    last_error: str | None = None
    last_load: dict[str, object] | None = None


def _runtime(request: Request) -> Runtime:
    rt: Runtime | None = getattr(request.app.state, "runtime", None)
    if rt is None:  # pragma: no cover - lifespan always sets it
        raise StateConflict("Chế độ live chưa khởi động.")
    return rt


def _hm(t: datetime | None) -> str | None:
    return None if t is None else to_local(t).strftime("%H:%M")


def live_status(rt: Runtime, as_of: datetime | None) -> LiveStatus:
    w, c = rt.watcher, rt.control.status()
    tick = w.last_tick
    return LiveStatus(
        enabled=rt.settings.live_enabled,
        watching=w.running,
        day=tick.day if tick else None,
        orders_until=_hm(tick.orders_inventory_until) if tick else None,
        ads_until=_hm(tick.ads_until) if tick else None,
        last_tick_at=w.last_tick_at,
        replay_running=bool(c["replay_running"]),
        replay_hour=c["replay_hour"],
        from_hour=c["from_hour"],
        interval_seconds=c["interval_seconds"],
        data_as_of=as_of,
        recompute_busy=rt.orchestrator.busy,
        recompute_runs=rt.orchestrator.runs,
        live_dir=str(w.root),
        last_error=w.last_error,
        last_load=w.last_report,
    )


@router.get("/live/status", response_model=Envelope[LiveStatus], summary="Trạng thái live")
def get_live_status(request: Request, conn: Conn, _user: Viewer) -> Envelope[LiveStatus]:
    as_of = data_as_of(conn)
    return Envelope[LiveStatus](
        data=live_status(_runtime(request), as_of),
        meta=Meta(data_as_of=as_of, computed_at=utcnow()),
    )


class LiveControl(BaseModel):
    command: Literal["start", "stop", "reset", "jump"]
    from_hour: int = Field(default=0, ge=0, le=23)
    interval_seconds: float = Field(default=20.0, ge=0.5, le=600)
    hour: int | None = Field(default=None, ge=0, le=23)
    wait: bool = False


@router.post("/live/control", response_model=Envelope[LiveStatus], summary="Điều khiển demo")
async def live_control(body: LiveControl, request: Request, user: DemoLead) -> Envelope[LiveStatus]:
    """Async on purpose: the replay task lives on the event loop."""
    settings: Settings = request.app.state.settings
    if not settings.demo_mode:
        raise StateConflict("Chỉ điều khiển phát lại được khi DEMO_MODE=true.")
    rt = _runtime(request)
    try:
        if body.command == "start":
            await rt.control.start(body.from_hour, body.interval_seconds)
        elif body.command == "stop":
            await rt.control.stop()
        elif body.command == "reset":
            await rt.control.reset()
        else:
            if body.hour is None:
                raise ValidationFailed("Lệnh jump cần 'hour' (0–23).")
            await rt.control.jump(body.hour, wait=body.wait)
    except FileNotFoundError as e:
        raise StateConflict(str(e)) from e
    with rt.db.reader() as cur:
        audit(cur, request, user, f"live.{body.command}", "live", body.command,
              after=body.model_dump())  # fmt: skip
        as_of = data_as_of(cur)
    return Envelope[LiveStatus](
        data=live_status(rt, as_of), meta=Meta(data_as_of=as_of, computed_at=utcnow())
    )
