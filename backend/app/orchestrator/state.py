"""``sku_compute_state`` and the stale rule (ORC-04, 06 §4).

State machine per SKU::

    fresh ──(DataUpdated chạm SKU)──▶ queued ──▶ computing ──▶ fresh
      ▲                                              │
      └──────────────(lỗi 3 lần)── failed ◀──────────┘

``stale`` is never stored: it is derived when a result is read (:func:`derive_state`).
A failure below :data:`MAX_RETRIES` puts the SKU back to ``queued`` (the next tick retries);
the third one stores ``failed``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Literal

import duckdb

from app.core.timeutil import utcnow

StoredState = Literal["fresh", "queued", "computing", "failed"]
ComputeStateName = Literal["fresh", "queued", "computing", "stale", "failed"]
MAX_RETRIES = 3
# 06 §4: a result is stale once the orders/stock data moved on by more than this
# (smallest source threshold: orders 30 min).
STALE_TOLERANCE = timedelta(minutes=30)


def _ids(sku_ids: Iterable[str]) -> list[str]:
    return sorted({str(s) for s in sku_ids})


def mark(
    conn: duckdb.DuckDBPyConnection,
    sku_ids: Iterable[str],
    state: Literal["queued", "computing"],
) -> int:
    """``queued`` / ``computing`` for every SKU (rows created when missing). Keeps
    ``computed_at`` / ``data_as_of`` of the last good result (the UI shows it greyed)."""
    ids = _ids(sku_ids)
    if not ids:
        return 0
    conn.execute(
        "INSERT INTO sku_compute_state (sku_id, state, retries)"
        " SELECT unnest(?), ?, 0 ON CONFLICT (sku_id) DO UPDATE SET state = excluded.state",
        [ids, state],
    )
    return len(ids)


def mark_fresh(
    conn: duckdb.DuckDBPyConnection,
    sku_ids: Iterable[str],
    *,
    data_as_of: datetime | None,
    input_hash: str | None = None,
    computed_at: datetime | None = None,
) -> int:
    ids = _ids(sku_ids)
    if not ids:
        return 0
    conn.execute(
        "INSERT INTO sku_compute_state (sku_id, state, last_input_hash, computed_at, data_as_of,"
        " error, retries) SELECT unnest(?), 'fresh', ?, ?, ?, NULL, 0"
        " ON CONFLICT (sku_id) DO UPDATE SET state = 'fresh',"
        " last_input_hash = excluded.last_input_hash, computed_at = excluded.computed_at,"
        " data_as_of = excluded.data_as_of, error = NULL, retries = 0",
        [ids, input_hash, computed_at or utcnow(), data_as_of],
    )
    return len(ids)


def mark_failed(conn: duckdb.DuckDBPyConnection, sku_ids: Iterable[str], error: str) -> list[str]:
    """One more failure. Returns the SKUs that reached :data:`MAX_RETRIES` (now ``failed``);
    the others go back to ``queued``."""
    ids = _ids(sku_ids)
    if not ids:
        return []
    conn.execute(
        "INSERT INTO sku_compute_state (sku_id, state, error, retries)"
        " SELECT unnest(?), 'queued', ?, 1 ON CONFLICT (sku_id) DO UPDATE SET"
        " error = excluded.error, retries = coalesce(sku_compute_state.retries, 0) + 1",
        [ids, error[:500]],
    )
    conn.execute(
        "UPDATE sku_compute_state SET state = CASE WHEN retries >= ? THEN 'failed'"
        " ELSE 'queued' END WHERE list_contains(?, sku_id)",
        [MAX_RETRIES, ids],
    )
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT sku_id FROM sku_compute_state WHERE list_contains(?, sku_id)"
            " AND state = 'failed' ORDER BY 1",
            [ids],
        ).fetchall()
    ]


def get_states(
    conn: duckdb.DuckDBPyConnection, sku_ids: Iterable[str] | None = None
) -> dict[str, dict[str, object]]:
    sql = "SELECT sku_id, state, computed_at, data_as_of, error, retries FROM sku_compute_state"
    args: list[object] = []
    if sku_ids is not None:
        sql += " WHERE list_contains(?, sku_id)"
        args.append(_ids(sku_ids))
    return {
        str(r[0]): {
            "state": r[1],
            "computed_at": r[2],
            "data_as_of": r[3],
            "error": r[4],
            "retries": int(r[5] or 0),
        }
        for r in conn.execute(sql, args).fetchall()
    }


def reset_failed(conn: duckdb.DuckDBPyConnection) -> int:
    """Replay restarted: failures of the old run do not carry over."""
    row = conn.execute("SELECT count(*) FROM sku_compute_state WHERE state <> 'fresh'").fetchone()
    conn.execute(
        "UPDATE sku_compute_state SET state = 'fresh', retries = 0, error = NULL"
        " WHERE state <> 'fresh'"
    )
    return int(row[0]) if row else 0


def derive_state(
    result_as_of: datetime | None,
    current_as_of: datetime | None,
    stored_state: str | None = None,
    stored_as_of: datetime | None = None,
    *,
    tolerance: timedelta = STALE_TOLERANCE,
) -> ComputeStateName | None:
    """06 §4. A stored ``queued | computing | failed`` wins. Otherwise the result is as new
    as the newer of its own ``data_as_of`` and the one stored when the orchestrator last
    marked the SKU ``fresh``; it is ``stale`` when the data clock moved on by more than
    ``tolerance`` (default 30 min = the orders threshold), else ``fresh``."""
    if stored_state in ("queued", "computing", "failed"):
        return stored_state  # type: ignore[return-value]
    marks = [t for t in (result_as_of, stored_as_of if stored_state == "fresh" else None) if t]
    if not marks:
        return None
    newest = max(marks)
    if current_as_of is not None and newest < current_as_of - tolerance:
        return "stale"
    return "fresh"
