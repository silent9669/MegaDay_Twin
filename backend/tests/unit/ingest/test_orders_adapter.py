"""ING-02 · Seller Center orders adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from app.ingest.adapters import NormFrame, OrdersAdapter
from tests.unit.ingest import adapter_data as d

HEADER = (
    "Mã đơn hàng,Ngày đặt hàng,Trạng thái đơn hàng,Tên sản phẩm,Tên phân loại hàng,"
    "SKU phân loại hàng,Giá gốc,Giá ưu đãi,Số lượng,Tổng tiền hàng,Người mua,Số điện thoại,"
    "Địa chỉ nhận hàng,Ghi chú của người mua\n"
)


def _run(tmp_path: Path, body: str) -> NormFrame:
    src = tmp_path / "orders.csv"
    src.write_text("﻿" + HEADER + body, encoding="utf-8")
    a = OrdersAdapter()
    return a.normalize(a.read(src), d.ctx())


@d.needs_nho
def test_dup_orders_match_manifest() -> None:
    norm = d.orders()
    expected = d.planted("nho", "trung_don")["so_dong"]
    assert len(d.codes(norm, "DUP_ORDER")) == expected
    assert norm.row_count - expected == len(norm.tables["orders"])
    assert norm.tables["orders"]["order_id"].is_unique


@d.needs_nho
def test_injection_order_flagged() -> None:
    norm = d.orders()
    flagged = norm.tables["orders"][norm.tables["orders"]["note_flagged"]]
    assert flagged["order_id"].tolist() == [d.planted("nho", "prompt_injection")["ma_don"]]
    issue = d.codes(norm, "SUSPICIOUS_NOTE")[0]
    assert issue.entity_id == flagged["order_id"].iloc[0]
    assert issue.detail["trust"] == "untrusted"
    # kept verbatim (it is data), only flagged
    assert "quảng cáo" in flagged["buyer_note_masked"].iloc[0]


@d.needs_nho
def test_schema_columns_and_types() -> None:
    norm = d.orders()
    o, lines = norm.tables["orders"], norm.tables["order_lines"]
    assert list(o.columns) == [
        "order_id",
        "ordered_at",
        "status",
        "status_final",
        "status_history",
        "buyer_note_masked",
        "note_flagged",
        "batch_id",
    ]
    assert list(lines.columns) == [
        "order_id",
        "line_no",
        "sku_id",
        "qty",
        "list_price",
        "unit_price",
        "line_total",
    ]
    assert str(o["ordered_at"].dt.tz) == "UTC"
    assert set(o["status"]) <= {"completed", "returned", "cancelled", "shipping", "awaiting_pickup"}
    first = o.iloc[0]
    # 2026-07-14 00:37 VN → 2026-07-13 17:37 UTC
    assert first["ordered_at"] == pd.Timestamp("2026-07-13 17:37", tz="UTC")
    assert json.loads(first["status_history"]) == [{"status": "completed", "batch_id": "b-test"}]
    assert (lines["line_total"] == lines["unit_price"] * lines["qty"]).all()
    assert norm.as_of == datetime(2026, 11, 10, 17, tzinfo=UTC)  # end of 23:xx VN on 10/11
    prov = d.codes(norm, "PROVISIONAL_STATUS")[0]
    assert prov.detail["by_status"].keys() == {"shipping", "awaiting_pickup"}


@d.needs_nho
def test_catalogue_and_source_items_keep_identity() -> None:
    norm = d.orders()
    skus, items = norm.tables["skus"], norm.source_items
    assert len(skus) == d.manifest()["so_sku"]
    row = items.set_index("raw_code").loc["BINHNUOC500-XANH"]
    assert row["product_name"] == "Bình nước giữ nhiệt 500ml"
    assert row["variant_name"] == "Xanh"
    combo = skus.set_index("sku_id").loc["COMBO-NOICHIEN"]
    assert combo["product_id"] == "COMBO-NOICHIEN"  # combos keep their own product


@d.needs_nho
def test_live_status_pending() -> None:
    norm = d.orders(live=True)
    statuses = set(norm.tables["orders"]["status"])
    assert statuses <= {"pending", "cancelled"} and "pending" in statuses
    assert (
        not norm.tables["orders"]
        .loc[norm.tables["orders"]["status"] == "pending", "status_final"]
        .any()
    )


def test_manual_mismatch_money_and_invalid(tmp_path: Path) -> None:
    body = (
        "A1,2026-11-01 10:00,Hoàn thành,Bình,Xanh,B-X,100.000,90k,2,999,K,0900000001,Đ,\n"
        "A1,2026-11-01 10:00,Hoàn thành,Bình,Xanh,B-X,100.000,90k,2,999,K,0900000001,Đ,\n"
        "A2,không phải ngày,Hoàn thành,Bình,Xanh,B-X,1,1,1,1,K,0900000002,Đ,\n"
        "A3,2026-11-01 11:00,Lạ quá,Bình,Hồng,B-H,abc,50000,1,50000,K,0900000003,Đ,\n"
        "A4,2026-11-01 12:00,Đang giao,Bình,Hồng,B-H,50000,50000,0,0,K,0900000004,Đ,\n"
    )
    norm = _run(tmp_path, body)
    got = norm.issue_codes()
    assert got["DUP_ORDER"] == 1
    assert got["ORDER_TOTAL_MISMATCH"] == 1
    assert got["ROW_INVALID"] == 2  # bad date, qty 0
    assert got["UNKNOWN_STATUS"] == 1
    assert got["MONEY_FORMAT"] == 1  # "abc" list price → falls back to unit price
    lines = norm.tables["order_lines"].set_index("order_id")
    assert lines.loc["A1", "unit_price"] == 90_000 and lines.loc["A1", "line_total"] == 180_000
    assert lines.loc["A3", "list_price"] == 50_000
    o = norm.tables["orders"].set_index("order_id")
    assert o.loc["A3", "status"] == "pending" and not o.loc["A3", "status_final"]


def test_multi_line_order_is_not_a_duplicate(tmp_path: Path) -> None:
    body = (
        "A1,2026-11-01 10:00,Hoàn thành,Bình,Xanh,B-X,1000,1000,1,1000,K,0900000001,Đ,\n"
        "A1,2026-11-01 10:00,Hoàn thành,Bình,Hồng,B-H,1000,1000,1,1000,K,0900000001,Đ,\n"
    )
    norm = _run(tmp_path, body)
    assert "DUP_ORDER" not in norm.issue_codes()
    assert norm.tables["order_lines"]["line_no"].tolist() == [1, 2]
    assert len(norm.tables["orders"]) == 1
