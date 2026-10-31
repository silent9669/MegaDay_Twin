"""Answer cache and data fingerprint (AI-10, 07 §9).

* :func:`data_fingerprint` — stable hash of what the answer depends on: loaded batches
  (source, as_of, rows), active goals, effective parameters. Deterministic across rebuilds
  of the same data (no ULIDs), so a recording made in rehearsal is found again at demo time.
* :class:`AnswerCache` — same flow + question + data + role → the same answer for 30 min
  (in memory, per process; key holds ``shop_id`` as 10 §9 asks).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

import duckdb

from app.ai.client import normalize_question

TTL_SECONDS = 30 * 60


def data_fingerprint(conn: duckdb.DuckDBPyConnection) -> str:
    parts: list[Any] = []
    parts.append(
        conn.execute(
            "SELECT source_type, CAST(as_of AS VARCHAR), row_count FROM ingest_batches"
            " WHERE status <> 'failed' ORDER BY 1, 2, 3"
        ).fetchall()
    )
    parts.append(
        conn.execute(
            "SELECT scope_id, kind, CAST(deadline AS VARCHAR), target_units, CAST(params AS"
            " VARCHAR) FROM goals WHERE active ORDER BY 1, 2, 3"
        ).fetchall()
    )
    parts.append(
        conn.execute(
            "SELECT key, scope, scope_id, value, CAST(value_json AS VARCHAR) FROM parameters"
            " WHERE set_by <> 'ai_suggested' QUALIFY row_number() OVER (PARTITION BY key, scope,"
            " scope_id ORDER BY version DESC) = 1 ORDER BY 1, 2, 3"
        ).fetchall()
    )
    raw = json.dumps(parts, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class AnswerCache:
    def __init__(self, ttl: float = TTL_SECONDS) -> None:
        self.ttl = ttl
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(flow: str, question: str, data_fp: str, role: str, shop_id: str) -> str:
        raw = json.dumps([shop_id, flow, normalize_question(question), data_fp, role])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any | None:
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            at, value = hit
            if time.monotonic() - at > self.ttl:
                self._data.pop(key, None)
                return None
            return value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


ANSWER_CACHE = AnswerCache()
