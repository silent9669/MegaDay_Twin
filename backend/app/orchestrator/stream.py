"""SSE hub behind ``GET /api/v1/stream`` (API-07, 08 §4).

Bus events are turned into UI events (``live.tick``, ``ingest.batch_loaded``,
``compute.state``, ``recommendation.changed``, ``alert.raised`` / ``alert.resolved``,
``decision.changed``, ``compute.failed``), numbered with an increasing ``id`` and kept in a
ring of the last :data:`BUFFER` events so a client reconnecting with ``Last-Event-ID`` gets
what it missed. Every subscriber has its own queue; a slow client loses its oldest events,
never blocks the bus.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import duckdb

from app.core.events import (
    AlertRaised,
    AlertResolved,
    ComputeFailed,
    DataUpdated,
    DecisionRecorded,
    Event,
    LiveTick,
    RecommendationChanged,
)
from app.core.logging import get_logger
from app.core.timeutil import iso_local, to_local, utcnow

log = get_logger(__name__)

BUFFER = 500
HEARTBEAT_S = 15.0
QUEUE_MAX = 1000
CursorFactory = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]


@dataclass(frozen=True)
class StreamEvent:
    id: int
    event: str
    data: dict[str, Any]

    def encode(self) -> str:
        body = json.dumps(self.data, ensure_ascii=False, default=str)
        return f"id: {self.id}\nevent: {self.event}\ndata: {body}\n\n"


def _hm(t: Any) -> str | None:
    return None if t is None else to_local(t).strftime("%H:%M")


class StreamHub:
    def __init__(self, cursor: CursorFactory | None = None, buffer: int = BUFFER) -> None:
        self._cursor = cursor
        self._ring: deque[StreamEvent] = deque(maxlen=buffer)
        self._subs: set[asyncio.Queue[StreamEvent]] = set()
        self._next = 1
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ input
    def bind_loop(self) -> None:
        self._loop = asyncio.get_running_loop()

    def push(self, event: str, data: dict[str, Any]) -> StreamEvent:
        """Add one UI event (event-loop thread)."""
        ev = StreamEvent(self._next, event, data)
        self._next += 1
        self._ring.append(ev)
        for q in list(self._subs):
            if q.full():
                q.get_nowait()
            q.put_nowait(ev)
        return ev

    def push_threadsafe(self, event: str, data: dict[str, Any]) -> None:
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is not None and running is not loop:
            loop.call_soon_threadsafe(self.push, event, data)
        else:
            self.push(event, data)

    async def on_event(self, event: Event) -> None:
        """Bus handler (``"*"``)."""
        out = self.translate(event)
        if out is not None:
            self.push(*out)

    def translate(self, event: Event) -> tuple[str, dict[str, Any]] | None:
        if isinstance(event, LiveTick):
            return "live.tick", {
                "day": event.day.isoformat(),
                "orders_until": _hm(event.orders_inventory_until),
                "ads_until": _hm(event.ads_until),
                "orders_until_at": iso_local(event.orders_inventory_until),
                "ads_until_at": iso_local(event.ads_until),
            }
        if isinstance(event, DataUpdated):
            return "ingest.batch_loaded", {
                "batch_id": event.batch_id,
                "sources": event.sources,
                "as_of": iso_local(event.as_of),
                "skus": len(event.skus_changed),
            }
        if isinstance(event, RecommendationChanged):
            return "recommendation.changed", {
                "campaign_id": event.campaign_id,
                "sku": None,
                "recommendation": event.new,
            }
        if isinstance(event, AlertRaised | AlertResolved):
            name = "alert.raised" if isinstance(event, AlertRaised) else "alert.resolved"
            return name, self._alert(event.alert_id)
        if isinstance(event, DecisionRecorded):
            return "decision.changed", self._decision(event.decision_id)
        if isinstance(event, ComputeFailed):
            return "compute.failed", {
                "target": event.target,
                "stage": event.stage,
                "error": event.error,
            }
        return None

    def _alert(self, alert_id: str) -> dict[str, Any]:
        if self._cursor is None:
            return {"id": alert_id}
        try:
            from app.governance.alerts import get_alert

            with self._cursor() as cur:
                a = get_alert(cur, alert_id)
            d = a.model_dump(mode="json")
            for k in ("raised_at", "updated_at", "last_seen_at", "resolved_at", "snoozed_until"):
                v = getattr(a, k, None)
                if v is not None:
                    d[k] = iso_local(v)
            return d
        except Exception as exc:
            log.warning("stream_alert_lookup_failed", alert_id=alert_id, error=str(exc))
            return {"id": alert_id}

    def _decision(self, decision_id: str) -> dict[str, Any]:
        if self._cursor is None:
            return {"id": decision_id}
        try:
            with self._cursor() as cur:
                sql = "SELECT state FROM decisions WHERE id = ?"
                row = cur.execute(sql, [decision_id]).fetchone()
            return {"id": decision_id, "state": row[0] if row else None}
        except duckdb.Error:
            return {"id": decision_id}

    # ------------------------------------------------------------------ output
    def since(self, last_id: int | None) -> list[StreamEvent]:
        if last_id is None:
            return []
        return [e for e in self._ring if e.id > last_id]

    @property
    def last_id(self) -> int:
        return self._next - 1

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    async def subscribe(
        self,
        last_id: int | None = None,
        *,
        heartbeat_s: float = HEARTBEAT_S,
        limit: int | None = None,
        idle_timeout: float | None = None,
    ) -> AsyncIterator[str]:
        """SSE text chunks: missed events after ``last_id``, then live ones, a
        ``heartbeat`` every ``heartbeat_s``. ``limit`` stops after that many events
        (heartbeats excluded); ``idle_timeout`` stops after that long without an event
        (both for curl and tests)."""
        q: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=QUEUE_MAX)
        self._subs.add(q)
        sent = 0
        try:
            yield "retry: 3000\n\n"
            for ev in self.since(last_id):
                yield ev.encode()
                sent += 1
                if limit is not None and sent >= limit:
                    return
            idle = 0.0
            while True:
                wait = heartbeat_s if idle_timeout is None else min(heartbeat_s, idle_timeout)
                try:
                    ev = await asyncio.wait_for(q.get(), wait)
                except TimeoutError:
                    idle += wait
                    if idle_timeout is not None and idle >= idle_timeout:
                        return
                    yield f'event: heartbeat\ndata: {{"at": "{iso_local(utcnow())}"}}\n\n'
                    continue
                idle = 0.0
                yield ev.encode()
                sent += 1
                if limit is not None and sent >= limit:
                    return
        finally:
            self._subs.discard(q)
