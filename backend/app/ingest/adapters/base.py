"""Adapter contract (02 §1): ``read(path) -> RawFrame`` then
``normalize(raw, ctx) -> NormFrame``.

Rules every adapter follows
---------------------------
* **Read only.** Adapters never touch DuckDB and never match SKUs. They return
  frames whose columns are the ``schema.sql`` column names of the target table,
  plus the data issues they saw (codes from 02 §3.1).
* **Source identity is kept.** Rows whose SKU is not a platform SKU yet
  (inventory: internal code, cost: free text name) carry ``sku_id = None`` and
  the original identity in ``src_raw_code`` / ``src_raw_name``. Every column
  prefixed ``src_`` is adapter metadata, not a schema column: the pipeline uses
  it (SKU matching, restock hour) and drops it before upsert
  (:meth:`NormFrame.schema_frame`).
* **PII is dropped while reading** (``app.ingest.pii``), so it never reaches a
  DataFrame, a log or the DB.
* **Time is UTC** (``datetime64[ns, UTC]`` columns); ``DATE`` columns hold VN
  calendar dates as :class:`datetime.date`.
* **Money is int VND** (``Int64`` columns, ``<NA>`` when unreadable).
"""

from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

SRC_PREFIX = "src_"


class SourceType(StrEnum):
    ORDERS = "orders"
    ADS = "ads"
    INVENTORY = "inventory"
    COST = "cost"
    CAMPAIGNS = "campaigns"
    SALE_PROGRAM = "sale_program"


Severity = Literal["info", "warn", "error"]
InventoryVariant = Literal["day", "hour"]

# 02 §3.1 + codes added by the adapters (docs/quyet-dinh/M1-adapter.md §4).
ISSUE_SEVERITY: dict[str, Severity] = {
    "DUP_ORDER": "warn",
    "ORDER_TOTAL_MISMATCH": "warn",
    "PROVISIONAL_STATUS": "info",
    "NEG_STOCK": "error",
    "STOCK_JUMP": "warn",
    "STOCK_ADJUSTMENT": "info",
    "MISSING_COST": "error",
    "MONEY_FORMAT": "error",
    "COST_NOTE_REVIEW": "info",
    "ADS_TZ_SHIFT": "warn",
    "ADS_GAP_DAY": "warn",
    "ADS_LAG": "info",
    "SPEND_OVER_BUDGET": "warn",
    "UNMATCHED_SKU": "error",
    "BUNDLE_NO_STOCK_ROW": "warn",
    "PII_DROPPED": "info",
    "SUSPICIOUS_NOTE": "warn",
    "UNKNOWN_SOURCE": "error",
    "UNPARSED_RULE": "error",
    # [M1-adapter] not in 02 §3.1, see decision log
    "ROW_INVALID": "error",  # required field unreadable (date, qty, id) → row skipped
    "UNKNOWN_STATUS": "warn",  # order/ads status text not in the mapping table
    "DUP_ROW": "warn",  # same natural key twice in a non-order file → last row kept
    "MISSING_COLUMNS": "error",  # file detected but a required column is absent
}


class DataIssue(BaseModel):
    """One row for ``data_quality_issues`` minus the fields the pipeline owns
    (``issue_id``, ``batch_id``, ``created_at``)."""

    code: str
    severity: Severity
    entity_type: str | None = None  # order|sku|campaign|snapshot|file
    entity_id: str | None = None
    row_ref: str | None = None  # "line 1043" (1 = header line) or a key
    detail: dict[str, Any] = Field(default_factory=dict)
    status: Literal["open", "auto_fixed", "confirmed", "dismissed"] = "open"


def make_issue(
    code: str,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    row_ref: str | None = None,
    detail: dict[str, Any] | None = None,
    status: Literal["open", "auto_fixed", "confirmed", "dismissed"] = "open",
    severity: Severity | None = None,
) -> DataIssue:
    return DataIssue(
        code=code,
        severity=severity or ISSUE_SEVERITY.get(code, "warn"),
        entity_type=entity_type,
        entity_id=entity_id,
        row_ref=row_ref,
        detail=detail or {},
        status=status,
    )


def line_ref(index: int) -> str:
    """Row index of the data frame (0-based) → file line (header is line 1)."""
    return f"line {index + 2}"


class RawFrame(BaseModel):
    """What ``read`` returns: original columns, PII already dropped."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_type: SourceType
    df: pd.DataFrame  # CSV: every cell as str ('' when empty). JSON: empty frame
    file_name: str
    file_sha256: str
    dropped_columns: list[str] = Field(default_factory=list)
    variant: InventoryVariant | None = None  # inventory only: day | hour snapshot
    payload: Any = None  # JSON sources: the parsed document


class IngestContext(BaseModel):
    """Per-batch inputs (02 §1). The pipeline fills what it knows; adapters
    work with the defaults when a field is missing."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    batch_id: str
    received_at: datetime  # aware UTC; cost_items.valid_from
    as_of: datetime | None = None
    mode: Literal["history", "live"] = "history"
    # Confirmed offset per source (giờ_VN = giờ_file + offset). If "ads" is
    # absent the ads adapter detects it from ``orders_hour_profile``.
    tz_offsets: dict[str, int] = Field(default_factory=dict)
    # 24 order counts by VN hour of day (timezone_check.orders_hour_profile).
    orders_hour_profile: list[float] | None = None
    # Exclusive end of the last order hour (orders NormFrame.as_of) → ADS_LAG.
    orders_as_of: datetime | None = None
    sku_resolver: Any = None  # unused by adapters (SKU matching is a later step)


class NormFrame(BaseModel):
    """What ``normalize`` returns.

    ``tables`` maps a schema table name to a frame whose columns are that
    table's column names (+ optional ``src_*`` metadata). ``source_items`` lists
    the distinct source identities for SKU matching: columns ``source_type,
    raw_code, raw_name, product_name, variant_name, list_price, n_rows, price``
    (``price`` = unit cost, cost source only; ``raw_code`` for cost is
    ``row:<file line>``)
    (``raw_code`` is ``''`` when the source has no code, like ``sku_aliases``).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source_type: SourceType
    variant: InventoryVariant | None = None
    file_name: str
    file_sha256: str
    batch_id: str
    tables: dict[str, pd.DataFrame] = Field(default_factory=dict)
    source_items: pd.DataFrame = Field(default_factory=pd.DataFrame)
    extras: dict[str, pd.DataFrame] = Field(default_factory=dict)
    issues: list[DataIssue] = Field(default_factory=list)
    dropped_columns: list[str] = Field(default_factory=list)
    # Columns holding seller/buyer free text: store verbatim but treat as
    # untrusted data (07 §9: trust='untrusted'), never as instructions.
    untrusted_columns: dict[str, list[str]] = Field(default_factory=dict)
    as_of: datetime | None = None  # exclusive end of the last hour with data (UTC)
    tz_offset_hours: int = 0
    row_count: int = 0
    detail: dict[str, Any] = Field(default_factory=dict)  # e.g. timezone check result

    def schema_frame(self, table: str) -> pd.DataFrame:
        """The table frame without adapter metadata (``src_*`` columns)."""
        df = self.tables[table]
        return df[[c for c in df.columns if not str(c).startswith(SRC_PREFIX)]]

    def issue_codes(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.issues:
            out[i.code] = out.get(i.code, 0) + 1
        return out


@runtime_checkable
class SourceAdapter(Protocol):
    source_type: ClassVar[SourceType]
    header_signature: ClassVar[frozenset[str]]

    def read(self, path: Path) -> RawFrame: ...

    def normalize(self, raw: RawFrame, ctx: IngestContext) -> NormFrame: ...


# ------------------------------------------------------------------ helpers
def nfc(text: str) -> str:
    """Header/cell text in NFC (some exports write Vietnamese decomposed)."""
    return unicodedata.normalize("NFC", text)


def clean_header(name: str) -> str:
    return nfc(name.replace("﻿", "")).strip()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        row = next(csv.reader(f), [])
    return [clean_header(c) for c in row]


def read_csv_str(path: Path, keep: list[str]) -> pd.DataFrame:
    """Read only ``keep`` columns (cleaned header names), every cell as str.

    Columns not in ``keep`` (PII) are never materialised: ``usecols`` filters
    them inside the CSV parser.
    """
    keep_set = set(keep)
    df = pd.read_csv(
        path,
        encoding="utf-8-sig",
        dtype=str,
        keep_default_na=False,
        na_filter=False,
        usecols=lambda c: clean_header(str(c)) in keep_set,
    )
    df.columns = [clean_header(str(c)) for c in df.columns]
    return df.map(lambda v: nfc(v) if isinstance(v, str) else v)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def missing_columns_issue(missing: set[str], file_name: str) -> DataIssue:
    return make_issue(
        "MISSING_COLUMNS",
        entity_type="file",
        entity_id=file_name,
        detail={"missing": sorted(missing)},
    )


def empty_items() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "source_type",
            "raw_code",
            "raw_name",
            "product_name",
            "variant_name",
            "list_price",
            "n_rows",
            "price",
        ]
    )
