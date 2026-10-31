"""FastAPI entry point: ``uv run uvicorn app.main:app --reload --port 8000``.

Plan: docs/dev-plan/01-kien-truc-va-cong-nghe.md §4, 08-api.md §1.
Lifespan: configure logging → migrate DuckDB → start the EventBus. The live
watcher and APScheduler jobs are attached here by LIVE-01 / ORC-01.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.errors import install_error_handlers
from app.api.router import API_PREFIX, api_router
from app.core.config import Settings, get_settings
from app.core.events import EventBus
from app.core.ids import new_id
from app.core.logging import configure_logging, get_logger
from app.governance.middleware import install_audit_middleware
from app.orchestrator.runtime import Runtime
from app.warehouse.db import Database, get_db

log = get_logger(__name__)


def create_app(settings: Settings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, json=settings.log_json)
        database: Database = db or get_db()
        tables = database.migrate()
        bus = EventBus()
        bus.start()
        app.state.db = database
        app.state.bus = bus
        app.state.settings = settings
        # ORC-01 / LIVE-01: event_log + SSE hub, orchestrator, alerts, live watcher
        runtime = Runtime(database, bus, settings)
        app.state.runtime = runtime
        runtime.start()
        log.info("startup", tables=len(tables), db=str(database.path), env=settings.app_env)
        try:
            yield
        finally:
            await runtime.stop()
            await bus.stop()
            database.close()
            log.info("shutdown")

    app = FastAPI(
        title="MegaDay Twin API",
        version=__version__,
        description="Trợ lý ra quyết định quảng cáo cho shop Shopee (bản hackathon).",
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or new_id("req")
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["X-Request-ID"] = request_id
        return response

    install_audit_middleware(app)
    install_error_handlers(app)
    app.include_router(api_router)

    @app.get("/openapi.json", include_in_schema=False)
    def openapi_alias() -> JSONResponse:
        """01 §5 points `npm run gen:api` at /openapi.json; 08 uses /api/v1/openapi.json."""
        spec: dict[str, Any] = app.openapi()
        return JSONResponse(spec)

    return app


app = create_app()
