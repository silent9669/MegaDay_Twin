"""ING-08 · Ghép SKU 3 nguồn (02 §5). Test tự đọc CSV thô của mock-data (không
import adapter) để dựng alias, rồi chấm bằng đáp án ``sku_that.csv`` — chỉ test
được đọc đáp án, code trong app/ thì không."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pandas as pd
import pytest

from app.core.errors import NotFound, ValidationFailed
from app.core.money import parse_vnd
from app.ingest.sku_matching import (
    AliasRecord,
    CatalogSku,
    MatchResult,
    add_bundle_consumption,
    alias_id_for,
    apply_confirmation,
    has_code,
    load_known_aliases,
    norm_name,
    resolve,
    stock_flow,
    write_aliases,
)
from app.warehouse import repo
from app.warehouse.db import Database

MOCK_OUT = Path(__file__).resolve().parents[4] / "mock-data" / "out"


# ================================================================ pure helpers
def test_norm_name_examples_from_plan() -> None:
    a = norm_name("Bình nước GN 500ml - xanh")
    b = norm_name("binh nuoc giu nhiet 500ml den")
    c = norm_name("Bình  nước  giữ  nhiệt  500ml  Hồng")
    assert a == "binh nuoc giu nhiet 500ml xanh"
    assert b == "binh nuoc giu nhiet 500ml den"
    assert c == "binh nuoc giu nhiet 500ml hong"
    assert norm_name("Nồi chiên KD mini 2L") == "noi chien khong dau mini 2l"
    assert norm_name("Đèn LED TT Tết - dây 5m") == "den led trang tri tet day 5m"


def test_alias_id_is_deterministic() -> None:
    assert alias_id_for("cost", "", "x") == alias_id_for("cost", "", "x")
    assert alias_id_for("cost", "", "x") != alias_id_for("inventory", "", "x")


def test_stock_flow_skips_restock_and_bad_rows() -> None:
    snaps: dict[str, int | None] = {
        "2026-07-01": 100,
        "2026-07-02": 90,
        "2026-07-03": 150,  # nhập hàng
        "2026-07-04": None,  # tồn âm đã bị adapter đặt NULL
        "2026-07-05": 140,
        "2026-07-06": 137,
    }
    assert stock_flow(snaps) == {"2026-07-02": 10.0, "2026-07-06": 3.0}


def test_add_bundle_consumption() -> None:
    sales = {"COMBO": {"d1": 2.0}, "A": {"d1": 1.0}}
    out = add_bundle_consumption(sales, {"COMBO": [("A", 1), ("B", 2)]})
    assert out["A"] == {"d1": 3.0}
    assert out["B"] == {"d1": 4.0}
    assert sales["A"] == {"d1": 1.0}  # không sửa đầu vào


def _toy_catalog() -> list[CatalogSku]:
    return [
        CatalogSku(sku_id="BN-XANH", product_id="BN", product_name="Bình nước giữ nhiệt 500ml",
                   variant_name="Xanh", list_price=189000),
        CatalogSku(sku_id="BN-HONG", product_id="BN", product_name="Bình nước giữ nhiệt 500ml",
                   variant_name="Hồng", list_price=189000),
        CatalogSku(sku_id="AO-M", product_id="AO", product_name="Áo giữ nhiệt nam",
                   variant_name="Size M", list_price=159000),
        CatalogSku(sku_id="AO-L", product_id="AO", product_name="Áo giữ nhiệt nam",
                   variant_name="Size L", list_price=159000),
    ]  # fmt: skip


def test_variant_conflict_is_never_matched() -> None:
    # BN-HONG và AO-L chưa có đơn nên không có trong danh mục; "Hồng"/"Size L" vẫn là
    # giá trị biến thể đã biết nhờ sản phẩm khác.
    cat = [
        *_toy_catalog()[:1],
        _toy_catalog()[2],
        CatalogSku(sku_id="QUAT-HONG", product_id="QUAT", product_name="Quạt mini",
                   variant_name="Hồng"),
        CatalogSku(sku_id="QUAN-L", product_id="QUAN", product_name="Quần nỉ",
                   variant_name="Size L"),
    ]  # fmt: skip
    res = resolve(
        [
            AliasRecord(source_type="cost", raw_name="binh nuoc giu nhiet 500ml hong"),
            AliasRecord(source_type="cost", raw_name="ao giu nhiet nam size l"),
        ],
        cat,
    )
    # Thiếu SKU HONG/L trong danh mục: không được ghép nhầm sang XANH/M (cùng sản phẩm).
    hong, size_l = res.matches
    assert hong.sku_id != "BN-XANH" and hong.status == "pending"
    assert size_l.sku_id != "AO-M" and size_l.status == "pending"


def test_toy_resolve_sources_and_statuses() -> None:
    cat = _toy_catalog()
    res = resolve(
        [
            AliasRecord(source_type="orders", raw_code="BN-XANH", raw_name="Bình nước - Xanh"),
            AliasRecord(source_type="inventory", raw_code="GD0001-XA",
                        raw_name="Bình nước GN 500ml - xanh"),
            AliasRecord(source_type="inventory", raw_code="GD0001-HO",
                        raw_name="Bình nước GN 500ml - hồng"),
            AliasRecord(source_type="cost", raw_name="ao giu nhiet nam size m", price=70000),
            AliasRecord(source_type="cost", raw_name="Áo  giữ  nhiệt  nam  Size  L", price=70000),
            AliasRecord(source_type="cost", raw_name="thứ gì đó lạ hoắc"),
        ],
        cat,
    )  # fmt: skip
    got = {(m.source_type, m.raw_name): m for m in res.matches}
    assert got[("orders", "Bình nước - Xanh")].method == "exact"
    inv = res.mapping("inventory")
    assert inv[("GD0001-XA", "Bình nước GN 500ml - xanh")] == "BN-XANH"
    assert inv[("GD0001-HO", "Bình nước GN 500ml - hồng")] == "BN-HONG"
    assert res.mapping("cost")[("", "ao giu nhiet nam size m")] == "AO-M"
    odd = got[("cost", "thứ gì đó lạ hoắc")]
    assert odd.status == "pending" and odd.sku_id is None
    assert [m.raw_name for m in res.queue] == ["thứ gì đó lạ hoắc"]


def test_ambiguous_names_go_to_queue_with_top3() -> None:
    cat = [
        CatalogSku(sku_id=f"KE{k}-XANH", product_id=f"KE{k}",
                   product_name=f"Kệ gia vị xoay (mẫu {k})", variant_name="Xanh")
        for k in (2, 3, 4)
    ]  # fmt: skip
    res = resolve([AliasRecord(source_type="cost", raw_name="Kệ gia vị xoay (mẫu - xanh")], cat)
    (m,) = res.matches
    assert m.status == "pending" and m.reason == "low_margin"
    assert len(m.candidates) == 3


def test_price_above_list_is_penalised() -> None:
    cat = _toy_catalog()
    ok = resolve([AliasRecord(source_type="cost", raw_name="ao giu nhiet nam size m",
                              price=70000)], cat).matches[0]  # fmt: skip
    bad = resolve([AliasRecord(source_type="cost", raw_name="ao giu nhiet nam size m",
                               price=900000)], cat).matches[0]  # fmt: skip
    assert ok.score is not None and bad.score is not None and bad.score < ok.score


def test_row_codes_split_same_name_lines_but_score_as_no_code() -> None:
    assert has_code("GD0001-XA") and has_code("BN-XANH")
    assert not has_code("") and not has_code("row:12")
    cat = [
        CatalogSku(sku_id=f"KE{k}-XANH", product_id=f"KE{k}",
                   product_name=f"Kệ gia vị xoay (mẫu {k})", variant_name="Xanh")
        for k in (2, 3)
    ]  # fmt: skip
    name = "Kệ gia vị xoay (mẫu - xanh"
    merged = resolve(
        [
            AliasRecord(source_type="cost", raw_name=name, price=50000),
            AliasRecord(source_type="cost", raw_name=name, price=52000),
        ],
        cat,
    )
    assert [m.reason for m in merged.matches] == ["duplicate_raw_name"]  # one alias for 2 lines
    split = resolve(
        [
            AliasRecord(source_type="cost", raw_code="row:7", raw_name=name, price=50000),
            AliasRecord(source_type="cost", raw_code="row:9", raw_name=name, price=52000),
        ],
        cat,
    )
    assert len(split.matches) == 2 and len({m.alias_id for m in split.matches}) == 2
    for m in split.matches:  # still ambiguous by name: each line waits for the user
        assert m.status == "pending" and m.method == "fuzzy" and m.reason == "low_margin"
    assert {m.sku_id for m in split.matches} == {"KE2-XANH", "KE3-XANH"}  # 1–1 suggestions


def test_row_code_keeps_shorthand_rule() -> None:
    """A stock-sheet shorthand used for 2 internal codes makes the name ambiguous for a
    cost line, with or without a ``row:`` code (ING-08 Q5)."""
    cat = [
        CatalogSku(sku_id="TUI-G1", product_id="TUI", product_name="Túi zip bảo quản TP",
                   variant_name="Gói 1"),
        CatalogSku(sku_id="TUI2-G1", product_id="TUI2",
                   product_name="Túi zip bảo quản TP (mẫu 2)", variant_name="Gói 1"),
    ]  # fmt: skip
    short = "Túi zip bảo quản TP - gói 1"
    inv = [
        AliasRecord(source_type="inventory", raw_code="TZ01-G1", raw_name=short),
        AliasRecord(source_type="inventory", raw_code="TZ02-G1", raw_name=short),
    ]
    for code in ("", "row:5"):
        alone = resolve([AliasRecord(source_type="cost", raw_code=code, raw_name=short)], cat)
        assert alone.matches[0].status == "auto_accepted"  # without the stock sheet
        res = resolve([*inv, AliasRecord(source_type="cost", raw_code=code, raw_name=short)], cat)
        (cost,) = [m for m in res.matches if m.source_type == "cost"]
        assert cost.status == "pending", code


# ================================================================ persistence
def test_write_confirm_and_reuse(db: Database) -> None:
    db.migrate()
    conn = db.write
    cat = _toy_catalog()
    for c in cat:
        repo.ensure_sku(conn, c.sku_id, product_id=c.product_id)
    aliases = [
        AliasRecord(source_type="cost", raw_name="ao giu nhiet nam size m", price=70000),
        AliasRecord(source_type="cost", raw_name="hàng lạ"),
    ]
    res = resolve(aliases, cat)
    assert write_aliases(conn, res, cat) == 2
    assert write_aliases(conn, res, cat) == 2  # idempotent
    assert repo.count_rows(conn, "sku_aliases") == 2
    row = repo.fetch_one(conn, "SELECT * FROM sku_aliases WHERE raw_name = 'hàng lạ'")
    assert row is not None and row["raw_code"] == "" and row["status"] == "pending"

    odd_id = alias_id_for("cost", "", "hàng lạ")
    with pytest.raises(ValidationFailed):
        apply_confirmation(conn, odd_id, "NOPE", user="u1")
    with pytest.raises(NotFound):
        apply_confirmation(conn, "sal-missing", "AO-L", user="u1")
    done = apply_confirmation(conn, odd_id, "AO-L", user="u1")
    assert done.status == "confirmed" and done.method == "user" and done.sku_id == "AO-L"

    known = load_known_aliases(conn)
    assert [k.alias_id for k in known] == [odd_id]
    again = resolve(aliases, cat, known=known)
    reused = next(m for m in again.matches if m.raw_name == "hàng lạ")
    assert reused.status == "confirmed" and reused.sku_id == "AO-L"
    # Lần ghi máy sau không được đè quyết định của người dùng.
    write_aliases(conn, resolve(aliases, cat), cat)
    row = repo.fetch_one(conn, "SELECT status, sku_id FROM sku_aliases WHERE alias_id = ?",
                         [odd_id])  # fmt: skip
    assert row == {"status": "confirmed", "sku_id": "AO-L"}

    rejected = apply_confirmation(conn, alias_id_for("cost", "", "ao giu nhiet nam size m"),
                                  None, user="u1")  # fmt: skip
    assert rejected.status == "rejected" and rejected.sku_id is None


# ================================================================ mock-data
@dataclass
class Dataset:
    catalog: list[CatalogSku]
    aliases: list[AliasRecord]
    truth: dict[tuple[str, str, str], str]  # alias key → SKU đúng


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", dtype=str, keep_default_na=False)


@cache
def load_dataset(size: str) -> Dataset:
    root = MOCK_OUT / size
    orders = _read(root / "don_hang_seller_center.csv").drop_duplicates("Mã đơn hàng")
    orders["day"] = orders["Ngày đặt hàng"].str[:10]
    orders["qty"] = orders["Số lượng"].astype(int)
    sales_df = orders.groupby(["SKU phân loại hàng", "day"])["qty"].sum()
    sales: dict[str, dict[str, float]] = {}
    for (sku, day), q in sales_df.items():
        sales.setdefault(str(sku), {})[str(day)] = float(q)
    bundles = {
        b.bundle_sku_id: [(c.sku_id, c.qty) for c in b.components] for b in repo.load_bundle_seed()
    }
    out_flow = add_bundle_consumption(sales, bundles)

    catalog: list[CatalogSku] = []
    aliases: list[AliasRecord] = []
    truth: dict[tuple[str, str, str], str] = {}
    first = orders.drop_duplicates("SKU phân loại hàng")
    for r in first.itertuples(index=False):
        sku, pname, vname = r[5], r[3], r[4]
        is_bundle = sku in bundles
        catalog.append(
            CatalogSku(
                sku_id=sku,
                product_id=repo.derive_product_id(sku, is_bundle=is_bundle),
                product_name=pname,
                variant_name=vname,
                list_price=parse_vnd(r[6]),
                is_bundle=is_bundle,
                daily_out=out_flow.get(sku, {}),
            )
        )
        raw = f"{pname} - {vname}" if vname else pname
        a = AliasRecord(source_type="orders", raw_code=sku, raw_name=raw)
        aliases.append(a)
        truth[a.key] = sku

    gt = _read(root / "ground_truth" / "sku_that.csv")
    code_to_sku = dict(zip(gt["ma_noi_bo"], gt["sku"], strict=True))

    inv = _read(root / "ton_kho_google_sheet.csv")
    inv_name: dict[str, str] = {}
    for code, grp in inv.groupby("Mã nội bộ"):
        snaps: dict[str, int | None] = {}
        for day, val in zip(grp["Ngày"], grp["Tồn thực tế"], strict=True):
            v = int(val) if val.lstrip("-").isdigit() else None
            snaps[day] = v if v is not None and v >= 0 else None
        name = str(grp["Tên hàng"].iloc[-1])
        inv_name[str(code)] = name
        a = AliasRecord(source_type="inventory", raw_code=str(code), raw_name=name,
                        daily_out=stock_flow(snaps))  # fmt: skip
        aliases.append(a)
        truth[a.key] = code_to_sku[str(code)]

    # File giá vốn theo thứ tự danh mục, bỏ các SKU thiếu giá vốn (manifest).
    manifest = json.loads((root / "ground_truth" / "manifest.json").read_text(encoding="utf-8"))
    missing = {s for e in manifest["loi_cai_san"] if e["loai"] == "thieu_gia_von" for s in e["sku"]}
    with_cost = gt[~gt["sku"].isin(missing)]
    cost = _read(root / "gia_von_nhap_tay.csv")
    assert len(cost) == len(with_cost)
    for raw, price, (_, g) in zip(cost["tên sp"], cost["giá vốn"], with_cost.iterrows(),
                                  strict=True):  # fmt: skip
        full = f"{g['ten_san_pham']} {g['phan_loai']}".strip()
        # Kiểm căn hàng: tên lộn xộn phải là một biến dạng của đúng SKU này.
        assert norm_name(raw) == norm_name(full) or raw == inv_name.get(g["ma_noi_bo"]), raw
        a = AliasRecord(source_type="cost", raw_name=raw, price=parse_vnd(price))
        aliases.append(a)
        truth[a.key] = g["sku"]
    return Dataset(catalog=catalog, aliases=aliases, truth=truth)


@dataclass
class Score:
    total: int
    auto: int
    wrong_auto: int
    pending: int
    suggest_hit: int

    @property
    def auto_rate(self) -> float:
        return self.auto / self.total if self.total else 0.0

    @property
    def error_rate(self) -> float:
        return self.wrong_auto / self.auto if self.auto else 0.0


def evaluate(ds: Dataset, res: MatchResult, sources: set[str]) -> Score:
    total = auto = wrong = pending = hit = 0
    for m in res.matches:
        if m.source_type not in sources:
            continue
        total += 1
        expected = ds.truth[(m.source_type, m.raw_code, m.raw_name)]
        if m.status == "auto_accepted":
            auto += 1
            wrong += m.sku_id != expected
        else:
            pending += 1
            hit += expected in {c.sku_id for c in m.candidates}
    return Score(total, auto, wrong, pending, hit)


@pytest.mark.parametrize("size", ["nho", "demo"])
def test_mock_data_targets(size: str) -> None:
    if not (MOCK_OUT / size / "don_hang_seller_center.csv").exists():
        pytest.skip(f"mock-data/out/{size} chưa sinh")
    ds = load_dataset(size)
    res = resolve(ds.aliases, ds.catalog)
    report = []
    for label, srcs in [
        ("inventory", {"inventory"}),
        ("cost", {"cost"}),
        ("inventory+cost", {"inventory", "cost"}),
        ("all", {"orders", "inventory", "cost"}),
    ]:
        sc = evaluate(ds, res, srcs)
        report.append(
            f"[{size}] {label:15s} n={sc.total:4d} auto={sc.auto_rate:6.1%} "
            f"sai/auto={sc.error_rate:5.2%} ({sc.wrong_auto}) cho={sc.pending} "
            f"dung-trong-top3={sc.suggest_hit}/{sc.pending}"
        )
    print("\n" + "\n".join(report))
    hard = evaluate(ds, res, {"inventory", "cost"})
    # 12 · ING-08: ≥ 90 % tự động, sai ≤ 1 % trong số tự động (tính riêng 2 nguồn khó).
    assert hard.auto_rate >= 0.90
    assert hard.error_rate <= 0.01
    # Mỗi alias trong 1 nguồn trỏ tối đa 1 SKU và ngược lại (gán 1–1).
    for src in ("inventory", "cost"):
        ids = [m.sku_id for m in res.matches if m.source_type == src and m.is_resolved]
        assert len(ids) == len(set(ids))
    # Combo không có dòng tồn riêng: không alias tồn nào bị ép vào COMBO-NOICHIEN.
    assert "COMBO-NOICHIEN" not in res.mapping("inventory").values()


def test_demo_roundtrip_to_db(db: Database) -> None:
    if not (MOCK_OUT / "nho" / "don_hang_seller_center.csv").exists():
        pytest.skip("mock-data/out/nho chưa sinh")
    db.migrate()
    ds = load_dataset("nho")
    res = resolve(ds.aliases, ds.catalog)
    n = write_aliases(db.write, res, ds.catalog)
    assert n == len(res.matches) == repo.count_rows(db.write, "sku_aliases")
