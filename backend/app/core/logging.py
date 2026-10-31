"""Structured logging (structlog, JSON lines). 01 §7.

Bind context with ``structlog.contextvars.bind_contextvars(batch_id=..., sku_id=...)``
or ``get_logger(__name__).bind(request_id=...)``. Never log PII or full prompts
at INFO.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_configured = False


def configure_logging(level: str = "INFO", *, json: bool = True) -> None:
    """Idempotent. Call once at process start (FastAPI lifespan, CLI)."""
    global _configured
    lvl = logging.getLevelName(level.upper())
    if not isinstance(lvl, int):
        lvl = logging.INFO

    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if json
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        # Resolve sys.stderr lazily so redirected/captured streams (tests, CLI
        # runners) never leave a logger pointing at a closed file.
        logger_factory=lambda *_: structlog.PrintLogger(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
    logging.basicConfig(level=lvl, stream=sys.stderr, format="%(message)s", force=True)
    _configured = True


def get_logger(name: str | None = None, **initial: Any) -> Any:
    """Return a bound structlog logger. Typed ``Any`` because structlog's
    bound-logger protocol is too loose for strict mypy callers."""
    if not _configured:
        configure_logging()
    log = structlog.get_logger(name)
    return log.bind(**initial) if initial else log
