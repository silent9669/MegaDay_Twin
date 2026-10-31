"""Seller Center order export → ``orders``, ``order_lines`` (+ ``products``,
``skus`` from the platform SKU, which is the canonical key). Plan 02 §2.1.

Issues: ``PII_DROPPED``, ``DUP_ORDER`` (row identical to an earlier one: one
issue per extra row, the first is kept), ``ORDER_TOTAL_MISMATCH``,
``MONEY_FORMAT``, ``PROVISIONAL_STATUS`` (one per file, with counts),
``SUSPICIOUS_NOTE`` (per order), ``UNKNOWN_STATUS``, ``ROW_INVALID``.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

import pandas as pd

from app.core.timeutil import floor_hour
from app.ingest.adapters._common import (
    parse_int_series,
    parse_local_series,
    parse_money_series,
    to_int,
)
from app.ingest.adapters.base import (
    DataIssue,
    IngestContext,
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
from app.ingest.pii import ScannedNote, scan_note, split_pii_columns
from app.warehouse.repo import derive_product_id

C_ID = "Mã đơn hàng"
C_AT = "Ngày đặt hàng"
C_STATUS = "Trạng thái đơn hàng"
C_PRODUCT = "Tên sản phẩm"
C_VARIANT = "Tên phân loại hàng"
C_SKU = "SKU phân loại hàng"
C_LIST = "Giá gốc"
C_UNIT = "Giá ưu đãi"
C_QTY = "Số lượng"
C_TOTAL = "Tổng tiền hàng"
C_NOTE = "Ghi chú của người mua"
REQUIRED = frozenset({C_ID, C_AT, C_STATUS, C_SKU, C_UNIT, C_QTY})
OPTIONAL = (C_PRODUCT, C_VARIANT, C_LIST, C_TOTAL, C_NOTE)

# 02 §2.1: source text → (status, status_final)
STATUS_MAP: dict[str, tuple[str, bool]] = {
    "Hoàn thành": ("completed", True),
    "Trả hàng/Hoàn tiền": ("returned", True),
    "Đã hủy": ("cancelled", True),
    "Đang giao": ("shipping", False),
    "Chờ lấy hàng": ("awaiting_pickup", False),
    "Chờ xác nhận": ("pending", False),
}
_STATUS_CASEFOLD = {k.casefold(): v for k, v in STATUS_MAP.items()}

ORDERS_COLUMNS = [
    "order_id",
    "ordered_at",
    "status",
    "status_final",
    "status_history",
    "buyer_note_masked",
    "note_flagged",
    "batch_id",
]
LINES_COLUMNS = ["order_id", "line_no", "sku_id", "qty", "list_price", "unit_price", "line_total"]


def is_bundle_sku(sku_id: str) -> bool:
    """Heuristic only for ``product_id``: combos keep their own product (repo
    convention). The authoritative flag comes from ``bundle_components``."""
    return sku_id.upper().startswith("COMBO")


class OrdersAdapter:
    source_type: ClassVar[SourceType] = SourceType.ORDERS
    header_signature: ClassVar[frozenset[str]] = frozenset({C_ID, C_SKU, C_QTY})

    def read(self, path: Path) -> RawFrame:
        path = Path(path)
        kept, dropped = split_pii_columns(read_csv_header(path))
        df = read_csv_str(path, kept)
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
            untrusted_columns={"orders": ["buyer_note_masked"]},
        )
        if raw.dropped_columns:
            issues.append(
                make_issue(
                    "PII_DROPPED",
                    entity_type="file",
                    entity_id=raw.file_name,
                    detail={"columns": list(raw.dropped_columns)},
                    status="auto_fixed",
                )
            )
        missing = set(REQUIRED) - set(df.columns)
        if missing:
            out.issues = [*issues, missing_columns_issue(missing, raw.file_name)]
            return out
        for c in OPTIONAL:
            if c not in df.columns:
                df[c] = ""
        df[C_ID] = df[C_ID].str.strip()
        df[C_SKU] = df[C_SKU].str.strip()

        # ---- DUP_ORDER: identical rows (all non-PII columns), keep the first
        dup = df.duplicated(keep="first")
        if dup.any():
            cols = list(df.columns)
            first_line = df.reset_index().groupby(cols, sort=False)["index"].transform("min")
            first_line.index = df.index
            for idx in df.index[dup]:
                issues.append(
                    make_issue(
                        "DUP_ORDER",
                        entity_type="order",
                        entity_id=str(df.at[idx, C_ID]),
                        row_ref=line_ref(int(idx)),
                        detail={"duplicate_of": line_ref(to_int(first_line.at[idx]))},
                        status="auto_fixed",
                    )
                )
            df = df[~dup]

        # ---- parse
        ordered_at = parse_local_series(df[C_AT])
        qty = parse_int_series(df[C_QTY])
        unit, unit_bad = parse_money_series(df[C_UNIT])
        lst, lst_bad = parse_money_series(df[C_LIST])
        total, total_bad = parse_money_series(df[C_TOTAL])

        checks = {
            C_ID: df[C_ID] == "",
            C_SKU: df[C_SKU] == "",
            C_AT: ordered_at.isna(),
            C_QTY: qty.isna() | (qty.fillna(0) < 1),
            C_UNIT: unit.isna(),
        }
        invalid = pd.concat(checks.values(), axis=1).any(axis=1)
        for idx in df.index[invalid]:
            reasons = [c for c, m in checks.items() if bool(m.at[idx])]
            code = "MONEY_FORMAT" if reasons == [C_UNIT] and unit_bad.at[idx] else "ROW_INVALID"
            issues.append(
                make_issue(
                    code,
                    entity_type="order",
                    entity_id=str(df.at[idx, C_ID]) or None,
                    row_ref=line_ref(int(idx)),
                    detail={"columns": reasons},
                )
            )
        keep = ~invalid
        df, ordered_at, qty, unit = df[keep], ordered_at[keep], qty[keep], unit[keep]
        lst, lst_bad, total, total_bad = lst[keep], lst_bad[keep], total[keep], total_bad[keep]

        for idx in df.index[lst_bad | total_bad]:
            bad = [c for c, m in ((C_LIST, lst_bad), (C_TOTAL, total_bad)) if m.at[idx]]
            issues.append(
                make_issue(
                    "MONEY_FORMAT",
                    entity_type="order",
                    entity_id=str(df.at[idx, C_ID]),
                    row_ref=line_ref(int(idx)),
                    detail={"columns": bad, "value": [str(df.at[idx, c]) for c in bad]},
                    status="auto_fixed",
                )
            )
        lst = lst.fillna(unit)
        expected = unit * qty
        mismatch = total.notna() & (total != expected)
        for idx in df.index[mismatch]:
            issues.append(
                make_issue(
                    "ORDER_TOTAL_MISMATCH",
                    entity_type="order",
                    entity_id=str(df.at[idx, C_ID]),
                    row_ref=line_ref(int(idx)),
                    detail={"value": to_int(total.at[idx]), "expected": to_int(expected.at[idx])},
                    status="auto_fixed",
                )
            )
        line_total = expected  # 02 §3.1: use unit_price × qty

        # ---- status
        mapped = df[C_STATUS].str.strip().str.casefold().map(_STATUS_CASEFOLD)
        unknown = mapped.isna()
        for text, grp in df[unknown].groupby(C_STATUS):
            issues.append(
                make_issue(
                    "UNKNOWN_STATUS",
                    entity_type="file",
                    entity_id=raw.file_name,
                    detail={"value": str(text), "rows": len(grp), "mapped_to": "pending"},
                )
            )
        status = mapped.map(lambda v: v[0] if isinstance(v, tuple) else "pending")
        final = mapped.map(lambda v: v[1] if isinstance(v, tuple) else False).astype(bool)

        # ---- notes (masked, untrusted)
        scans: dict[str, ScannedNote] = {}
        for text in df[C_NOTE].unique():
            scans[str(text)] = scan_note(str(text))

        lines = pd.DataFrame(
            {
                "order_id": df[C_ID],
                "sku_id": df[C_SKU],
                "qty": qty.astype("Int64"),
                "list_price": lst.astype("Int64"),
                "unit_price": unit.astype("Int64"),
                "line_total": line_total.astype("Int64"),
            }
        )
        lines.insert(1, "line_no", lines.groupby("order_id").cumcount().astype("int64") + 1)

        head = pd.DataFrame(
            {
                "order_id": df[C_ID],
                "ordered_at": ordered_at,
                "status": status,
                "status_final": final,
                "note": df[C_NOTE],
            }
        )
        # One order per id: first line wins for time/status; first non-empty note.
        orders = head.groupby("order_id", sort=False).first().reset_index()
        notes_by_order = (
            head[head["note"].str.strip() != ""].groupby("order_id", sort=False)["note"].first()
        )
        mapped_note = orders["order_id"].map(notes_by_order)
        orders["note"] = [v if isinstance(v, str) else "" for v in mapped_note]
        orders["buyer_note_masked"] = orders["note"].map(lambda t: scans[str(t)].text)
        orders["note_flagged"] = orders["note"].map(lambda t: scans[str(t)].flagged).astype(bool)
        orders["status_history"] = orders["status"].map(
            lambda s: json.dumps([{"status": s, "batch_id": ctx.batch_id}])
        )
        orders["batch_id"] = ctx.batch_id
        for _, row in orders[orders["note_flagged"]].iterrows():
            issues.append(
                make_issue(
                    "SUSPICIOUS_NOTE",
                    entity_type="order",
                    entity_id=str(row["order_id"]),
                    detail={
                        "patterns": scans[str(row["note"])].patterns,
                        "column": C_NOTE,
                        "trust": "untrusted",
                    },
                )
            )
        orders = orders[ORDERS_COLUMNS]

        provisional = orders[~orders["status_final"]]
        if len(provisional):
            issues.append(
                make_issue(
                    "PROVISIONAL_STATUS",
                    entity_type="file",
                    entity_id=raw.file_name,
                    detail={
                        "orders": len(provisional),
                        "by_status": {
                            str(k): int(v) for k, v in provisional["status"].value_counts().items()
                        },
                    },
                )
            )

        # ---- catalogue from the platform SKU (canonical key)
        cat = pd.DataFrame(
            {
                "sku_id": df[C_SKU],
                "product_name": df[C_PRODUCT].str.strip(),
                "variant_name": df[C_VARIANT].str.strip(),
                "list_price": lst.astype("Int64"),
                "ordered_at": ordered_at,
            }
        ).sort_values("ordered_at", kind="stable")
        last = cat.groupby("sku_id", sort=True).agg(
            product_name=("product_name", "last"),
            variant_name=("variant_name", "last"),
            list_price=("list_price", "last"),
            n_rows=("sku_id", "size"),
        )
        last = last.reset_index()
        last["product_id"] = last["sku_id"].map(
            lambda s: derive_product_id(s, is_bundle=is_bundle_sku(s))
        )
        skus = pd.DataFrame(
            {
                "sku_id": last["sku_id"],
                "product_id": last["product_id"],
                "variant_name": last["variant_name"].replace("", None),
                "list_price": last["list_price"].astype("Int64"),
            }
        )
        products = (
            last.groupby("product_id", sort=True)["product_name"]
            .last()
            .reset_index()
            .rename(columns={"product_name": "name"})
        )
        products["name"] = products["name"].where(products["name"] != "", products["product_id"])
        items = pd.DataFrame(
            {
                "source_type": self.source_type.value,
                "raw_code": last["sku_id"],
                "raw_name": (last["product_name"] + " " + last["variant_name"]).str.strip(),
                "product_name": last["product_name"],
                "variant_name": last["variant_name"],
                "list_price": last["list_price"].astype("Int64"),
                "n_rows": last["n_rows"].astype("int64"),
                "price": pd.Series(pd.NA, index=last.index, dtype="Int64"),
            }
        )

        out.tables = {
            "orders": orders.reset_index(drop=True),
            "order_lines": lines.reset_index(drop=True),
            "products": products,
            "skus": skus,
        }
        out.source_items = items
        out.issues = issues
        if len(orders):
            last_at = orders["ordered_at"].max().to_pydatetime()
            out.as_of = floor_hour(last_at) + timedelta(hours=1)
        return out
