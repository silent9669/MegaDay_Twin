"""Hand-typed cost sheet → ``cost_items``. Plan 02 §2.4.

No code column: the product is a free-text name ("Bình  nước  GN 500ml - xanh",
unaccented, abbreviated…). The adapter keeps that text **verbatim** in
``src_raw_name`` (and in ``source_items.raw_name``) for SKU matching, with
``raw_code = "row:<file line>"`` so two rows typed with the same name stay two
aliases (``sku_aliases`` is UNIQUE on source/code/name); it does
not normalise or match it. Money goes through ``app.core.money.parse_vnd``.

Issues: ``MONEY_FORMAT`` (unit cost unreadable → row left out of
``cost_items``; packaging unreadable → 0), ``COST_NOTE_REVIEW`` (note says the
price needs a look), ``DUP_ROW`` (same name twice → last kept).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import pandas as pd
from unidecode import unidecode

from app.core.money import parse_vnd
from app.ingest.adapters.base import (
    DataIssue,
    IngestContext,
    NormFrame,
    RawFrame,
    SourceType,
    clean_header,
    file_sha256,
    line_ref,
    make_issue,
    missing_columns_issue,
    read_csv_header,
    read_csv_str,
)
from app.ingest.pii import scan_note, split_pii_columns

C_NAME = "tên sp"
C_COST = "giá vốn"
C_PACK = "phí đóng gói"
C_NOTE = "ghi chú"

# Notes that mean "this number is not final" (unaccented, lowercase).
_REVIEW_NOTE = re.compile(
    r"vat|gia cu|cap nhat|tam tinh|chua chot|chua chinh xac|kiem tra lai|uoc tinh|chua gom"
)

COST_COLUMNS = ["sku_id", "valid_from", "unit_cost", "packaging_cost", "note", "source"]


def needs_review(note: str) -> bool:
    return bool(_REVIEW_NOTE.search(unidecode(note).lower()))


class CostAdapter:
    source_type: ClassVar[SourceType] = SourceType.COST
    header_signature: ClassVar[frozenset[str]] = frozenset({C_NAME, C_COST})

    def read(self, path: Path) -> RawFrame:
        path = Path(path)
        kept, dropped = split_pii_columns(read_csv_header(path))
        df = read_csv_str(path, kept)
        # the sheet is typed by hand: accept "Tên SP", "Giá vốn", …
        canon = {c.casefold(): c for c in (C_NAME, C_COST, C_PACK, C_NOTE)}
        df.columns = [canon.get(clean_header(c).casefold(), c) for c in df.columns]
        return RawFrame(
            source_type=self.source_type,
            df=df,
            file_name=path.name,
            file_sha256=file_sha256(path),
            dropped_columns=dropped,
        )

    def normalize(self, raw: RawFrame, ctx: IngestContext) -> NormFrame:
        df = raw.df.copy()
        issues: list[DataIssue] = []
        out = NormFrame(
            source_type=self.source_type,
            file_name=raw.file_name,
            file_sha256=raw.file_sha256,
            batch_id=ctx.batch_id,
            dropped_columns=list(raw.dropped_columns),
            row_count=len(df),
            untrusted_columns={"cost_items": ["note"]},
        )
        missing = {C_NAME, C_COST} - set(df.columns)
        if missing:
            out.issues = [missing_columns_issue(missing, raw.file_name)]
            return out
        for c in (C_PACK, C_NOTE):
            if c not in df.columns:
                df[c] = ""

        rows = []
        for idx in df.index:
            name = str(df.at[idx, C_NAME])
            if not name.strip():
                if any(str(df.at[idx, c]).strip() for c in df.columns):
                    issues.append(
                        make_issue(
                            "ROW_INVALID",
                            entity_type="sku",
                            row_ref=line_ref(int(idx)),
                            detail={"columns": [C_NAME]},
                        )
                    )
                continue
            ref = line_ref(int(idx))
            cost_text = str(df.at[idx, C_COST])
            unit = parse_vnd(cost_text)
            if unit is None or unit < 0:
                issues.append(
                    make_issue(
                        "MONEY_FORMAT",
                        entity_type="sku",
                        entity_id=name,
                        row_ref=ref,
                        detail={"column": C_COST, "value": cost_text},
                    )
                )
            pack_text = str(df.at[idx, C_PACK])
            pack = parse_vnd(pack_text) if pack_text.strip() else 0
            if pack is None or pack < 0:
                issues.append(
                    make_issue(
                        "MONEY_FORMAT",
                        entity_type="sku",
                        entity_id=name,
                        row_ref=ref,
                        detail={"column": C_PACK, "value": pack_text, "used": 0},
                        status="auto_fixed",
                    )
                )
                pack = 0
            note = scan_note(str(df.at[idx, C_NOTE]))
            if note.text and needs_review(note.text):
                issues.append(
                    make_issue(
                        "COST_NOTE_REVIEW",
                        entity_type="sku",
                        entity_id=name,
                        row_ref=ref,
                        detail={"note": note.text, "trust": "untrusted"},
                    )
                )
            if note.flagged:
                issues.append(
                    make_issue(
                        "SUSPICIOUS_NOTE",
                        entity_type="sku",
                        entity_id=name,
                        row_ref=ref,
                        detail={"patterns": note.patterns, "column": C_NOTE, "trust": "untrusted"},
                    )
                )
            rows.append(
                {
                    "sku_id": None,
                    "valid_from": ctx.received_at,
                    "unit_cost": unit if unit is not None and unit >= 0 else None,
                    "packaging_cost": pack,
                    "note": note.text,
                    "source": "file",
                    "src_raw_code": f"row:{int(idx) + 2}",
                    "src_raw_name": name,
                    "src_line": ref,
                }
            )

        items_df = pd.DataFrame(
            rows, columns=[*COST_COLUMNS, "src_raw_code", "src_raw_name", "src_line"]
        )
        # Same name twice with a different price is legitimate (two SKUs typed
        # alike), so only fully identical rows count as duplicates.
        dup = items_df.duplicated(
            subset=["src_raw_name", "unit_cost", "packaging_cost", "note"], keep="first"
        )
        for idx in items_df.index[dup]:
            issues.append(
                make_issue(
                    "DUP_ROW",
                    entity_type="sku",
                    entity_id=str(items_df.at[idx, "src_raw_name"]),
                    row_ref=str(items_df.at[idx, "src_line"]),
                    detail={"key": [C_NAME, C_COST, C_PACK, C_NOTE]},
                    status="auto_fixed",
                )
            )
        items_df = items_df[~dup]
        cost = items_df[items_df["unit_cost"].notna()].copy()
        cost["unit_cost"] = cost["unit_cost"].astype("int64")
        cost["packaging_cost"] = cost["packaging_cost"].astype("int64")
        cost["valid_from"] = pd.to_datetime(cost["valid_from"], utc=True)

        source_items = pd.DataFrame(
            {
                "source_type": self.source_type.value,
                "raw_code": items_df["src_raw_code"],
                "raw_name": items_df["src_raw_name"],
                "product_name": None,
                "variant_name": None,
                "list_price": pd.Series(pd.NA, index=items_df.index, dtype="Int64"),
                "n_rows": 1,
                "price": items_df["unit_cost"].astype("Int64"),
            }
        ).reset_index(drop=True)

        out.tables = {"cost_items": cost.reset_index(drop=True)}
        out.source_items = source_items
        out.issues = issues
        out.as_of = ctx.received_at
        return out
