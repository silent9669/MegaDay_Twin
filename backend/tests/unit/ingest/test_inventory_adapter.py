"""ING-04 · stock sheet adapter (day + hour), receipts, plans, negative stock."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from app.ingest.adapters import InventoryAdapter, NormFrame
from app.ingest.adapters.inventory import parse_receipt
from tests.unit.ingest import adapter_data as d

DAY_HEADER = "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế,Đang về,Ngày dự kiến về,Ghi chú\n"
HOUR_HEADER = "Thời điểm cập nhật,Mã nội bộ,Tên hàng,Tồn thực tế,Đang về,Ngày dự kiến về,Ghi chú\n"


def _run(tmp_path: Path, header: str, body: str) -> NormFrame:
    src = tmp_path / "inv.csv"
    src.write_text("﻿" + header + body, encoding="utf-8")
    a = InventoryAdapter()
    return a.normalize(a.read(src), d.ctx())


def test_parse_receipt() -> None:
    assert parse_receipt("Nhập 380") == 380
    assert parse_receipt("nhap 1.200") == 1200
    assert parse_receipt("Nhập hàng: 50") == 50
    assert parse_receipt("Xuất 2 sp bảo hành") is None
    assert parse_receipt("Loại 80 áo size M") is None


def test_manual_day_sheet(tmp_path: Path) -> None:
    body = (
        "2026-11-01,GD1-XA,Bình - xanh,100,50,2026-11-03,\n"
        "2026-11-02,GD1-XA,Bình - xanh,-7,50,2026-11-03,\n"
        "2026-11-03,GD1-XA,Bình - xanh,140,,,Nhập 50; Xuất 2 sp bảo hành\n"
        "2026-11-03,GD2,Đèn,abc,,,\n"
    )
    norm = _run(tmp_path, DAY_HEADER, body)
    assert norm.variant == "day"
    snaps = norm.tables["inventory_snapshots"].set_index(["src_raw_code", "snapshot_at"])
    # end of 2026-11-02 VN = 2026-11-02T17:00Z
    neg = snaps.loc[("GD1-XA", pd.Timestamp("2026-11-02 17:00", tz="UTC"))]
    assert pd.isna(neg["on_hand"]) and neg["quality_flag"] == "negative"
    assert neg["granularity"] == "day" and pd.isna(neg["sku_id"])
    issue = d.codes(norm, "NEG_STOCK")[0]
    assert issue.detail["value"] == -7 and issue.severity == "error"
    assert d.codes(norm, "STOCK_ADJUSTMENT")[0].detail["note"] == "Xuất 2 sp bảo hành"
    assert norm.issue_codes()["ROW_INVALID"] == 1  # "abc"

    plans = norm.tables["restock_plans"]
    assert plans["qty_incoming"].tolist() == [50, 50]
    assert plans["planned_date"].tolist() == [date(2026, 11, 3)] * 2

    rec = norm.tables["restock_receipts"]
    r = rec.iloc[0]
    assert (r["src_raw_code"], r["received_on"], r["qty"]) == ("GD1-XA", date(2026, 11, 3), 50)
    assert r["planned_date"] == date(2026, 11, 3)
    assert norm.untrusted_columns == {"inventory_snapshots": ["note"]}


def test_hourly_notes_repeat_but_receipt_counted_once(tmp_path: Path) -> None:
    body = (
        "2026-11-11 10:00,GD1,Bình,10,40,2026-11-11,\n"
        "2026-11-11 11:00,GD1,Bình,50,,,Nhập 40\n"
        "2026-11-11 12:00,GD1,Bình,45,,,Nhập 40\n"
        "2026-11-11 13:00,GD1,Bình,85,,,Nhập 40; Nhập 40\n"
    )
    norm = _run(tmp_path, HOUR_HEADER, body)
    assert norm.variant == "hour"
    rec = norm.tables["restock_receipts"]
    assert rec["qty"].tolist() == [80]  # two receipts of 40 that day, not four
    assert rec["src_first_seen_at"].iloc[0] == pd.Timestamp("2026-11-11 04:00", tz="UTC")
    snaps = norm.tables["inventory_snapshots"]
    assert snaps["snapshot_at"].iloc[0] == pd.Timestamp("2026-11-11 03:00", tz="UTC")
    assert (snaps["granularity"] == "hour").all()


@d.needs_nho
def test_mock_negative_stock_matches_manifest() -> None:
    norm = d.inventory()
    got = {(i.detail["date"], i.entity_id, i.detail["value"]) for i in d.codes(norm, "NEG_STOCK")}
    expected = {
        (x["ngay"], x["ma_noi_bo"], x["gia_tri_sai"])
        for x in d.planted("nho", "ton_am")["chi_tiet"]
    }
    assert got == expected
    assert norm.tables["inventory_snapshots"]["quality_flag"].eq("negative").sum() == 3
    assert len(norm.tables["inventory_snapshots"]) == d.manifest()["so_dong"]["inventory"]


@d.needs_nho
def test_mock_receipts_match_real_arrivals() -> None:
    norm = d.inventory()
    truth_dir = d.data_dir() / "ground_truth"
    code_of = pd.read_csv(truth_dir / "sku_that.csv", encoding="utf-8-sig").set_index("sku")[
        "ma_noi_bo"
    ]
    arr = pd.read_csv(truth_dir / "nhap_hang_that.csv", encoding="utf-8-sig")
    arr["day"] = pd.to_datetime(arr["thuc_te"], format="%Y-%m-%d %H:%M", errors="coerce").dt.date
    arr = arr[arr["day"].notna()]
    arr = arr[(arr["day"] >= date(2026, 7, 14)) & (arr["day"] <= date(2026, 11, 10))]
    arr["code"] = arr["sku"].map(code_of)
    expected = arr.groupby(["code", "day"])["so_luong"].sum().to_dict()
    rec = norm.tables["restock_receipts"]
    got = {(r.src_raw_code, r.received_on): int(r.qty) for r in rec.itertuples()}
    assert got == expected
    assert rec["planned_date"].notna().sum() > 0


@d.needs_nho
def test_mock_live_hourly_sheet() -> None:
    norm = d.inventory(live=True)
    assert norm.variant == "hour"
    assert len(norm.tables["inventory_snapshots"]) == d.manifest()["so_dong"]["liveInventory"]
    adj = d.codes(norm, "STOCK_ADJUSTMENT")
    assert len(adj) == 1 and adj[0].entity_id == "GD0007-M"  # "Loại 80 áo size M …"
    assert len(norm.tables["restock_receipts"]) >= 1


@d.needs_nho
def test_source_items_keep_internal_code_and_name() -> None:
    items = d.inventory().source_items.set_index("raw_code")
    assert items.loc["GD0001-XA", "raw_name"] == "Bình nước GN 500ml - xanh"
    assert len(items) == d.manifest()["so_sku"] - 1  # the combo has no stock row
