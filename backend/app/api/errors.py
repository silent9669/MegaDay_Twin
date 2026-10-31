"""Exception → HTTP mapping (08 §1 "Lỗi"). Body: ``{"error": {code, message, details}}``."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core import messages_vi as vi
from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)

_HTTP_CODE = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "STATE_CONFLICT",
    422: "VALIDATION_ERROR",
    429: "AI_BUDGET_EXCEEDED",
    503: "AI_UNAVAILABLE",
}
_HTTP_MESSAGE = {
    401: vi.ERR_UNAUTHENTICATED,
    403: vi.ERR_FORBIDDEN,
    404: vi.ERR_NOT_FOUND,
}


def error_response(
    status: int, code: str, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


async def _app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.http_status >= 500:
        log.error("app_error", code=exc.code, details=exc.details)
    return error_response(exc.http_status, exc.code, exc.message, exc.details)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return error_response(400, "VALIDATION_ERROR", vi.ERR_VALIDATION, {"errors": errors})


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_CODE.get(exc.status_code, "HTTP_ERROR")
    message = _HTTP_MESSAGE.get(exc.status_code) or str(exc.detail)
    return error_response(exc.status_code, code, message)


async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", error_type=type(exc).__name__)
    return error_response(500, "INTERNAL_ERROR", vi.ERR_INTERNAL)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled)
