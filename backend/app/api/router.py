"""Aggregate router mounted at ``/api/v1``. Each group in 08 §2 adds its own
module here (``app/api/<group>.py``) and is included below."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import actions as actions_api
from app.api import ai as ai_api
from app.api import alerts as alerts_api
from app.api import (
    campaign_analysis,
    campaigns,
    economics,
    health,
    ingest,
    replay_archive,
    scenarios,
    skus,
    system,
)
from app.api import decisions as decisions_api
from app.api import live as live_api
from app.api import stream as stream_api

API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(health.router)
api_router.include_router(system.router)
api_router.include_router(skus.router)
api_router.include_router(campaigns.router)
api_router.include_router(economics.router)
api_router.include_router(scenarios.router)
api_router.include_router(ingest.router)
api_router.include_router(ai_api.router)
api_router.include_router(decisions_api.router)
api_router.include_router(actions_api.router)
api_router.include_router(alerts_api.router)
api_router.include_router(live_api.router)
api_router.include_router(replay_archive.router)
api_router.include_router(stream_api.router)

api_router.include_router(campaign_analysis.router)
