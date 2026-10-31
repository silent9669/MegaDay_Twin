"""ING-05 cost sheet · ING-06 campaign structure + sale program."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pandas as pd

from app.ingest.adapters import CampaignsAdapter, CostAdapter, NormFrame, SaleProgramAdapter
from app.ingest.adapters.sale_program import is_must_run_rule
from tests.unit.ingest import adapter_data as d


def _cost(tmp_path: Path, body: str) -> NormFrame:
    src = tmp_path / "cost.csv"
    src.write_text("﻿tên sp,giá vốn,phí đóng gói,ghi chú\n" + body, encoding="utf-8")
    a = CostAdapter()
    return a.normalize(a.read(src), d.ctx())


def test_cost_money_styles_and_notes(tmp_path: Path) -> None:
    rows = [
        ["Bình  nước  GN 500ml - xanh", "45k", "2k", ""],
        ["binh nuoc 500ml hong", "45.000đ", "", "chưa gồm VAT"],
        ["nồi chiên", "455.000đ", "3.000", "giá cũ, cần cập nhật"],
        ["giấy lót", "1,5tr", "5000", "giá nhập lô tháng 9"],
        ["cốc lạ", "hỏi lại", "3000", ""],
    ]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["tên sp", "giá vốn", "phí đóng gói", "ghi chú"])
    w.writerows(rows)
    src = tmp_path / "cost.csv"
    src.write_text("﻿" + buf.getvalue(), encoding="utf-8")
    a = CostAdapter()
    norm = a.normalize(a.read(src), d.ctx())

    cost = norm.tables["cost_items"]
    assert cost["unit_cost"].tolist() == [45_000, 45_000, 455_000, 1_500_000]
    assert cost["packaging_cost"].tolist() == [2_000, 0, 3_000, 5_000]
    assert cost["src_raw_name"].iloc[0] == "Bình  nước  GN 500ml - xanh"  # verbatim
    assert cost["sku_id"].isna().all() and (cost["source"] == "file").all()
    assert (cost["valid_from"] == pd.Timestamp(d.RECEIVED_AT)).all()
    money = d.codes(norm, "MONEY_FORMAT")
    assert [m.entity_id for m in money] == ["cốc lạ"] and money[0].severity == "error"
    review = {i.entity_id for i in d.codes(norm, "COST_NOTE_REVIEW")}
    assert review == {"binh nuoc 500ml hong", "nồi chiên"}
    # every row stays a source item for SKU matching, even the unreadable one
    items = norm.source_items
    assert items["raw_code"].tolist() == [f"row:{n}" for n in range(2, 7)]
    assert items["price"].tolist()[:4] == [45_000, 45_000, 455_000, 1_500_000]


def test_cost_same_name_twice_keeps_both(tmp_path: Path) -> None:
    norm = _cost(tmp_path, "cốc A,10000,0,\ncốc A,12000,0,\ncốc A,12000,0,\n")
    assert norm.source_items["raw_code"].tolist() == ["row:2", "row:3"]
    assert norm.issue_codes() == {"DUP_ROW": 1}


@d.needs_nho
def test_mock_cost_sheet_reads_every_row() -> None:
    norm = d.cost()
    assert "MONEY_FORMAT" not in norm.issue_codes()  # ING-05: 0 unreadable rows
    assert len(norm.tables["cost_items"]) == d.manifest()["so_dong"]["gia_von_nhap_tay"]
    names = set(norm.source_items["raw_name"])
    assert "Bình  nước  giữ  nhiệt  500ml  Xanh" in names  # untouched spacing
    first = norm.tables["cost_items"].iloc[0]
    assert (first["unit_cost"], first["packaging_cost"]) == (92_000, 2_000)


@d.needs_nho
def test_mock_campaigns() -> None:
    norm = d.campaigns()
    c = norm.tables["campaigns"].set_index("campaign_id")
    assert len(c) == d.manifest()["so_chien_dich"]
    assert c.loc["CD001", "daily_budget"] == 256_000
    assert c.loc["CD001", "started_at"] == pd.Timestamp("2026-07-13 17:00", tz="UTC")
    assert not c.loc["CD002", "sku_level_control"]  # 3 SKUs share one budget
    links = norm.tables["campaign_skus"]
    assert set(links.loc[links["campaign_id"] == "CD002", "sku_id"]) == {
        "NOICHIEN2L",
        "GIAYLOT50",
        "COMBO-NOICHIEN",
    }
    single = links.groupby("campaign_id").size()
    for cid, n in single.items():
        assert bool(c.loc[cid, "sku_level_control"]) == (n == 1)


def test_campaign_bad_budget(tmp_path: Path) -> None:
    src = tmp_path / "c.json"
    src.write_text(
        json.dumps(
            [
                {"ma_chien_dich": "X1", "ngan_sach_ngay": "300k", "sku": ["A"]},
                {"ma_chien_dich": "X2", "ngan_sach_ngay": "nhiều", "sku": ["B"]},
            ]
        ),
        encoding="utf-8",
    )
    a = CampaignsAdapter()
    norm = a.normalize(a.read(src), d.ctx())
    assert norm.tables["campaigns"]["daily_budget"].tolist() == [300_000]
    assert norm.issue_codes() == {"MONEY_FORMAT": 1}


@d.needs_nho
def test_mock_sale_program() -> None:
    norm = d.sale_program()
    sc = norm.tables["sale_commitments"]
    assert norm.issues == []
    win = sc[sc["kind"] == "flash_window"].sort_values("window_start")
    # 00:00-02:00 and 12:00-14:00 VN on 11/11
    assert win["window_start"].tolist() == [
        pd.Timestamp("2026-11-10 17:00", tz="UTC"),
        pd.Timestamp("2026-11-11 05:00", tz="UTC"),
    ]
    assert win["window_end"].tolist() == [
        pd.Timestamp("2026-11-10 19:00", tz="UTC"),
        pd.Timestamp("2026-11-11 07:00", tz="UTC"),
    ]
    must = sc[sc["kind"] == "campaign_must_run"]
    assert set(must["campaign_id"]) == {"CD002"}  # ING-06 acceptance
    assert sorted(must["window_start"]) == win["window_start"].tolist()
    price = sc[(sc["kind"] == "committed_price") & (sc["sku_id"] == "NOICHIEN2L")]
    assert price["value_int"].tolist() == [531_000]
    reserve = sc[(sc["kind"] == "flash_reserve") & (sc["sku_id"] == "NOICHIEN2L")]
    assert reserve["value_int"].tolist() == [46]
    assert (sc["day_local"].astype(str) == "2026-11-11").all()
    assert sc["commitment_id"].is_unique
    assert (sc["kind"] == "rule_text").sum() == 2  # quy_dinh_chung
    # deterministic ids → re-ingest upserts the same rows
    again = SaleProgramAdapter().normalize(
        SaleProgramAdapter().read(d.data_dir() / "chuong_trinh_sale.json"), d.ctx(batch_id="b2")
    )
    assert (
        again.tables["sale_commitments"]["commitment_id"].tolist() == sc["commitment_id"].tolist()
    )


def test_unparsed_campaign_rule(tmp_path: Path) -> None:
    src = tmp_path / "s.json"
    src.write_text(
        json.dumps(
            {
                "chuong_trinh": "Sale",
                "ngay": "2026-12-12",
                "khung_flash_sale": ["22:00-24:00", "23:00-01:00"],
                "cam_ket": [],
                "quy_dinh_chien_dich": [
                    {"ma_chien_dich": "CD9", "quy_dinh": "Giữ ngân sách tối thiểu 200k"}
                ],
            }
        ),
        encoding="utf-8",
    )
    a = SaleProgramAdapter()
    norm = a.normalize(a.read(src), d.ctx())
    assert norm.issue_codes() == {"UNPARSED_RULE": 1}
    sc = norm.tables["sale_commitments"]
    win = sc[sc["kind"] == "flash_window"]
    assert win["window_end"].tolist() == [
        pd.Timestamp("2026-12-12 17:00", tz="UTC"),
        pd.Timestamp("2026-12-12 18:00", tz="UTC"),
    ]
    assert sc.loc[sc["kind"] == "rule_text", "campaign_id"].tolist() == ["CD9"]


def test_must_run_pattern() -> None:
    assert is_must_run_rule("Không tắt chiến dịch trong khung flash sale (đã cam kết với sàn)")
    assert is_must_run_rule("KHÔNG được tạm dừng chiến dịch trong khung giờ flash sale")
    assert not is_must_run_rule("Không tăng giá")
