"""Wiring of the live path in the FastAPI process (ORC-01, LIVE-01, API-07).

``Runtime`` owns, on the app bus:

* ``EventLogSink`` (``"*"`` → ``event_log``) and ``StreamHub`` (``"*"`` → SSE) — always on;
* ``Orchestrator`` (``DataUpdated`` / ``GoalChanged`` / ``ParameterChanged`` → recompute)
  and ``AlertEvaluator`` (``DriftDetected`` / ``RecommendationChanged`` / ``ComputeFailed``)
  — on unless ``APP_ENV=test`` (unit/API tests must not get background writers; tests of
  this module turn it on with ``compute=True``);
* ``LiveWatcher`` on ``LIVE_DIR`` — when ``LIVE_ENABLED`` and compute is on;
* ``LiveController`` (demo buttons, ``POST /live/control``), replaying
  ``DATA_DIR/live_11_11/_replay.json``.

One ``threading.Lock`` serialises every writer of the live path (watcher load, recompute,
alerts); API requests keep their own cursors.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager

import duckdb

from app.core.config import Settings
from app.core.events import Event, EventBus
from app.core.logging import get_logger
from app.live.control import LiveController
from app.live.replay import replay_source
from app.live.watcher import LiveWatcher
from app.orchestrator import state
from app.orchestrator.alerts_eval import AlertEvaluator
from app.orchestrator.event_log import EventLogSink
from app.orchestrator.recompute import Orchestrator
from app.orchestrator.stream import StreamHub
from app.simulator.locks import register_live_write_lock, unregister_live_write_lock
from app.warehouse.db import Database

log = get_logger(__name__)

CursorFactory = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]


class Runtime:
    def __init__(
        self,
        db: Database,
        bus: EventBus,
        settings: Settings,
        *,
        compute: bool | None = None,
        watch: bool | None = None,
        simulate: bool = True,
    ) -> None:
        self.db = db
        self.bus = bus
        self.settings = settings
        self.compute = settings.app_env != "test" if compute is None else compute
        self.watch = (self.compute and settings.live_enabled) if watch is None else watch
        self.lock = threading.Lock()
        cursor: CursorFactory = db.reader
        self.hub = StreamHub(cursor)
        self.event_log = EventLogSink(cursor)
        self.evaluator = AlertEvaluator(
            cursor, lock=self.lock, shop_id=settings.shop_id, demo_mode=settings.demo_mode
        )
        self.orchestrator = Orchestrator(
            cursor,
            bus,
            lock=self.lock,
            samples=settings.mc_samples,
            simulate=simulate,
            evaluator=self.evaluator,
            notify=self.hub.push,
        )
        self.watcher = LiveWatcher(
            settings.live_dir,
            bus,
            cursor,
            poll_s=settings.live_poll_seconds,
            lock=self.lock,
            on_restart=[self._on_restart],
        )
        self.control = LiveController(
            self.watcher, replay_source(settings.data_dir), settle=self.settle
        )
        self.started = False

    def _on_restart(self, conn: duckdb.DuckDBPyConnection) -> list[Event]:
        self.orchestrator.reset_day()
        state.reset_failed(conn)
        return self.evaluator.on_restart(conn)

    def start(self) -> None:
        # AI compute tools (no request at hand) find the live write lock here (M5 Q5)
        register_live_write_lock(self.lock)
        self.hub.bind_loop()
        self.bus.subscribe("*", self.event_log)
        self.bus.subscribe("*", self.hub.on_event)
        if self.compute:
            self.orchestrator.attach()
            self.evaluator.attach(self.bus)
            self.orchestrator.start()
        if self.watch:
            self.watcher.start()
        self.started = True
        log.info("runtime_started", compute=self.compute, watch=self.watch,
                 live_dir=str(self.settings.live_dir))  # fmt: skip

    async def stop(self) -> None:
        await self.control.stop()
        await self.watcher.stop()
        await self.orchestrator.stop()
        unregister_live_write_lock(self.lock)

    async def settle(self) -> None:
        """Wait until the bus and the recompute worker are both idle (jump/tests)."""
        while True:
            if self.bus.running:
                await self.bus.join()
            if self.compute:
                await self.orchestrator.drain()
            await asyncio.sleep(0)
            if self.bus.pending == 0 and not (self.compute and self.orchestrator.busy):
                return
