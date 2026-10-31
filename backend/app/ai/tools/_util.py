"""Small helpers shared by the tool modules (time parsing, data clock, formatting)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import duckdb

from app.api.common import data_as_of
from app.core.errors import NotFound, ValidationFailed
from app.core.timeutil import VN_TZ, floor_hour, iso_local, to_local, to_utc, utcnow


def now_data(conn: duckdb.DuckDBPyConnection) -> datetime:
    """Data clock (exclusive mark): ``max(as_of)`` of orders / inventory, else wall clock."""
    return data_as_of(conn) or floor_hour(utcnow())


def parse_ts(text: str | None, default: datetime) -> datetime:
    """ISO 8601 (``+07:00`` / ``Z`` / naive = VN time) or ``YYYY-MM-DD`` → aware UTC."""
    if text is None or not text.strip():
        return default
    s = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise ValidationFailed(f"Thời điểm không hợp lệ: {text!r} (cần ISO 8601).") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=VN_TZ)
    return to_utc(dt)


def hhmm(ts: datetime | None) -> str:
    return "" if ts is None else f"{to_local(ts):%H%M}"


def label_until(ts: datetime | None) -> str:
    """ "dữ liệu tới 13:59 11/11" for an exclusive mark ``ts`` = 14:00."""
    if ts is None:
        return "chưa có dữ liệu"
    last = to_local(ts - timedelta(minutes=1))
    return f"dữ liệu tới {last:%H:%M %d/%m}"


def require_sku(conn: duckdb.DuckDBPyConnection, sku: str) -> None:
    if conn.execute("SELECT 1 FROM skus WHERE sku_id = ?", [sku]).fetchone() is None:
        raise NotFound(f"Không có SKU {sku}. Dùng list_skus để tìm đúng mã.", sku=sku)


def require_campaign(conn: duckdb.DuckDBPyConnection, campaign_id: str) -> None:
    row = conn.execute("SELECT 1 FROM campaigns WHERE campaign_id = ?", [campaign_id]).fetchone()
    if row is None:
        raise NotFound(f"Không có chiến dịch {campaign_id}.", campaign_id=campaign_id)


def campaign_of(conn: duckdb.DuckDBPyConnection, sku: str) -> str | None:
    row = conn.execute(
        "SELECT campaign_id FROM campaign_skus WHERE sku_id = ? ORDER BY campaign_id LIMIT 1",
        [sku],
    ).fetchone()
    return None if row is None else str(row[0])


def iso(ts: Any) -> str | None:
    if isinstance(ts, datetime):
        return iso_local(ts)
    if ts is None:
        return None
    return str(ts)


def r2(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)
