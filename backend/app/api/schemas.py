"""Response envelope + error body shared by every router (08 §1).

Every response that carries numbers uses ``Envelope[T]`` with a ``Meta`` that
states ``data_as_of``, ``computed_at``, ``compute_state`` and ``confidence``.
Lists use ``Page[T]`` (same plus a top-level ``next_cursor``, 08 §1 "Phân trang").
Datetimes are serialised as ISO 8601 with the ``+07:00`` offset.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field, PlainSerializer

from app.core.timeutil import iso_local

ComputeState = Literal["fresh", "queued", "computing", "stale", "failed"]
Confidence = Literal["verified", "reference", "scenario"]

LocalDatetime = Annotated[
    datetime,
    PlainSerializer(lambda v: iso_local(v), return_type=str, when_used="json"),
]

T = TypeVar("T")


class SourceRef(BaseModel):
    """Where a number comes from (CitationChip on the web, M3-web §6). ``data_as_of``,
    ``tool`` and ``href`` are optional additions to 08 §1."""

    id: str
    kind: str
    label: str
    data_as_of: LocalDatetime | None = None
    tool: str | None = None
    href: str | None = None


class Meta(BaseModel):
    data_as_of: LocalDatetime | None = None
    computed_at: LocalDatetime | None = None
    compute_state: ComputeState | None = None
    confidence: Confidence | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    cache_hit: bool | None = None  # POST /scenarios/run only
    total: int | None = None  # [DEV] rows matching the filters, all pages (GET /skus)


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: Meta = Field(default_factory=Meta)


class Page(BaseModel, Generic[T]):
    data: list[T]
    meta: Meta = Field(default_factory=Meta)
    next_cursor: str | None = None


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    error: ErrorInfo
