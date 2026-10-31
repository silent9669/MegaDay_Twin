"""Shared helpers for routers: data clock, freshness per source, ``compute_state``,
cursor pagination (08 §1, 06 §4).

**Data clock.** ``data_as_of`` = ``max(as_of)`` of the ``orders`` / ``inventory`` batches,
the same mark the simulator uses (``simulator.inputs.data_as_of``): ``as_of`` 01:00 means
"data up to 00:59". In ``DEMO_MODE`` the reference "now" for freshness lags is the data
clock itself (the mock 11.11 day is in the future of the wall clock), see
docs/quyet-dinh/M3-api.md.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from typing import Any, Literal

import duckdb
from pydantic import BaseModel

from app.api.schemas import ComputeState, LocalDatetime
from app.core.errors import ValidationFailed
from app.core.timeutil import local_date, local_day_bounds_utc, utcnow

FreshStatus = Literal["fresh", "stale", "partial", "missing"]

# 06 §4: orders 30 min, inventory 60 min, ads 3 h (ads are 2 h late by design)
STALE_MINUTES: dict[str, int | None] = {
    "orders": 30,
    "inventory": 60,
    "ads": 180,
    "cost": None,
    "campaigns": None,
    "sale_program": None,
}
SOURCES: tuple[str, ...] = tuple(STALE_MINUTES)
SOURCE_LABEL_VI: dict[str, str] = {
    "orders": "Đơn hàng",
    "inventory": "Tồn kho",
    "ads": "Quảng cáo",
    "cost": "Giá vốn",
    "campaigns": "Chiến dịch",
    "sale_program": "Chương trình sale",
}
MAX_LIMIT = 500


def scalar(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    row = conn.execute(sql, params or []).fetchone()
    return row[0] if row else None


def data_as_of(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    v = scalar(
        conn,
        "SELECT max(as_of) FROM ingest_batches WHERE source_type IN ('orders', 'inventory')"
        " AND status <> 'failed'",
    )
    return v if isinstance(v, datetime) else None


def source_as_of(conn: duckdb.DuckDBPyConnection) -> dict[str, tuple[datetime | None, datetime]]:
    """``{source: (max as_of, max received_at)}`` over loaded batches."""
    rows = conn.execute(
        "SELECT source_type, max(as_of), max(received_at) FROM ingest_batches"
        " WHERE status <> 'failed' GROUP BY 1"
    ).fetchall()
    return {str(r[0]): (r[1], r[2]) for r in rows}


def reference_now(conn: duckdb.DuckDBPyConnection, demo_mode: bool) -> datetime:
    """Wall clock, or in demo mode the latest data mark of orders/inventory/ads."""
    if not demo_mode:
        return utcnow()
    v = scalar(
        conn,
        "SELECT max(as_of) FROM ingest_batches WHERE status <> 'failed'"
        " AND source_type IN ('orders', 'inventory', 'ads')",
    )
    return v if isinstance(v, datetime) else utcnow()


def today_bounds(clock: datetime) -> tuple[datetime, datetime]:
    """[start, end) in UTC of the VN day that holds the last data hour (``clock`` is an
    exclusive mark, so 00:00 belongs to the previous day)."""
    return local_day_bounds_utc(local_date(clock - timedelta(microseconds=1)))


class SourceFreshness(BaseModel):
    source: str
    label: str
    as_of: LocalDatetime | None
    received_at: LocalDatetime | None = None
    status: FreshStatus
    lag_minutes: int | None = None
    threshold_minutes: int | None = None
    missing_skus: int | None = None


def freshness(conn: duckdb.DuckDBPyConnection, now: datetime) -> list[SourceFreshness]:
    marks = source_as_of(conn)
    missing_cost = int(
        scalar(
            conn,
            "SELECT count(DISTINCT entity_id) FROM data_quality_issues"
            " WHERE code = 'MISSING_COST' AND status = 'open'",
        )
        or 0
    )
    out: list[SourceFreshness] = []
    for src in SOURCES:
        thr = STALE_MINUTES[src]
        mark = marks.get(src)
        if mark is None:
            out.append(
                SourceFreshness(
                    source=src,
                    label=SOURCE_LABEL_VI[src],
                    as_of=None,
                    status="missing",
                    threshold_minutes=thr,
                )
            )
            continue
        as_of, received = mark
        lag = None if as_of is None else max(0, int((now - as_of).total_seconds() // 60))
        status: FreshStatus = "fresh"
        if thr is not None and lag is not None and lag > thr:
            status = "stale"
        item = SourceFreshness(
            source=src,
            label=SOURCE_LABEL_VI[src],
            as_of=as_of,
            received_at=received,
            status=status,
            lag_minutes=lag if thr is not None else None,
            threshold_minutes=thr,
        )
        if src == "cost" and missing_cost:
            item.status = "partial"
            item.missing_skus = missing_cost
        out.append(item)
    return out


def compute_state(
    result_as_of: datetime | None,
    current_as_of: datetime | None,
    stored_state: str | None = None,
    stored_as_of: datetime | None = None,
) -> ComputeState | None:
    """06 §4 (ORC-04): a stored ``queued|computing|failed`` (``sku_compute_state``) wins;
    otherwise ``stale`` when the data clock moved on by more than 30 min since the result
    (or since the orchestrator last marked the SKU fresh), else ``fresh``. Rule in
    :func:`app.orchestrator.state.derive_state`."""
    from app.orchestrator.state import derive_state

    return derive_state(result_as_of, current_as_of, stored_state, stored_as_of)


# ---------------------------------------------------------------------- pagination
def encode_cursor(offset: int) -> str:
    raw = json.dumps({"o": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        pad = "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(cursor + pad))
        off = int(data["o"])
    except (ValueError, KeyError, TypeError) as e:
        raise ValidationFailed("Cursor phân trang không hợp lệ.", cursor=cursor) from e
    if off < 0:
        raise ValidationFailed("Cursor phân trang không hợp lệ.", cursor=cursor)
    return off


def paginate(items: list[Any], limit: int, cursor: str | None) -> tuple[list[Any], str | None]:
    off = decode_cursor(cursor)
    page = items[off : off + limit]
    nxt = encode_cursor(off + limit) if off + limit < len(items) else None
    return page, nxt


def parse_json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v
