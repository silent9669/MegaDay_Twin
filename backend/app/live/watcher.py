"""LiveWatcher (LIVE-01, 06 §5): poll ``live_stream/trang_thai.json``; when the mark
changes, load the new rows with :func:`app.ingest.pipeline.ingest_live_delta` (``live_cursor``
per file, same adapters as a normal import, ``mode='live'``) and publish its ``DataUpdated``
then ``LiveTick`` on the bus.

* The mark is compared in the event loop (cheap file read); the load runs in a worker
  thread under the shared write lock.
* A CSV shorter than its cursor = ``replay.js`` restarted from hour 0: the pipeline removes
  the live rows and reloads from row 0; the watcher also tells the orchestrator (drift
  memory) and the alert evaluator (alerts of the old run are resolved) via ``on_restart``.
* :meth:`poke` wakes the loop at once (demo controls write the folder themselves).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from app.core.events import Event, EventBus, LiveTick
from app.core.logging import get_logger
from app.core.timeutil import utcnow
from app.ingest.pipeline import LiveDeltaReport, ingest_live_delta
from app.live.replay import STATUS_FILE

log = get_logger(__name__)

CursorFactory = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]
RestartHook = Callable[[duckdb.DuckDBPyConnection], list[Event]]


class _Collect(EventBus):
    """Stand-in bus for the pipeline: keeps the events so the watcher publishes them on the
    real bus from the event loop."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[Event] = []

    def publish_nowait(self, event: Event) -> None:
        self.events.append(event)


class LiveWatcher:
    def __init__(
        self,
        root: Path,
        bus: EventBus,
        cursor: CursorFactory,
        *,
        poll_s: float = 2.0,
        lock: threading.Lock | None = None,
        on_restart: list[RestartHook] | None = None,
    ) -> None:
        self.root = Path(root)
        self.bus = bus
        self.cursor = cursor
        self.poll_s = poll_s
        self.lock = lock or threading.Lock()
        self.on_restart = list(on_restart or [])
        self._task: asyncio.Task[None] | None = None
        self._poke: asyncio.Event | None = None
        self._last_raw: str | None = None
        self.last_tick: LiveTick | None = None
        self.last_tick_at: datetime | None = None  # wall clock of the last load
        self.last_report: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.loads = 0
        self.restarts = 0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._poke = asyncio.Event()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="live-watcher")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def poke(self) -> None:
        if self._poke is not None:
            self._poke.set()

    async def _run(self) -> None:
        assert self._poke is not None
        while True:
            try:
                if self._changed():
                    await self.poll()
            except Exception as exc:  # keep watching
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("live_poll_failed", error=self.last_error)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._poke.wait(), self.poll_s)
            self._poke.clear()

    def _changed(self) -> bool:
        path = self.root / STATUS_FILE
        try:
            raw = path.read_text(encoding="utf-8-sig")
        except (FileNotFoundError, PermissionError, OSError):
            return False
        if raw == self._last_raw:
            return False
        self._last_raw = raw
        return True

    # ------------------------------------------------------------------ one load
    async def poll(self) -> LiveDeltaReport:
        """Load what is new and publish the events (``DataUpdated``, ``LiveTick``,
        ``AlertResolved`` after a restart)."""
        report, events = await asyncio.to_thread(self.poll_sync)
        for ev in events:
            await self.bus.publish(ev)
        return report

    def poll_sync(self) -> tuple[LiveDeltaReport, list[Event]]:
        collect = _Collect()
        with self.lock, self.cursor() as conn:
            report = ingest_live_delta(conn, self.root, bus=collect, build_panel=True)
            extra: list[Event] = []
            if report.restarted:
                self.restarts += 1
                for hook in self.on_restart:
                    try:
                        extra += hook(conn)
                    except Exception as exc:
                        log.exception("live_restart_hook_failed", error=str(exc))
        if report.status != "unchanged":
            self.loads += 1
            self.last_tick_at = utcnow()
            if report.tick is not None:
                self.last_tick = report.tick
            self.last_report = {
                "status": report.status,
                "restarted": report.restarted,
                "new_rows": report.new_rows,
                "duration_ms": report.duration_ms,
            }
            self.last_error = None
            log.info(
                "live_loaded",
                status=report.status,
                restarted=report.restarted,
                new_rows=report.new_rows,
                ms=report.duration_ms,
            )
        return report, [*extra, *collect.events]
