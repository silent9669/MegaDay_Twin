"""In-process event bus + every event type of the plan.

Plan: docs/dev-plan/01-kien-truc-va-cong-nghe.md §2 (7 core events) and
docs/dev-plan/06-dieu-phoi-va-hieu-nang.md §1 (6 auxiliary events, marked *).

Design
------
* One process, one bus (``asyncio.Queue``). ``publish`` only enqueues; the
  background ``run`` loop dispatches to handlers, so publishers never wait on
  slow consumers.
* Events are frozen pydantic models; ``type`` is the class name (``"DataUpdated"``)
  and is what ``event_log.type`` stores. ``to_log_row()`` gives the row.
* ``subscribe(DataUpdated, handler)`` or ``subscribe("DataUpdated", handler)``;
  ``subscribe("*", handler)`` receives everything (used by the ``event_log`` sink).
* A failing handler never kills the bus: it is logged and a ``ComputeFailed``
  event is published (unless the failing event is itself ``ComputeFailed``).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.timeutil import utcnow

log = get_logger(__name__)

WILDCARD = "*"

# Event class name → class, filled automatically by Event.__init_subclass__.
EVENT_TYPES: dict[str, type[Event]] = {}


class Event(BaseModel):
    """Base event. Subclasses add typed payload fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=lambda: new_id("evt"))
    at: datetime = Field(default_factory=utcnow)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        EVENT_TYPES[cls.__name__] = cls

    @property
    def type(self) -> str:
        return type(self).__name__

    @property
    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"id", "at"})

    def to_log_row(self) -> dict[str, Any]:
        """Row for the ``event_log`` table (06 §7)."""
        return {"id": self.id, "type": self.type, "payload": self.payload, "at": self.at}


# --------------------------------------------------------------------- core (01 §2)
class DataUpdated(Event):
    """Emitted by ``live.watcher`` / ingest after each load."""

    batch_id: str
    sources: list[str]
    as_of: datetime | None = None
    skus_changed: list[str] = Field(default_factory=list)  # empty = unknown → all


class RecomputeRequested(Event):
    sku_ids: list[str]
    reason: str
    priority: float = 0.0


class ForecastUpdated(Event):
    sku_id: str
    forecast_id: str


class DriftDetected(Event):
    sku_id: str
    direction: Literal["up", "down"]
    stat: float


class RecommendationChanged(Event):
    """``old``/``new`` are serialised ``Recommendation`` objects (05 §1)."""

    campaign_id: str
    old: dict[str, Any] | None
    new: dict[str, Any]


class AlertRaised(Event):
    alert_id: str


class DecisionRecorded(Event):
    decision_id: str


# --------------------------------------------------------------------- auxiliary (06 §1)
class GoalChanged(Event):
    scope: str
    ids: list[str]
    version: int


class ParameterChanged(Event):
    scope: str
    ids: list[str]
    version: int


class StageDone(Event):
    stage: str  # panel|forecast|simulate|recommend
    duration_ms: float
    sku_id: str | None = None
    campaign_id: str | None = None


class ComputeFailed(Event):
    target: str
    stage: str
    error: str


class AlertResolved(Event):
    alert_id: str


class LiveTick(Event):
    """Mirrors ``live_stream/trang_thai.json``: ``ngay`` → ``day``,
    ``don_hang_va_ton_den_het`` → ``orders_inventory_until``,
    ``ads_den_het`` → ``ads_until`` (both UTC instants)."""

    day: date
    orders_inventory_until: datetime | None
    ads_until: datetime | None


CORE_EVENTS: tuple[type[Event], ...] = (
    DataUpdated,
    RecomputeRequested,
    ForecastUpdated,
    DriftDetected,
    RecommendationChanged,
    AlertRaised,
    DecisionRecorded,
)
AUX_EVENTS: tuple[type[Event], ...] = (
    GoalChanged,
    ParameterChanged,
    StageDone,
    ComputeFailed,
    AlertResolved,
    LiveTick,
)

Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    """Single in-process bus. Not thread-safe; use from the event loop."""

    def __init__(self, maxsize: int = 0) -> None:
        self._subs: dict[str, list[Handler]] = {}
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task[None] | None = None

    # ---------------------------------------------------------------- subscribe
    def subscribe(self, event_type: str | type[Event], handler: Handler) -> None:
        key = event_type if isinstance(event_type, str) else event_type.__name__
        if key != WILDCARD and key not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {key}")
        self._subs.setdefault(key, []).append(handler)

    def unsubscribe(self, event_type: str | type[Event], handler: Handler) -> None:
        key = event_type if isinstance(event_type, str) else event_type.__name__
        with contextlib.suppress(KeyError, ValueError):
            self._subs[key].remove(handler)

    def handlers_for(self, event: Event) -> list[Handler]:
        return [*self._subs.get(event.type, []), *self._subs.get(WILDCARD, [])]

    # ---------------------------------------------------------------- publish
    async def publish(self, event: Event) -> None:
        """Enqueue; does not wait for handlers."""
        await self._queue.put(event)

    def publish_nowait(self, event: Event) -> None:
        self._queue.put_nowait(event)

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    # ---------------------------------------------------------------- dispatch
    async def _dispatch(self, event: Event) -> None:
        for handler in self.handlers_for(event):
            try:
                await handler(event)
            except Exception as exc:
                with contextlib.suppress(Exception):  # logging must never kill the bus
                    log.exception(
                        "event_handler_failed",
                        event_type=event.type,
                        event_id=event.id,
                        handler=getattr(handler, "__qualname__", repr(handler)),
                    )
                if not isinstance(event, ComputeFailed):
                    self.publish_nowait(
                        ComputeFailed(
                            target=getattr(handler, "__qualname__", "handler"),
                            stage=f"handle:{event.type}",
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )

    async def run(self) -> None:
        """Background loop. Cancel the task to stop."""
        while True:
            event = await self._queue.get()
            try:
                await self._dispatch(event)
            except Exception:  # pragma: no cover - defensive, _dispatch already guards
                pass
            finally:
                self._queue.task_done()

    async def join(self) -> None:
        """Wait until every queued event (including ones published by handlers)
        has been dispatched. Requires ``run`` to be active."""
        await self._queue.join()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="event-bus")
        return self._task

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()
