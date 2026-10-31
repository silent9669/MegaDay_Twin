"""Write locks shared by every path that runs the simulator (M5 Q5).

A simulation computes and persists forecasts and demand groups. DuckDB refuses two writers
on the same rows ("Conflict on update"), so every caller serialises on the same two locks,
always in the same order (no deadlock):

1. the **live write lock** of the runtime (``Runtime.lock``: watcher load, orchestrator
   recompute, alert evaluation, live control);
2. :data:`RUN_LOCK` (one simulation at a time: the first run at a new start computes the
   forecasts the others reuse).

Callers: ``Orchestrator._simulate`` (already holds 1, takes 2), ``POST /scenarios/run``
(``app.api.scenarios``) and the AI compute tools ``run_scenario`` / ``compare_actions`` /
``compute_breakeven`` (``app.ai.tools.compute``), which take 1 then 2.

The runtime registers its lock on start (:func:`register_live_write_lock`) so code without
the FastAPI request (AI tools, SSE threads, checklist) finds it; without a running runtime
(unit tests, scripts) the live lock is a no-op.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterator
from typing import Any

RUN_LOCK = threading.Lock()

_registered: Any = None  # the running Runtime's lock (threading.Lock), or None
_reg_guard = threading.Lock()


def register_live_write_lock(lock: Any) -> None:
    global _registered
    with _reg_guard:
        _registered = lock


def unregister_live_write_lock(lock: Any) -> None:
    """Forget ``lock`` if it is still the registered one (a newer runtime keeps its own)."""
    global _registered
    with _reg_guard:
        if _registered is lock:
            _registered = None


def live_write_lock(lock: Any = None) -> contextlib.AbstractContextManager[Any]:
    """``lock`` if given, else the registered runtime lock, else a no-op."""
    chosen = lock if lock is not None else _registered
    return chosen if chosen is not None else contextlib.nullcontext()


@contextlib.contextmanager
def simulation_locks(lock: Any = None) -> Iterator[None]:
    """Live write lock **then** :data:`RUN_LOCK` (the orchestrator's order)."""
    with live_write_lock(lock), RUN_LOCK:
        yield
