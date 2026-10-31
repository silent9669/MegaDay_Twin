"""``GET /api/v1/health`` (01 "Tiêu chí hoàn thành", 08 §2 "Hệ thống"). No auth."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app import __version__
from app.api.schemas import Envelope, Meta
from app.core import messages_vi as vi
from app.core.config import Settings
from app.core.timeutil import utcnow
from app.warehouse.db import Database

router = APIRouter(tags=["system"])


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    db: Literal["ok", "error"]
    ai: Literal["ok", "disabled"]
    ai_mode: Literal["live", "record", "replay"]
    app_env: str
    version: str
    message: str


@router.get("/health", response_model=Envelope[Health], summary="Trạng thái hệ thống")
def health(request: Request, response: Response) -> Envelope[Health]:
    settings: Settings = request.app.state.settings
    db: Database = request.app.state.db
    db_ok = db.ping()
    ai: Literal["ok", "disabled"] = "ok" if settings.ai_available else "disabled"
    status: Literal["ok", "degraded"] = "ok" if db_ok else "degraded"
    if not db_ok:
        response.status_code = 503
    return Envelope[Health](
        data=Health(
            status=status,
            db="ok" if db_ok else "error",
            ai=ai,
            ai_mode=settings.ai_mode,
            app_env=settings.app_env,
            version=__version__,
            message=vi.HEALTH_OK if db_ok else vi.HEALTH_DEGRADED,
        ),
        meta=Meta(computed_at=utcnow()),
    )
