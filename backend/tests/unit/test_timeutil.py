from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from app.core.timeutil import (
    VN_TZ,
    floor_hour,
    is_double_day,
    is_payday,
    is_post_double_day,
    is_pre_double_day,
    iso_local,
    local_date,
    local_day_bounds_utc,
    parse_local,
    to_local,
    to_utc,
)


def test_parse_local_shifts_minus_7() -> None:
    # 02 §2.1: 'YYYY-MM-DD HH:MM' VN → UTC (minus 7 hours)
    assert parse_local("2026-11-11 14:00") == datetime(2026, 11, 11, 7, 0, tzinfo=UTC)
    assert parse_local("2026-11-11 03:30") == datetime(2026, 11, 10, 20, 30, tzinfo=UTC)


def test_to_utc_naive_is_vn_by_default() -> None:
    assert to_utc(datetime(2026, 11, 11, 0, 0)) == datetime(2026, 11, 10, 17, 0, tzinfo=UTC)


def test_to_utc_keeps_aware() -> None:
    ts = datetime(2026, 11, 11, 9, 0, tzinfo=timezone(timedelta(hours=7)))
    out = to_utc(ts)
    assert out.tzinfo is UTC
    assert out == datetime(2026, 11, 11, 2, 0, tzinfo=UTC)


def test_roundtrip_utc_local() -> None:
    ts = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
    local = to_local(ts)
    assert local.tzinfo == VN_TZ
    assert (local.year, local.month, local.day, local.hour) == (2026, 11, 11, 0)
    assert to_utc(local) == ts


def test_floor_hour() -> None:
    ts = datetime(2026, 11, 11, 7, 59, 59, 999_999, tzinfo=UTC)
    assert floor_hour(ts) == datetime(2026, 11, 11, 7, 0, tzinfo=UTC)
    # naive input treated as UTC
    assert floor_hour(datetime(2026, 1, 1, 3, 15)).tzinfo is UTC


def test_floor_hour_preserves_offset() -> None:
    ts = datetime(2026, 11, 11, 9, 45, tzinfo=VN_TZ)
    assert floor_hour(ts) == datetime(2026, 11, 11, 9, 0, tzinfo=VN_TZ)


def test_is_double_day_dates() -> None:
    for d in [date(2026, 8, 8), date(2026, 9, 9), date(2026, 10, 10), date(2026, 11, 11)]:
        assert is_double_day(d)
    assert not is_double_day(date(2026, 11, 10))
    assert not is_double_day(date(2026, 10, 20))


def test_is_double_day_uses_vn_calendar() -> None:
    # 2026-11-10 17:00 UTC = 2026-11-11 00:00 VN → double day
    assert is_double_day(datetime(2026, 11, 10, 17, 0, tzinfo=UTC))
    # 2026-11-11 17:00 UTC = 2026-11-12 00:00 VN → not
    assert not is_double_day(datetime(2026, 11, 11, 17, 0, tzinfo=UTC))
    assert local_date(datetime(2026, 11, 10, 16, 59, tzinfo=UTC)) == date(2026, 11, 10)


def test_pre_post_double_day_and_payday() -> None:
    assert is_pre_double_day(date(2026, 11, 10))
    assert is_post_double_day(date(2026, 11, 12))
    assert not is_pre_double_day(date(2026, 11, 11))
    assert is_payday(date(2026, 10, 25))
    assert not is_payday(date(2026, 10, 24))


def test_local_day_bounds_utc() -> None:
    start, end = local_day_bounds_utc(date(2026, 9, 20))
    # 02 §2.2: VN day 20/09 = 2026-09-19 17:00 → 2026-09-20 17:00 UTC
    assert start == datetime(2026, 9, 19, 17, 0, tzinfo=UTC)
    assert end == datetime(2026, 9, 20, 17, 0, tzinfo=UTC)


def test_iso_local_has_plus_7() -> None:
    assert iso_local(datetime(2026, 11, 11, 7, 59, tzinfo=UTC)) == "2026-11-11T14:59:00+07:00"
    assert iso_local(None) is None
