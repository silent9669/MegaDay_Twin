"""Domain exceptions. The API layer maps each to an HTTP status + machine
``code`` (docs/dev-plan/08-api.md §1, "Lỗi"). Raise these from any layer;
never raise ``HTTPException`` outside ``app/api``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.core import messages_vi as vi


class AppError(Exception):
    """Base class. ``message`` is Vietnamese, shown to the user as-is."""

    http_status: ClassVar[int] = 500
    code: ClassVar[str] = "INTERNAL_ERROR"
    default_message: ClassVar[str] = vi.ERR_INTERNAL

    def __init__(self, message: str | None = None, **details: Any) -> None:
        self.message = message or self.default_message
        self.details: dict[str, Any] = details
        super().__init__(self.message)


class ValidationFailed(AppError):
    http_status = 400
    code = "VALIDATION_ERROR"
    default_message = vi.ERR_VALIDATION


class ParamOutOfRange(AppError):
    http_status = 400
    code = "PARAM_OUT_OF_RANGE"
    default_message = vi.ERR_PARAM_OUT_OF_RANGE


class Unauthenticated(AppError):
    http_status = 401
    code = "UNAUTHENTICATED"
    default_message = vi.ERR_UNAUTHENTICATED


class Forbidden(AppError):
    http_status = 403
    code = "FORBIDDEN"
    default_message = vi.ERR_FORBIDDEN


class NotFound(AppError):
    http_status = 404
    code = "NOT_FOUND"
    default_message = vi.ERR_NOT_FOUND


class StateConflict(AppError):
    """Invalid state-machine transition (10 §2)."""

    http_status = 409
    code = "STATE_CONFLICT"
    default_message = vi.ERR_STATE_CONFLICT


class ConstraintViolation(AppError):
    """Action violates a sale-program / campaign-structure constraint (05 §5.4)."""

    http_status = 409
    code = "CONSTRAINT_VIOLATION"
    default_message = vi.ERR_CONSTRAINT_VIOLATION


class StaleDataError(AppError):
    """Decision/scenario based on inputs that changed since compute (``STALE_INPUT``)."""

    http_status = 409
    code = "STALE_INPUT"
    default_message = vi.ERR_STALE_INPUT


class DataQualityError(AppError):
    """Blocking data issue (e.g. ``MISSING_COST``) prevents computation."""

    http_status = 422
    code = "BLOCKING_DATA_ISSUE"
    default_message = vi.ERR_BLOCKING_DATA_ISSUE


class AiBudgetExceeded(AppError):
    http_status = 429
    code = "AI_BUDGET_EXCEEDED"
    default_message = vi.ERR_AI_BUDGET_EXCEEDED


class AiUnavailable(AppError):
    http_status = 503
    code = "AI_UNAVAILABLE"
    default_message = vi.ERR_AI_UNAVAILABLE


class NotImplementedYet(AppError):
    """Placeholder for endpoints/commands owned by a later task."""

    http_status = 501
    code = "NOT_IMPLEMENTED"
    default_message = vi.ERR_NOT_IMPLEMENTED
