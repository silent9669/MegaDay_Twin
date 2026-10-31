"""Recognise a seller file by its header, never by its file name (02 §1).

Seven file kinds: orders, ads, inventory (daily snapshot ``Ngày`` / hourly live
snapshot ``Thời điểm cập nhật``), cost, campaigns (JSON array), sale program
(JSON object). Anything else → ``UNKNOWN_SOURCE`` so the UI/AI can propose a
column mapping (07, AI-05).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.ingest.adapters.base import (
    DataIssue,
    InventoryVariant,
    SourceType,
    clean_header,
    make_issue,
    read_csv_header,
)
from app.ingest.adapters.inventory import INVENTORY_DAY_COLUMN, INVENTORY_HOUR_COLUMN

# Required columns per CSV source (02 §1 table).
CSV_SIGNATURES: dict[SourceType, frozenset[str]] = {
    SourceType.ORDERS: frozenset({"Mã đơn hàng", "SKU phân loại hàng", "Số lượng"}),
    SourceType.ADS: frozenset({"Mã chiến dịch", "Chi phí", "Lượt click"}),
    SourceType.INVENTORY: frozenset({"Mã nội bộ", "Tồn thực tế"}),
    SourceType.COST: frozenset({"tên sp", "giá vốn"}),
}
CAMPAIGN_KEYS = frozenset({"ma_chien_dich", "sku"})
SALE_PROGRAM_KEYS = frozenset({"cam_ket", "khung_flash_sale"})


class Detection(BaseModel):
    source_type: SourceType | None
    variant: InventoryVariant | None = None
    columns: list[str] = Field(default_factory=list)  # CSV header or JSON keys seen
    issue: DataIssue | None = None  # UNKNOWN_SOURCE when source_type is None

    @property
    def known(self) -> bool:
        return self.source_type is not None


def _norm_set(columns: Iterable[str]) -> set[str]:
    # Header matching ignores case (the cost sheet is typed by hand).
    return {clean_header(c).casefold() for c in columns}


def detect_columns(columns: Iterable[str], file_name: str = "") -> Detection:
    """Classify a CSV by its header names."""
    cols = [clean_header(c) for c in columns]
    have = _norm_set(cols)
    for source, signature in CSV_SIGNATURES.items():
        if {s.casefold() for s in signature} <= have:
            variant: InventoryVariant | None = None
            if source is SourceType.INVENTORY:
                if INVENTORY_HOUR_COLUMN.casefold() in have:
                    variant = "hour"
                elif INVENTORY_DAY_COLUMN.casefold() in have:
                    variant = "day"
                else:
                    return _unknown(cols, file_name, "inventory_without_time_column")
            return Detection(source_type=source, variant=variant, columns=cols)
    return _unknown(cols, file_name, "no_signature_matched")


def detect_json(payload: Any, file_name: str = "") -> Detection:
    """Classify a parsed JSON document."""
    if isinstance(payload, list):
        first = next((x for x in payload if isinstance(x, dict)), None)
        keys = sorted(first) if first else []
        if first is not None and set(first) >= CAMPAIGN_KEYS:
            return Detection(source_type=SourceType.CAMPAIGNS, columns=keys)
        if first is not None and set(first) >= SALE_PROGRAM_KEYS:
            return Detection(source_type=SourceType.SALE_PROGRAM, columns=keys)
        return _unknown(keys, file_name, "json_array_unknown")
    if isinstance(payload, dict):
        keys = sorted(payload)
        if set(payload) >= SALE_PROGRAM_KEYS:
            return Detection(source_type=SourceType.SALE_PROGRAM, columns=keys)
        return _unknown(keys, file_name, "json_object_unknown")
    return _unknown([], file_name, "json_not_object")


def detect_source(path: Path) -> Detection:
    """Read the first bytes of ``path`` and classify it."""
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", errors="replace") as f:
        head = f.read(64).lstrip()
    if head.startswith(("[", "{")):
        try:
            with path.open("r", encoding="utf-8-sig") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _unknown([], path.name, "invalid_json")
        return detect_json(payload, path.name)
    try:
        header = read_csv_header(path)
    except UnicodeDecodeError:
        return _unknown([], path.name, "not_utf8_text")
    return detect_columns(header, path.name)


def _unknown(columns: list[str], file_name: str, reason: str) -> Detection:
    return Detection(
        source_type=None,
        columns=columns,
        issue=make_issue(
            "UNKNOWN_SOURCE",
            entity_type="file",
            entity_id=file_name or None,
            detail={"reason": reason, "columns": columns[:50]},
        ),
    )
