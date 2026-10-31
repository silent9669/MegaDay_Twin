"""``event_log`` sink (ORC-01, 06 §1/§7): every bus event becomes one row, for debugging,
the demo timeline and performance (``LiveTick.at`` → last ``StageDone``)."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import duckdb

from app.core.events import Event
from app.core.logging import get_logger

log = get_logger(__name__)

CursorFactory = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]


def write_event(conn: duckdb.DuckDBPyConnection, event: Event) -> None:
    row = event.to_log_row()
    conn.execute(
        'INSERT INTO event_log (id, type, payload, "at") VALUES (?, ?, ?, ?)'
        " ON CONFLICT (id) DO NOTHING",
        [row["id"], row["type"], json.dumps(row["payload"], ensure_ascii=False), row["at"]],
    )


class EventLogSink:
    """Bus handler for ``"*"``. A failed insert is logged, never raised (the bus would
    publish ``ComputeFailed`` and we would log that too, forever)."""

    def __init__(self, cursor: CursorFactory) -> None:
        self._cursor = cursor
        self.written = 0

    async def __call__(self, event: Event) -> None:
        try:
            with self._cursor() as cur:
                write_event(cur, event)
            self.written += 1
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("event_log_failed", event_type=event.type, error=str(exc))


def read_events(
    conn: duckdb.DuckDBPyConnection, *, types: list[str] | None = None, limit: int = 1000
) -> list[dict[str, Any]]:
    sql = 'SELECT id, type, payload, "at" FROM event_log'
    args: list[Any] = []
    if types:
        sql += " WHERE list_contains(?, type)"
        args.append(types)
    sql += ' ORDER BY "at", id LIMIT ?'
    args.append(limit)
    out = []
    for r in conn.execute(sql, args).fetchall():
        payload = json.loads(r[2]) if isinstance(r[2], str) else r[2]
        out.append({"id": r[0], "type": r[1], "payload": payload, "at": r[3]})
    return out
