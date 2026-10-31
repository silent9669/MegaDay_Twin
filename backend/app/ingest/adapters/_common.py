"""Small vectorised parsers shared by the CSV adapters."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from app.core.money import parse_vnd
from app.core.timeutil import VN_TZ

LOCAL_MINUTE_FORMAT = "%Y-%m-%d %H:%M"
LOCAL_DATE_FORMAT = "%Y-%m-%d"


def parse_local_series(text: pd.Series, fmt: str = LOCAL_MINUTE_FORMAT) -> pd.Series:
    """VN wall-clock strings → ``datetime64[ns, UTC]`` (NaT when unreadable)."""
    naive = pd.to_datetime(text.str.strip(), format=fmt, errors="coerce")
    return naive.dt.tz_localize(VN_TZ).dt.tz_convert("UTC")


def parse_naive_series(text: pd.Series, fmt: str = LOCAL_MINUTE_FORMAT) -> pd.Series:
    return pd.to_datetime(text.str.strip(), format=fmt, errors="coerce")


def shift_naive_to_utc(naive: pd.Series, offset_hours: int) -> pd.Series:
    """File wall clock + offset = VN wall clock → UTC."""
    vn = naive + timedelta(hours=offset_hours)
    return vn.dt.tz_localize(VN_TZ).dt.tz_convert("UTC")


def parse_int_series(text: pd.Series) -> pd.Series:
    """Plain integers (counts) → ``Int64``; blank/invalid → <NA>."""
    s = text.astype(str).str.strip().str.replace(r"[.,\s](?=\d{3}\b)", "", regex=True)
    num = pd.to_numeric(s.where(s != ""), errors="coerce")
    num = num.where(num == num.round())
    return num.astype("Int64")


def parse_money_series(text: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Money cells → (``Int64`` VND, bool mask of non-blank cells that failed).

    Fast path for plain digits; ``parse_vnd`` for everything else ("45k", "45.000đ").
    """
    s = text.astype(str).str.strip()
    blank = s == ""
    plain = s.str.fullmatch(r"-?\d+")
    out = pd.Series(pd.NA, index=s.index, dtype="Int64")
    if plain.any():
        out[plain] = pd.to_numeric(s[plain]).astype("Int64")
    rest = ~plain & ~blank
    if rest.any():
        out[rest] = s[rest].map(parse_vnd).astype("Int64")
    failed = ~blank & out.isna()
    return out, failed


def to_int(value: Any) -> int:
    """A pandas scalar (numpy int, Int64 cell) → ``int``."""
    return int(value)


def to_date(value: Any) -> date:
    """A pandas object cell known to hold a ``date``."""
    if not isinstance(value, date):
        raise TypeError(f"expected date, got {type(value).__name__}")
    return value
