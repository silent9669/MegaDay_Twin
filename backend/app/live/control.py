"""Demo controls for live mode (API-07 ``POST /live/control``, 08 §3.13).

* ``start(from_hour, interval_seconds)`` — replays 11.11 hour by hour into ``live_dir``
  (Python twin of ``replay.js``, :mod:`app.live.replay`; no Node needed), poking the watcher
  after each step.
* ``stop()`` — stops the replay task (data already loaded stays).
* ``jump(hour)`` — "nhảy tới giờ H": stops the replay, writes the folder as it is after hour
  ``H`` and loads it at once (going back in time = a replay restart: the live rows are
  removed and reloaded up to ``H``). The recompute then runs in the background; ``settle``
  waits for it.
* ``reset()`` — stops, empties ``live_dir`` and removes every live batch (back to the
  history only).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.core.timeutil import utcnow
from app.ingest.pipeline import read_live_status, reset_live_data
from app.live.replay import clear_live_stream, load_replay, write_live_stream
from app.live.watcher import LiveWatcher

log = get_logger(__name__)


class LiveController:
    def __init__(
        self,
        watcher: LiveWatcher,
        source: Path,
        *,
        settle: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.watcher = watcher
        self.source = Path(source)
        self._settle = settle
        self._task: asyncio.Task[None] | None = None
        self.from_hour: int | None = None
        self.interval_seconds: float | None = None
        self.hour: int | None = None  # last hour written by this controller
        self.started_at: datetime | None = None
        self.finished = False
        self.command_lock = asyncio.Lock()

    @property
    def live_dir(self) -> Path:
        return self.watcher.root

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------ commands
    async def start(self, from_hour: int = 0, interval_seconds: float = 20.0) -> None:
        if not 0 <= from_hour <= 23:
            raise ValueError("from_hour phải trong 0–23")
        load_replay(self.source)  # fail fast when the shop has no replay file
        await self.stop()
        self.from_hour, self.interval_seconds = from_hour, interval_seconds
        self.started_at, self.finished = utcnow(), False
        self._task = asyncio.create_task(self._replay(from_hour, interval_seconds), name="replay")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def jump(self, hour: int, *, wait: bool = False) -> None:
        await self.stop()
        await self._step(hour)
        if wait and self._settle is not None:
            await self._settle()

    async def reset(self) -> int:
        await self.stop()
        await asyncio.to_thread(clear_live_stream, self.live_dir)
        self.hour, self.from_hour, self.finished = None, None, False

        def _wipe() -> int:
            with self.watcher.lock, self.watcher.cursor() as conn:
                n = reset_live_data(conn)
                for hook in self.watcher.on_restart:
                    hook(conn)
                return n

        n = await asyncio.to_thread(_wipe)
        self.watcher.last_tick = None
        log.info("live_reset", batches=n)
        return n

    async def _replay(self, from_hour: int, interval: float) -> None:
        try:
            for hour in range(from_hour, 24):
                await self._step(hour)
                if hour < 23:
                    await asyncio.sleep(interval)
            self.finished = True
        except Exception as exc:
            self.watcher.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("replay_failed")

    async def _step(self, hour: int) -> None:
        async def commit() -> None:
            await asyncio.to_thread(write_live_stream, self.source, self.live_dir, hour)
            await self.watcher.poll()  # also works when the file watcher is disabled
            self.hour = hour

        step = asyncio.create_task(commit())
        try:
            await asyncio.shield(step)
        except asyncio.CancelledError:
            # Stopping waits for the in-flight hour; never leave a thread writing after reset.
            await step
            raise

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        st = read_live_status(self.live_dir)
        return {
            "replay_running": self.running,
            "replay_hour": self.hour,
            "from_hour": self.from_hour,
            "interval_seconds": self.interval_seconds,
            "finished": self.finished,
            "folder_day": st.day.isoformat() if st else None,
            "folder_orders_until": st.raw.get("don_hang_va_ton_den_het") if st else None,
            "folder_ads_until": st.raw.get("ads_den_het") if st else None,
        }
