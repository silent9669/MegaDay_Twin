"""Column contract of ``hourly_panel`` and :func:`validate_panel`.

Plan: 01 §7 (the panel is the only DataFrame that crosses a layer boundary,
``panel`` → ``forecast`` / ``ads_effect``), 02 §6 (DDL). The column list below
mirrors ``app/warehouse/schema.sql``; a test checks the two never drift apart.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
import pandas as pd

from app.core.errors import DataQualityError

Kind = Literal["str", "ts", "date", "int", "float", "bool", "list"]

# (column, kind, nullable) in DDL order.
PANEL_COLUMNS: tuple[tuple[str, Kind, bool], ...] = (
    ("sku_id", "str", False),
    ("hour_utc", "ts", False),
    ("hour_local", "int", False),
    ("dow_local", "int", False),
    ("day_local", "date", False),
    ("units_gross", "int", False),
    ("units_provisional", "int", False),
    ("units_cancelled", "int", False),
    ("orders_count", "int", False),
    ("revenue_net", "int", False),
    ("list_price", "int", True),
    ("eff_price", "int", True),
    ("discount_pct", "float", True),
    ("stock_begin", "int", True),
    ("stock_end", "int", True),
    ("stock_source", "str", True),
    ("stockout", "bool", False),
    ("stockout_conf", "float", True),
    ("low_stock", "bool", False),
    ("restock_arrived", "int", True),
    ("campaign_id", "str", True),
    ("ad_status", "str", True),
    ("ad_spend_alloc", "float", True),
    ("ad_alloc_share", "float", True),
    ("attributed_orders_alloc", "float", True),
    ("is_double_day", "bool", False),
    ("is_pre_double_day", "bool", False),
    ("is_post_double_day", "bool", False),
    ("is_payday", "bool", False),
    ("in_flash_window", "bool", False),
    ("days_to_tet", "int", True),
    ("days_to_gift_peak", "int", True),
    ("data_flags", "list", True),
    ("panel_version", "str", False),
)

COLUMN_NAMES: tuple[str, ...] = tuple(c for c, _, _ in PANEL_COLUMNS)

STOCK_SOURCES = frozenset({"snapshot_hour", "reconstructed", "interpolated"})
AD_STATUSES = frozenset({"running", "paused", "budget_exhausted", "unknown", "none"})

# data_flags vocabulary (02 §6 example: {'ADS_GAP_DAY','PROVISIONAL'}).
FLAG_PROVISIONAL = "PROVISIONAL"
FLAG_ADS_GAP_DAY = "ADS_GAP_DAY"  # ads report missing a run of >= 20 hours inside history
FLAG_ADS_MISSING_HOUR = "ADS_MISSING_HOUR"  # shorter hole inside the ads history
FLAG_ADS_LAG = "ADS_LAG"  # after the last hour the ads report covers
FLAG_STOCK_INTERPOLATED = "STOCK_INTERPOLATED"
FLAG_STOCK_JUMP = "STOCK_JUMP"
FLAG_NO_STOCK_DATA = "NO_STOCK_DATA"
FLAG_BUNDLE_NO_STOCK_ROW = "BUNDLE_NO_STOCK_ROW"


class PanelSchemaError(DataQualityError):
    """The panel DataFrame does not follow the contract."""

    code = "PANEL_SCHEMA"


def _fail(problems: list[str]) -> None:
    raise PanelSchemaError("Bảng panel theo giờ sai cấu trúc: " + "; ".join(problems))


def _check_kind(col: str, kind: Kind, s: pd.Series[Any], problems: list[str]) -> None:
    if kind == "ts":
        if not isinstance(s.dtype, pd.DatetimeTZDtype) or str(s.dtype.tz) != "UTC":
            problems.append(f"{col} phải là datetime có múi giờ UTC (đang là {s.dtype})")
    elif kind == "int":
        if not (pd.api.types.is_integer_dtype(s.dtype) or s.isna().all()):
            problems.append(f"{col} phải là số nguyên (đang là {s.dtype})")
    elif kind == "float":
        if not pd.api.types.is_numeric_dtype(s.dtype) and not s.isna().all():
            problems.append(f"{col} phải là số (đang là {s.dtype})")
    elif kind == "bool" and not pd.api.types.is_bool_dtype(s.dtype):
        problems.append(f"{col} phải là bool (đang là {s.dtype})")


def validate_panel(df: pd.DataFrame, *, require_unique: bool = True) -> pd.DataFrame:
    """Check that ``df`` follows the ``hourly_panel`` contract and return it unchanged.

    Checks: every column present, dtypes (UTC timestamps, integers, booleans),
    NOT NULL columns, hour alignment, value ranges and vocabularies, uniqueness of
    ``(sku_id, hour_utc)``, ``hour_local``/``day_local`` consistent with ``hour_utc``.
    Raises :class:`PanelSchemaError` listing every problem found.
    """
    problems: list[str] = []
    missing = [c for c in COLUMN_NAMES if c not in df.columns]
    if missing:
        _fail([f"thiếu cột {missing}"])
    for col, kind, nullable in PANEL_COLUMNS:
        s = df[col]
        _check_kind(col, kind, s, problems)
        if not nullable and bool(s.isna().any()):
            problems.append(f"{col} không được rỗng")
    if problems:
        _fail(problems)
    if df.empty:
        return df

    hours = df["hour_utc"]
    if bool((hours != hours.dt.floor("h")).any()):
        problems.append("hour_utc phải tròn giờ")
    local = hours.dt.tz_convert("Asia/Ho_Chi_Minh")
    if bool((local.dt.hour.to_numpy() != df["hour_local"].to_numpy()).any()):
        problems.append("hour_local không khớp hour_utc")
    day_local = pd.to_datetime(df["day_local"]).dt.date
    if bool((local.dt.date != day_local).any()):
        problems.append("day_local không khớp hour_utc")
    if bool((~df["dow_local"].between(0, 6)).any()):
        problems.append("dow_local ngoài 0..6")
    for col in ("units_gross", "units_provisional", "units_cancelled", "orders_count"):
        if bool((df[col] < 0).any()):
            problems.append(f"{col} âm")
    if bool((df["units_provisional"] > df["units_gross"]).any()):
        problems.append("units_provisional > units_gross")
    for col in ("stock_begin", "stock_end"):
        if bool((df[col].dropna() < 0).any()):
            problems.append(f"{col} âm")
    src = df["stock_source"].dropna()
    if not set(src.unique()) <= STOCK_SOURCES:
        problems.append(f"stock_source ngoài {sorted(STOCK_SOURCES)}")
    st = df["ad_status"].dropna()
    if not set(st.unique()) <= AD_STATUSES:
        problems.append(f"ad_status ngoài {sorted(AD_STATUSES)}")
    for col in ("stockout_conf", "ad_alloc_share", "discount_pct"):
        v = df[col].dropna().to_numpy(dtype=float)
        if v.size and (np.nanmin(v) < -1e-9 or np.nanmax(v) > 1 + 1e-9):
            problems.append(f"{col} ngoài 0..1")
    unknown = df["ad_status"] == "unknown"
    if bool(df.loc[unknown, "ad_spend_alloc"].notna().any()):
        problems.append("ad_spend_alloc phải rỗng khi ad_status='unknown'")
    if require_unique and bool(df.duplicated(["sku_id", "hour_utc"]).any()):
        problems.append("trùng (sku_id, hour_utc)")
    if problems:
        _fail(problems)
    return df
