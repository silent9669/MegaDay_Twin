"""Stock Google Sheet → ``inventory_snapshots``, ``restock_plans``,
``restock_receipts``. Plan 02 §2.3.

Two layouts, told apart by the time column (``app.ingest.detect``):

* history ``Ngày``: end-of-day snapshot → ``snapshot_at`` = next day 00:00 VN.
* live ``Thời điểm cập nhật``: end-of-hour snapshot (``'2026-11-11 01:00'`` =
  stock at 01:00, after hour 00) → that instant.

Rows use the internal code (``Mã nội bộ``), not the platform SKU, so
``sku_id`` is ``None`` and the identity travels in ``src_raw_code`` /
``src_raw_name`` for SKU matching.

Notes (``Ghi chú``, several entries joined by ``;``): ``Nhập {qty}`` is a real
receipt → ``restock_receipts``; anything else → ``STOCK_ADJUSTMENT``. Notes are
stored verbatim (contacts masked) and are untrusted text. Live snapshots repeat
the day's notes every hour, so receipts are de-duplicated per (code, day) by
taking, for each quantity, the highest count seen in one snapshot.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
from unidecode import unidecode

from app.core.timeutil import VN_TZ
from app.ingest.adapters._common import (
    LOCAL_DATE_FORMAT,
    parse_int_series,
    parse_local_series,
    to_date,
    to_int,
)
from app.ingest.adapters.base import (
    DataIssue,
    IngestContext,
    InventoryVariant,
    NormFrame,
    RawFrame,
    SourceType,
    file_sha256,
    line_ref,
    make_issue,
    missing_columns_issue,
    read_csv_header,
    read_csv_str,
)
from app.ingest.pii import scan_note, split_pii_columns

INVENTORY_DAY_COLUMN = "Ngày"
INVENTORY_HOUR_COLUMN = "Thời điểm cập nhật"
C_CODE = "Mã nội bộ"
C_NAME = "Tên hàng"
C_STOCK = "Tồn thực tế"
C_INCOMING = "Đang về"
C_PLANNED = "Ngày dự kiến về"
C_NOTE = "Ghi chú"
OPTIONAL = (C_NAME, C_INCOMING, C_PLANNED, C_NOTE)

_RECEIPT = re.compile(r"^\s*nhap(?:\s+hang)?\s*[:\-]?\s*\+?(\d[\d.,]*)\s*(?:sp|cai|chiec)?\s*$")
_NUMBER = re.compile(r"-?\d+")

SNAPSHOT_COLUMNS = [
    "sku_id",
    "snapshot_at",
    "on_hand",
    "quality_flag",
    "note",
    "granularity",
    "batch_id",
]


def parse_receipt(entry: str) -> int | None:
    """``"Nhập 380"`` → 380; anything else → None."""
    m = _RECEIPT.match(unidecode(entry).lower())
    if not m:
        return None
    digits = re.sub(r"[.,]", "", m.group(1))
    return int(digits) if digits.isdigit() and int(digits) > 0 else None


def split_note(note: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;\n]", note) if p.strip()]


class InventoryAdapter:
    source_type: ClassVar[SourceType] = SourceType.INVENTORY
    header_signature: ClassVar[frozenset[str]] = frozenset({C_CODE, C_STOCK})

    def read(self, path: Path) -> RawFrame:
        path = Path(path)
        header = read_csv_header(path)
        kept, dropped = split_pii_columns(header)
        variant: InventoryVariant = "hour" if INVENTORY_HOUR_COLUMN in header else "day"
        return RawFrame(
            source_type=self.source_type,
            df=read_csv_str(path, kept),
            file_name=path.name,
            file_sha256=file_sha256(path),
            dropped_columns=dropped,
            variant=variant,
        )

    def normalize(self, raw: RawFrame, ctx: IngestContext) -> NormFrame:
        df = raw.df.copy()
        variant: InventoryVariant | None = raw.variant
        if variant is None:
            variant = "hour" if INVENTORY_HOUR_COLUMN in df.columns else "day"
        time_col = INVENTORY_HOUR_COLUMN if variant == "hour" else INVENTORY_DAY_COLUMN
        issues: list[DataIssue] = []
        out = NormFrame(
            source_type=self.source_type,
            variant=variant,
            file_name=raw.file_name,
            file_sha256=raw.file_sha256,
            batch_id=ctx.batch_id,
            dropped_columns=list(raw.dropped_columns),
            row_count=len(df),
            untrusted_columns={"inventory_snapshots": ["note"]},
        )
        missing = {time_col, C_CODE, C_STOCK} - set(df.columns)
        if missing:
            out.issues = [missing_columns_issue(missing, raw.file_name)]
            return out
        for c in OPTIONAL:
            if c not in df.columns:
                df[c] = ""
        df[C_CODE] = df[C_CODE].str.strip()

        # ---- snapshot instant (UTC)
        if variant == "day":
            day = pd.to_datetime(
                df[time_col].str.strip(), format=LOCAL_DATE_FORMAT, errors="coerce"
            )
            snap = (day + timedelta(days=1)).dt.tz_localize(VN_TZ).dt.tz_convert("UTC")
        else:
            snap = parse_local_series(df[time_col])
        bad = snap.isna() | (df[C_CODE] == "")
        for idx in df.index[bad]:
            issues.append(
                make_issue(
                    "ROW_INVALID",
                    entity_type="snapshot",
                    entity_id=str(df.at[idx, C_CODE]) or None,
                    row_ref=line_ref(int(idx)),
                    detail={"columns": [time_col, C_CODE]},
                )
            )
        df, snap = df[~bad], snap[~bad]
        # the VN day the snapshot closes (end of day d / end of hour h on day d)
        closes_day = (snap - pd.Timedelta(hours=1)).dt.tz_convert(VN_TZ).dt.date

        # ---- stock
        stock = parse_int_series(df[C_STOCK])
        on_hand = stock.copy()
        flag = pd.Series(None, index=df.index, dtype="object")
        neg = stock.notna() & (stock < 0)
        for idx in df.index[neg]:
            issues.append(
                make_issue(
                    "NEG_STOCK",
                    entity_type="snapshot",
                    entity_id=str(df.at[idx, C_CODE]),
                    row_ref=line_ref(int(idx)),
                    detail={
                        "value": to_int(stock.at[idx]),
                        "raw_code": str(df.at[idx, C_CODE]),
                        "date": str(df.at[idx, time_col]).strip(),
                        "action": "set_null_interpolate",
                    },
                    status="auto_fixed",
                )
            )
        on_hand[neg] = pd.NA
        flag[neg] = "negative"
        unreadable = stock.isna() & (df[C_STOCK].str.strip() != "")
        for idx in df.index[unreadable]:
            issues.append(
                make_issue(
                    "ROW_INVALID",
                    entity_type="snapshot",
                    entity_id=str(df.at[idx, C_CODE]),
                    row_ref=line_ref(int(idx)),
                    detail={"columns": [C_STOCK], "value": str(df.at[idx, C_STOCK])},
                )
            )

        # ---- notes
        notes = df[C_NOTE].fillna("")
        scans = {t: scan_note(t) for t in notes.unique()}
        masked = notes.map(lambda t: scans[t].text)

        snapshots = pd.DataFrame(
            {
                "sku_id": pd.Series(None, index=df.index, dtype="object"),
                "snapshot_at": snap,
                "on_hand": on_hand.astype("Int64"),
                "quality_flag": flag,
                "note": masked,
                "granularity": variant,
                "batch_id": ctx.batch_id,
                "src_raw_code": df[C_CODE],
                "src_raw_name": df[C_NAME].str.strip(),
            }
        )
        dup = snapshots.duplicated(subset=["src_raw_code", "snapshot_at"], keep="last")
        for idx in snapshots.index[dup]:
            issues.append(
                make_issue(
                    "DUP_ROW",
                    entity_type="snapshot",
                    entity_id=str(snapshots.at[idx, "src_raw_code"]),
                    row_ref=line_ref(int(idx)),
                    detail={"key": ["Mã nội bộ", time_col]},
                    status="auto_fixed",
                )
            )
        keep = ~dup
        snapshots = snapshots[keep]
        df, snap, closes_day, notes = df[keep], snap[keep], closes_day[keep], notes[keep]

        for idx in df.index:
            sc = scans[notes.at[idx]]
            if sc.flagged:
                issues.append(
                    make_issue(
                        "SUSPICIOUS_NOTE",
                        entity_type="snapshot",
                        entity_id=str(df.at[idx, C_CODE]),
                        row_ref=line_ref(int(idx)),
                        detail={"patterns": sc.patterns, "column": C_NOTE, "trust": "untrusted"},
                    )
                )

        plans, plan_issues = self._plans(df, snap, time_col)
        issues.extend(plan_issues)
        receipts, adj_issues = self._receipts(df, snap, closes_day, notes, plans, time_col)
        issues.extend(adj_issues)

        items = (
            snapshots.groupby("src_raw_code", sort=True)
            .agg(raw_name=("src_raw_name", "last"), n_rows=("src_raw_name", "size"))
            .reset_index()
            .rename(columns={"src_raw_code": "raw_code"})
        )
        items.insert(0, "source_type", self.source_type.value)
        items["product_name"] = None
        items["variant_name"] = None
        items["list_price"] = pd.Series(pd.NA, index=items.index, dtype="Int64")
        items["price"] = pd.Series(pd.NA, index=items.index, dtype="Int64")
        items = items[
            [
                "source_type",
                "raw_code",
                "raw_name",
                "product_name",
                "variant_name",
                "list_price",
                "n_rows",
                "price",
            ]
        ]

        out.tables = {
            "inventory_snapshots": snapshots[[*SNAPSHOT_COLUMNS, "src_raw_code", "src_raw_name"]]
            .sort_values(["src_raw_code", "snapshot_at"], kind="stable")
            .reset_index(drop=True),
            "restock_plans": plans,
            "restock_receipts": receipts,
        }
        out.source_items = items
        out.issues = issues
        if len(snapshots):
            out.as_of = snapshots["snapshot_at"].max().to_pydatetime()
        return out

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _plans(
        df: pd.DataFrame, snap: pd.Series, time_col: str
    ) -> tuple[pd.DataFrame, list[DataIssue]]:
        issues: list[DataIssue] = []
        has = (df[C_INCOMING].str.strip() != "") | (df[C_PLANNED].str.strip() != "")
        qty = parse_int_series(df[C_INCOMING])
        planned = pd.to_datetime(
            df[C_PLANNED].str.strip(), format=LOCAL_DATE_FORMAT, errors="coerce"
        ).dt.date
        ok = has & qty.notna() & (qty.fillna(0) > 0) & planned.notna()
        for idx in df.index[has & ~ok]:
            issues.append(
                make_issue(
                    "ROW_INVALID",
                    entity_type="snapshot",
                    entity_id=str(df.at[idx, C_CODE]),
                    row_ref=line_ref(int(idx)),
                    detail={
                        "columns": [C_INCOMING, C_PLANNED],
                        "value": [str(df.at[idx, C_INCOMING]), str(df.at[idx, C_PLANNED])],
                    },
                )
            )
        plans = pd.DataFrame(
            {
                "sku_id": pd.Series(None, index=df.index[ok], dtype="object"),
                "seen_at": snap[ok],
                "qty_incoming": qty[ok].astype("Int64"),
                "planned_date": planned[ok],
                "src_raw_code": df.loc[ok, C_CODE],
            }
        )
        return plans.sort_values(["src_raw_code", "seen_at"], kind="stable").reset_index(
            drop=True
        ), issues

    @staticmethod
    def _receipts(
        df: pd.DataFrame,
        snap: pd.Series,
        closes_day: pd.Series,
        notes: pd.Series,
        plans: pd.DataFrame,
        time_col: str,
    ) -> tuple[pd.DataFrame, list[DataIssue]]:
        issues: list[DataIssue] = []
        # (code, day) → qty → max multiplicity in one snapshot; first instant seen
        found: dict[tuple[str, date], Counter[int]] = {}
        first_seen: dict[tuple[str, date], Any] = {}
        adjustments_seen: set[tuple[str, date, str]] = set()
        has_note = notes.str.strip() != ""
        for idx in df.index[has_note]:
            code = str(df.at[idx, C_CODE])
            day = to_date(closes_day.at[idx])
            counts: Counter[int] = Counter()
            for entry in split_note(str(notes.at[idx])):
                q = parse_receipt(entry)
                if q is not None:
                    counts[q] += 1
                    continue
                key = (code, day, entry)
                if key in adjustments_seen:
                    continue
                adjustments_seen.add(key)
                masked = scan_note(entry).text or ""
                issues.append(
                    make_issue(
                        "STOCK_ADJUSTMENT",
                        entity_type="snapshot",
                        entity_id=code,
                        row_ref=line_ref(int(idx)),
                        detail={
                            "date": day.isoformat(),
                            "note": masked,
                            "numbers": [int(n) for n in _NUMBER.findall(masked)][:5],
                            "trust": "untrusted",
                        },
                    )
                )
            if counts:
                acc = found.setdefault((code, day), Counter())
                for q, n in counts.items():
                    acc[q] = max(acc[q], n)
                first_seen.setdefault((code, day), snap.at[idx])

        rows = []
        for (code, day), counts in found.items():
            seen_at = first_seen[(code, day)]
            rows.append(
                {
                    "sku_id": None,
                    "received_on": day,
                    "qty": sum(q * n for q, n in counts.items()),
                    "planned_date": _nearest_plan(plans, code, seen_at),
                    "src_raw_code": code,
                    "src_first_seen_at": seen_at,
                    "src_entries": sum(counts.values()),
                }
            )
        cols = [
            "sku_id",
            "received_on",
            "qty",
            "planned_date",
            "src_raw_code",
            "src_first_seen_at",
            "src_entries",
        ]
        receipts = pd.DataFrame(rows, columns=cols)
        if len(receipts):
            receipts["qty"] = receipts["qty"].astype("Int64")
            receipts = receipts.sort_values(["src_raw_code", "received_on"], kind="stable")
        return receipts.reset_index(drop=True), issues


def _nearest_plan(plans: pd.DataFrame, code: str, seen_at: Any) -> date | None:
    """Planned date of the latest plan seen strictly before the receipt
    snapshot (the plan disappears from the sheet once the goods arrive)."""
    if not len(plans):
        return None
    p = plans[(plans["src_raw_code"] == code) & (plans["seen_at"] < seen_at)]
    if not len(p):
        return None
    value = p.iloc[-1]["planned_date"]
    return value if isinstance(value, date) else None
