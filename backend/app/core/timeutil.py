"""Time helpers. Rule (01 §7): code and DB always hold tz-aware UTC datetimes;
convert to Asia/Ho_Chi_Minh only for API ``*_local`` fields and display.

Vietnam has no DST, so VN = UTC+7 all year; we still use ``zoneinfo`` so the
intent is explicit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
UTC_TZ = UTC

LOCAL_MINUTE_FORMAT = "%Y-%m-%d %H:%M"


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_utc(ts: datetime, *, naive_tz: ZoneInfo = VN_TZ) -> datetime:
    """Convert to aware UTC. Naive input is interpreted in ``naive_tz``
    (default VN, because every seller file writes local time)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=naive_tz)
    return ts.astimezone(UTC)


def to_local(ts: datetime) -> datetime:
    """Convert to Asia/Ho_Chi_Minh. Naive input is treated as UTC."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(VN_TZ)


def parse_local(text: str, fmt: str = LOCAL_MINUTE_FORMAT) -> datetime:
    """Parse a VN local wall-clock string (e.g. ``'2026-11-11 14:00'``) into UTC."""
    return to_utc(datetime.strptime(text.strip(), fmt), naive_tz=VN_TZ)


def floor_hour(ts: datetime) -> datetime:
    """Truncate to the start of the hour, keeping tz. Naive input → UTC."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.replace(minute=0, second=0, microsecond=0)


def local_date(ts: datetime) -> date:
    """Calendar date in VN for a timestamp."""
    return to_local(ts).date()


def _as_local_date(d: date | datetime) -> date:
    return local_date(d) if isinstance(d, datetime) else d


def is_double_day(d: date | datetime) -> bool:
    """Mega-sale "double day": month == day (8.8, 9.9, 10.10, 11.11, 12.12…).
    Datetimes are evaluated on their VN calendar date."""
    ld = _as_local_date(d)
    return ld.month == ld.day


def is_pre_double_day(d: date | datetime) -> bool:
    return is_double_day(_as_local_date(d) + timedelta(days=1))


def is_post_double_day(d: date | datetime) -> bool:
    return is_double_day(_as_local_date(d) - timedelta(days=1))


def is_payday(d: date | datetime) -> bool:
    """Payday = 25th of the month (02 §6)."""
    return _as_local_date(d).day == 25


def local_day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    """[start, end) of a VN calendar day, expressed in UTC."""
    start = datetime(d.year, d.month, d.day, tzinfo=VN_TZ)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def iso_local(ts: datetime | None) -> str | None:
    """ISO 8601 with ``+07:00`` offset, as required by the API (08 §1)."""
    return None if ts is None else to_local(ts).isoformat()
