"""EVAL-01/02 · Chấm lớp dữ liệu bằng đáp án (plan 02 §8, 11 §2 phần 1–2, 12 · M1).

Chạy pipeline thật (``app.ingest.pipeline.ingest_files``) trên một DuckDB tạm, rồi
so kết quả với ``mock-data/out/<cỡ>/ground_truth/`` (chỉ thư mục ``eval/`` và test
được đọc đáp án):

* ghép SKU tự động: % đúng, % sai, hàng chờ, gợi ý đầu đúng (``sku_that.csv``);
* 12 lỗi cài sẵn của ``manifest.json → loi_cai_san``;
* số bán theo ngày và tồn cuối ngày so với ``nhu_cau_theo_ngay.csv``;
* cờ hết hàng so với ``nhu_cau_theo_gio.csv``;
* kích thước ``hourly_panel``, PII còn sót trong DuckDB, thời gian nạp;
* thời gian nạp từng giờ live 11.11 (``ingest_live_delta``, phát lại bằng
  ``eval.live_replay``).

Lệnh::

    uv run python -m eval.eval_data --size demo
    uv run python -m app.cli eval --size demo

Báo cáo: ``eval/reports/data_<cỡ>_<YYYYMMDD-HHMM>.md`` + ``.json`` (không commit).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from app.ingest.pipeline import IngestReport, ingest_files, ingest_live_delta
from app.warehouse import repo
from app.warehouse.db import Database
from eval import REPORTS_DIR, ground_truth_dir, mock_dir
from eval.live_replay import write_live_stream

VN = "Asia/Ho_Chi_Minh"
PII_COLUMNS = ("Người mua", "Số điện thoại", "Địa chỉ nhận hàng")
PII_TEXT = ("Khách Test", "Đường Mẫu", "Phường Thử Nghiệm")
PHONE_RE = r"(^|[^0-9A-Za-z])0[0-9]{9}([^0-9A-Za-z]|$)"  # not inside ids / hashes

# 12 · M1 thresholds (not relaxed here: a miss is reported as a miss).
T_INGEST_S = 60.0
T_AUTO = 0.90
T_WRONG = 0.01
T_TOP1 = 0.97
T_LIVE_S = 5.0
T_DAILY_LATE = 0.02
T_STOCK_NEG = 0.05
T_STOCKOUT = 0.90


@dataclass
class Check:
    key: str
    label: str
    value: str
    target: str
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)


# ====================================================================== helpers
def _csv(path: Path, **kw: Any) -> pd.DataFrame:
    return pd.read_csv(path, encoding="utf-8-sig", **kw)


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%".replace(".", ",")


def _manifest(size: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (ground_truth_dir(size) / "manifest.json").read_text(encoding="utf-8")
    )
    return data


def _planted(man: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(e for e in man["loi_cai_san"] if e["loai"] == kind)


def _issues(conn: duckdb.DuckDBPyConnection, code: str) -> list[dict[str, Any]]:
    rows = repo.fetch_all(
        conn,
        "SELECT entity_id, row_ref, status, CAST(detail AS VARCHAR) AS detail "
        "FROM data_quality_issues WHERE code = ? ORDER BY issue_id",
        [code],
    )
    for r in rows:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else {}
    return rows


# ====================================================================== (1) SKU matching
def _truth_for_aliases(size: str) -> dict[tuple[str, str, str], set[str]]:
    """alias key → set of correct SKUs (cost lines of the same name → several)."""
    gt = _csv(ground_truth_dir(size) / "sku_that.csv", dtype=str, keep_default_na=False)
    man = _manifest(size)
    missing = set(_planted(man, "thieu_gia_von")["sku"])
    truth: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    inv = _csv(mock_dir(size) / "ton_kho_google_sheet.csv", dtype=str, keep_default_na=False)
    code_to_sku = dict(zip(gt["ma_noi_bo"], gt["sku"], strict=True))
    for code, name in inv[["Mã nội bộ", "Tên hàng"]].drop_duplicates().itertuples(index=False):
        truth[("inventory", str(code).strip(), str(name).strip())].add(code_to_sku[str(code)])
    cost = _csv(mock_dir(size) / "gia_von_nhap_tay.csv", dtype=str, keep_default_na=False)
    with_cost = gt[~gt["sku"].isin(missing)]
    if len(cost) != len(with_cost):
        raise RuntimeError("gia_von_nhap_tay.csv không thẳng hàng với sku_that.csv")
    for i, (raw, sku) in enumerate(zip(cost["tên sp"], with_cost["sku"], strict=True)):
        truth[("cost", "", str(raw))].add(str(sku))
        truth[("cost", "", str(raw).strip())].add(str(sku))
        # Lines typed with the same name are separate aliases ``row:<file line>``.
        truth[("cost", f"row:{i + 2}", str(raw))].add(str(sku))
    for sku in gt["sku"]:
        truth[("orders", str(sku), "*")].add(str(sku))
    return truth


def score_matching(conn: duckdb.DuckDBPyConnection, size: str) -> dict[str, Any]:
    truth = _truth_for_aliases(size)
    gt = _csv(ground_truth_dir(size) / "sku_that.csv", dtype=str, keep_default_na=False)
    product = {s: repo.derive_product_id(s, is_bundle=s.startswith("COMBO")) for s in gt["sku"]}
    rows = repo.fetch_all(
        conn, "SELECT source_type, raw_code, raw_name, sku_id, status, score FROM sku_aliases"
    )
    acc: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    wrong_examples: list[dict[str, Any]] = []
    for r in rows:
        src = str(r["source_type"])
        key = (src, str(r["raw_code"]), "*" if src == "orders" else str(r["raw_name"]))
        ok_set = truth.get(key, set())
        if not ok_set:
            acc[src]["no_truth"] += 1
        groups = [src, "all"] + (["inventory+cost"] if src in ("inventory", "cost") else [])
        sku = r["sku_id"]
        auto = r["status"] in ("auto_accepted", "confirmed")
        for g in groups:
            acc[g]["total"] += 1
            if auto:
                acc[g]["auto"] += 1
                if sku in ok_set:
                    acc[g]["auto_ok"] += 1
                    acc[g]["top1_ok"] += 1
                else:
                    acc[g]["auto_wrong"] += 1
                    if sku and any(product.get(sku) == product.get(t) for t in ok_set):
                        acc[g]["variant_wrong"] += 1
            else:
                acc[g]["pending"] += 1
                if sku in ok_set:
                    acc[g]["top1_ok"] += 1
        if auto and sku not in ok_set and len(wrong_examples) < 10:
            wrong_examples.append({**r, "truth": sorted(ok_set)})
    out: dict[str, Any] = {}
    for g, a in acc.items():
        n = a["total"] or 1
        out[g] = {
            **dict(a),
            "auto_rate": a["auto_ok"] / n,
            "wrong_rate": a["auto_wrong"] / n,
            "pending_rate": a["pending"] / n,
            "top1_rate": a["top1_ok"] / n,
        }
    out["wrong_examples"] = wrong_examples
    return out


# ====================================================================== (2) planted errors
def check_planted(
    conn: duckdb.DuckDBPyConnection, size: str, matching: dict[str, Any]
) -> list[Check]:
    man = _manifest(size)
    checks: list[Check] = []
    batches = {
        str(r["source_type"]): r
        for r in repo.fetch_all(
            conn,
            "SELECT source_type, as_of, tz_offset_hours, dropped_columns, batch_id "
            "FROM ingest_batches WHERE mode = 'history'",
        )
    }

    # mui_gio ----------------------------------------------------------------
    tz = _issues(conn, "ADS_TZ_SHIFT")
    with (mock_dir(size) / "bao_cao_ads.csv").open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        sample = next(reader)
    hour = pd.Timestamp(sample["Thời gian"]).tz_localize("UTC")  # the file is UTC (answer)
    row = repo.fetch_one(
        conn,
        "SELECT spend FROM ad_hourly WHERE campaign_id = ? AND hour_utc = ?",
        [sample["Mã chiến dịch"], hour.to_pydatetime()],
    )
    proposed = tz[0]["detail"].get("proposed_offset_hours") if tz else None
    ok = bool(
        tz
        and proposed == 7
        and int(batches["ads"]["tz_offset_hours"]) == 7
        and row is not None
        and int(row["spend"]) == int(sample["Chi phí"])
    )
    checks.append(
        Check(
            "mui_gio",
            "Lệch múi giờ ads (+7)",
            f"ADS_TZ_SHIFT {proposed:+d}h, dòng mẫu {'khớp' if row else 'không thấy'}"
            if proposed is not None
            else "không phát hiện",
            "phát hiện +7, dòng mẫu đúng giờ",
            ok,
            {"issue": tz[0]["detail"] if tz else None},
        )
    )

    # do_tre_ads -------------------------------------------------------------
    lag = _issues(conn, "ADS_LAG")
    held = int(_planted(man, "do_tre_ads")["so_dong_bi_giu"])
    lag_h = (
        (pd.Timestamp(batches["orders"]["as_of"]) - pd.Timestamp(batches["ads"]["as_of"]))
        / pd.Timedelta(hours=1)
        if "ads" in batches and "orders" in batches
        else 0
    )
    d = lag[0]["detail"] if lag else {}
    rows_held = int(d.get("lag_hours", 0)) * int(d.get("campaigns", 0))
    checks.append(
        Check(
            "do_tre_ads",
            "Ads trễ so với đơn",
            f"trễ {lag_h:g} giờ, {rows_held} dòng chiến dịch-giờ chưa có",
            f"≥ 2 giờ, {held} dòng",
            bool(lag and lag_h >= 2 and rows_held == held),
        )
    )

    # thieu_ngay_ads ---------------------------------------------------------
    gap = _issues(conn, "ADS_GAP_DAY")
    gap_days = sorted({x for g in gap for x in g["detail"].get("local_dates", [])})
    gap_rows = sum(int(g["detail"]["hours"]) * int(g["detail"]["campaigns"]) for g in gap)
    want_rows = int(_planted(man, "thieu_ngay_ads")["so_dong_thieu"])
    checks.append(
        Check(
            "thieu_ngay_ads",
            "Ads thiếu trọn ngày",
            f"{', '.join(gap_days) or 'không'}; {gap_rows} dòng",
            f"2026-09-20; {want_rows} dòng",
            gap_days == ["2026-09-20"] and gap_rows == want_rows,
        )
    )

    # trung_don --------------------------------------------------------------
    n_dup = len(_issues(conn, "DUP_ORDER"))
    want_dup = int(_planted(man, "trung_don")["so_dong"])
    checks.append(
        Check("trung_don", "Đơn trùng bị loại", str(n_dup), f"= {want_dup}", n_dup == want_dup)
    )

    # ton_am -----------------------------------------------------------------
    neg = {
        (i["detail"].get("date"), i["detail"].get("raw_code")) for i in _issues(conn, "NEG_STOCK")
    }
    want_neg = {(e["ngay"], e["ma_noi_bo"]) for e in _planted(man, "ton_am")["chi_tiet"]}
    checks.append(
        Check(
            "ton_am",
            "Tồn âm",
            f"{len(neg & want_neg)}/{len(want_neg)} đúng, {len(neg - want_neg)} thừa",
            f"{len(want_neg)}/{len(want_neg)}, 0 thừa",
            neg == want_neg,
        )
    )

    # thieu_gia_von ----------------------------------------------------------
    mc = _issues(conn, "MISSING_COST")
    flagged = {str(i["entity_id"]) for i in mc}
    want_mc = set(_planted(man, "thieu_gia_von")["sku"])
    extra = flagged - want_mc
    pending_cost = int(
        repo.fetch_one(
            conn,
            "SELECT count(*) AS n FROM sku_aliases WHERE source_type = 'cost' "
            "AND status = 'pending'",
        )["n"]  # type: ignore[index]
    )
    checks.append(
        Check(
            "thieu_gia_von",
            "Thiếu giá vốn (luật chặn)",
            f"{len(flagged & want_mc)}/{len(want_mc)} SKU bị chặn; thêm {len(extra)} SKU "
            f"có dòng giá vốn đang chờ xác nhận ({pending_cost} alias)",
            f"đủ {len(want_mc)} SKU",
            want_mc <= flagged,
            {"missing": sorted(want_mc - flagged), "extra": sorted(extra)},
        )
    )

    # lech_ten_va_ma ---------------------------------------------------------
    hard = matching.get("inventory+cost", {})
    checks.append(
        Check(
            "lech_ten_va_ma",
            "Ghép mã nội bộ / tên giá vốn",
            f"tự động đúng {_pct(hard.get('auto_rate', 0))}, sai {_pct(hard.get('wrong_rate', 0))}",
            f"≥ {_pct(T_AUTO)}, ≤ {_pct(T_WRONG)}",
            hard.get("auto_rate", 0) >= T_AUTO and hard.get("wrong_rate", 1) <= T_WRONG,
        )
    )

    # dinh_dang_tien ---------------------------------------------------------
    cost_batch = batches.get("cost", {}).get("batch_id")
    bad_money = repo.fetch_one(
        conn,
        "SELECT count(*) AS n FROM data_quality_issues WHERE code = 'MONEY_FORMAT' "
        "AND batch_id = ? AND severity = 'error'",
        [cost_batch],
    )
    gt = _csv(ground_truth_dir(size) / "sku_that.csv", dtype=str, keep_default_na=False)
    true_cost = {s: int(c) for s, c in zip(gt["sku"], gt["gia_von"], strict=True) if c}
    got = repo.fetch_all(conn, "SELECT sku_id, unit_cost FROM cost_items")
    exact = rounded = 0
    for r in got:
        true = true_cost.get(str(r["sku_id"]))
        if true is None:
            continue
        if int(r["unit_cost"]) == true:
            exact += 1
        elif int(r["unit_cost"]) == int(true / 1000 + 0.5) * 1000:
            rounded += 1  # the file wrote "25k" for 24.500: the file is what we read
    cost_ok = exact + rounded
    n_bad = int(bad_money["n"]) if bad_money else -1
    checks.append(
        Check(
            "dinh_dang_tien",
            "Tiền viết nhiều kiểu",
            f"{n_bad} dòng đọc lỗi; {cost_ok}/{len(got)} giá vốn đúng số ({exact} khớp "
            f"tuyệt đối, {rounded} file ghi kiểu 'k' đã làm tròn nghìn)",
            "0 dòng lỗi, giá đúng",
            n_bad == 0 and cost_ok == len(got) and len(got) > 0,
        )
    )

    # trang_thai_tam ---------------------------------------------------------
    raw = _csv(
        mock_dir(size) / "don_hang_seller_center.csv",
        usecols=["Mã đơn hàng", "Ngày đặt hàng", "Trạng thái đơn hàng"],
        dtype=str,
    ).drop_duplicates()
    shipping_raw = set(raw.loc[raw["Trạng thái đơn hàng"] == "Đang giao", "Mã đơn hàng"])
    shipping_db = {
        str(r["order_id"])
        for r in repo.fetch_all(
            conn, "SELECT order_id FROM orders WHERE status = 'shipping' AND NOT status_final"
        )
    }
    last_at = pd.to_datetime(raw["Ngày đặt hàng"]).max()
    ship_at = pd.to_datetime(raw.loc[raw["Mã đơn hàng"].isin(shipping_raw), "Ngày đặt hàng"])
    in_48h = bool((ship_at >= last_at - pd.Timedelta(hours=48)).all())
    prov = repo.fetch_one(
        conn,
        "SELECT count(*) AS n, sum(units_provisional) AS u FROM hourly_panel "
        "WHERE list_contains(data_flags, 'PROVISIONAL')",
    )
    n_prov_rows = int(prov["n"]) if prov else 0
    checks.append(
        Check(
            "trang_thai_tam",
            "Đơn 'Đang giao' 48 giờ cuối gắn tạm",
            f"{len(shipping_db & shipping_raw)}/{len(shipping_raw)} đơn tạm; "
            f"{n_prov_rows} giờ panel cờ PROVISIONAL",
            "mọi đơn 'Đang giao' tạm, có cờ trên panel",
            shipping_db == shipping_raw and n_prov_rows > 0 and in_48h,
        )
    )

    # combo ------------------------------------------------------------------
    comps = repo.get_bundle_components(conn)
    combo_ok, combo_n, combo_detail = _check_combo(conn, comps)
    no_row = {str(i["entity_id"]) for i in _issues(conn, "BUNDLE_NO_STOCK_ROW")}
    checks.append(
        Check(
            "combo",
            "Combo ăn tồn thành phần",
            f"{combo_ok}/{combo_n} giờ tồn combo = min(tồn thành phần / qty)",
            "100% giờ",
            combo_n > 0 and combo_ok == combo_n and not (no_row & set(comps)),
            combo_detail,
        )
    )

    # du_lieu_ca_nhan --------------------------------------------------------
    pii = scan_pii(conn)
    dropped = set(batches.get("orders", {}).get("dropped_columns") or [])
    checks.append(
        Check(
            "du_lieu_ca_nhan",
            "Không còn PII trong DuckDB",
            f"bỏ {len(dropped & set(PII_COLUMNS))}/3 cột; {pii['hits']} ô khớp mẫu PII",
            "bỏ 3/3 cột, 0 ô",
            dropped >= set(PII_COLUMNS) and pii["hits"] == 0,
            pii,
        )
    )

    # prompt_injection -------------------------------------------------------
    ma_don = str(_planted(man, "prompt_injection")["ma_don"])
    flagged_orders = {
        str(r["order_id"])
        for r in repo.fetch_all(conn, "SELECT order_id FROM orders WHERE note_flagged")
    }
    sus = {str(i["entity_id"]) for i in _issues(conn, "SUSPICIOUS_NOTE")}
    checks.append(
        Check(
            "prompt_injection",
            "Ghi chú dụ AI bị gắn cờ",
            f"đơn {ma_don}: {'có cờ' if ma_don in flagged_orders else 'không cờ'}; "
            f"{len(flagged_orders)} đơn có cờ",
            "đúng đơn trong manifest",
            ma_don in flagged_orders and ma_don in sus,
        )
    )
    return checks


def _check_combo(
    conn: duckdb.DuckDBPyConnection, comps: dict[str, list[repo.BundleComponent]]
) -> tuple[int, int, dict[str, Any]]:
    ok = n = 0
    detail: dict[str, Any] = {}
    for bundle, parts in comps.items():
        df = conn.execute(
            "SELECT sku_id, hour_utc, stock_begin FROM hourly_panel WHERE sku_id IN "
            "(SELECT unnest(?))",
            [[bundle, *(p.sku_id for p in parts)]],
        ).df()
        if df.empty:
            continue
        wide = df.pivot(index="hour_utc", columns="sku_id", values="stock_begin").astype(float)
        expect = np.floor(
            np.min(np.stack([wide[p.sku_id].to_numpy() / p.qty for p in parts]), axis=0)
        )
        got = wide[bundle].to_numpy()
        valid = ~np.isnan(expect)
        same = valid & (np.maximum(expect, 0) == got)
        ok += int(same.sum())
        n += int(valid.sum())
        detail[bundle] = {"hours": int(valid.sum()), "equal": int(same.sum())}
    return ok, n, detail


def scan_pii(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Every text column of every table (names, addresses, phone numbers)."""
    hits: dict[str, int] = {}
    cols = conn.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'main' AND (data_type LIKE 'VARCHAR%' OR data_type = 'JSON')"
    ).fetchall()
    for table, col in cols:
        expr = f'CAST("{col}" AS VARCHAR)'
        cond = " OR ".join(
            [f"{expr} LIKE '%' || ? || '%'" for _ in PII_TEXT]
            + [f"regexp_matches({expr}, '{PHONE_RE}')"]
        )
        n = conn.execute(f'SELECT count(*) FROM "{table}" WHERE {cond}', list(PII_TEXT)).fetchall()
        if n and n[0][0]:
            hits[f"{table}.{col}"] = int(n[0][0])
    return {"hits": sum(hits.values()), "columns": hits}


def scan_file(db_path: Path) -> dict[str, int]:
    """``grep`` on the closed DuckDB file (02 §8: ``grep "Khách Test" megaday.duckdb``)."""
    raw = db_path.read_bytes() if db_path.exists() else b""
    return {t: raw.count(t.encode("utf-8")) for t in PII_TEXT}


# ====================================================================== (3) panel vs answer
def score_panel(conn: duckdb.DuckDBPyConnection, size: str) -> list[Check]:
    checks: list[Check] = []
    shape = conn.execute(
        "SELECT count(*), count(DISTINCT sku_id), count(DISTINCT hour_utc) FROM hourly_panel"
    ).fetchall()[0]
    n_sku_true = len(_csv(ground_truth_dir(size) / "sku_that.csv", usecols=["sku"]))
    days = len(pd.date_range("2026-07-14", "2026-11-10"))
    want_hours = days * 24
    checks.append(
        Check(
            "panel_shape",
            "hourly_panel đủ SKU × giờ",
            f"{shape[1]} SKU × {shape[2]} giờ = {shape[0]:,} dòng".replace(",", "."),
            f"{n_sku_true} SKU × {want_hours} giờ",
            shape[1] == n_sku_true and shape[2] == want_hours and shape[0] == shape[1] * shape[2],
        )
    )

    daily = conn.execute(
        "SELECT sku_id, day_local, sum(units_gross) AS units, "
        "max(CASE WHEN hour_local = 23 THEN stock_end END) AS stock_end "
        "FROM hourly_panel GROUP BY 1, 2"
    ).df()
    daily["day_local"] = pd.to_datetime(daily["day_local"]).dt.date
    gd = _csv(ground_truth_dir(size) / "nhu_cau_theo_ngay.csv")
    gd["day_local"] = pd.to_datetime(gd["ngay"]).dt.date
    gd = gd[gd["day_local"] <= pd.Timestamp("2026-11-10").date()]
    m = gd.merge(daily, left_on=["sku", "day_local"], right_on=["sku_id", "day_local"], how="left")
    early = m[m["day_local"] <= pd.Timestamp("2026-11-08").date()]
    late = m[m["day_local"] > pd.Timestamp("2026-11-08").date()]
    exact_early = float((early["units"] == early["ban_rong"]).mean())
    late_by_day = late.groupby("day_local")[["units", "ban_rong"]].sum()
    late_err = (late_by_day["units"] - late_by_day["ban_rong"]).abs() / late_by_day["ban_rong"]
    # 02 §8: the late surplus comes from "Chờ lấy hàng" orders cancelled after the export.
    pickup = conn.execute(
        "SELECT CAST(o.ordered_at + INTERVAL 7 HOUR AS DATE) AS d, sum(ol.qty) AS u "
        "FROM orders o JOIN order_lines ol USING (order_id) "
        "WHERE o.status = 'awaiting_pickup' GROUP BY 1"
    ).fetchall()
    pickup_by_day = {d: int(u) for d, u in pickup}
    surplus = {d: int(r["units"] - r["ban_rong"]) for d, r in late_by_day.iterrows()}
    explained = all(0 <= v <= pickup_by_day.get(d, 0) for d, v in surplus.items())
    checks.append(
        Check(
            "daily_units",
            "Số bán theo ngày vs ban_rong",
            f"khớp tuyệt đối {_pct(exact_early)} dòng tới 08/11; 09–10/11 lệch tối đa "
            f"{_pct(float(late_err.max()))} (dư {sum(surplus.values())} sp, "
            f"{'đều' if explained else 'không'} nằm trong đơn 'Chờ lấy hàng')",
            f"100% tới 08/11; ≤ {_pct(T_DAILY_LATE)}",
            exact_early == 1.0 and float(late_err.max()) <= T_DAILY_LATE,
            {
                "late_error_by_day": {str(k): float(v) for k, v in late_err.items()},
                "surplus_by_day": {str(k): v for k, v in surplus.items()},
                "awaiting_pickup_units_by_day": {str(k): v for k, v in pickup_by_day.items()},
                "surplus_explained_by_awaiting_pickup": explained,
            },
        )
    )

    man = _manifest(size)
    neg = {(e["ngay"], e["ma_noi_bo"]) for e in _planted(man, "ton_am")["chi_tiet"]}
    gt = _csv(ground_truth_dir(size) / "sku_that.csv", dtype=str, keep_default_na=False)
    code_to_sku = dict(zip(gt["ma_noi_bo"], gt["sku"], strict=True))
    neg_keys = {(code_to_sku[c], pd.Timestamp(d).date()) for d, c in neg}
    is_neg = pd.Series(
        [(s, d) in neg_keys for s, d in zip(m["sku"], m["day_local"], strict=True)], index=m.index
    )
    stock = m[m["ton_cuoi_ngay"].notna()]  # combo has no stock row in the answer
    normal = stock[~is_neg.loc[stock.index]]
    exact_stock = float((normal["stock_end"] == normal["ton_cuoi_ngay"]).mean())
    neg_rows = stock[is_neg.loc[stock.index]]
    neg_err = (
        float(
            ((neg_rows["stock_end"] - neg_rows["ton_cuoi_ngay"]).abs() / neg_rows["ton_cuoi_ngay"])
            .fillna(1.0)
            .max()
        )
        if len(neg_rows)
        else 0.0
    )
    checks.append(
        Check(
            "eod_stock",
            "Tồn cuối ngày vs ton_cuoi_ngay",
            f"khớp tuyệt đối {_pct(exact_stock)} dòng; 3 dòng tồn âm lệch tối đa {_pct(neg_err)}",
            f"100%; tồn âm ≤ {_pct(T_STOCK_NEG)}",
            exact_stock == 1.0 and neg_err <= T_STOCK_NEG,
            {"mismatch_rows": int((normal["stock_end"] != normal["ton_cuoi_ngay"]).sum())},
        )
    )

    gh = _csv(
        ground_truth_dir(size) / "nhu_cau_theo_gio.csv",
        usecols=["thoi_gian", "sku", "nhu_cau_that", "ban_duoc", "het_hang", "ton_cuoi_gio"],
    )
    gh["hour_utc"] = pd.to_datetime(gh["thoi_gian"]).dt.tz_localize(VN).dt.tz_convert("UTC")
    p = conn.execute("SELECT sku_id, hour_utc, stockout FROM hourly_panel").df()
    p["hour_utc"] = pd.DatetimeIndex(p["hour_utc"]).tz_convert("UTC")
    h = gh.merge(p, left_on=["sku", "hour_utc"], right_on=["sku_id", "hour_utc"], how="inner")
    truth = h["het_hang"] == 1
    comparable = truth | ((h["ton_cuoi_gio"] <= 0) & (h["nhu_cau_that"] == h["ban_duoc"]))
    pred = h["stockout"].astype(bool)
    recall = float((pred & truth).sum() / max(truth.sum(), 1))
    prec_c = float((pred & comparable).sum() / max(pred.sum(), 1))
    rec_c = float((pred & comparable).sum() / max(comparable.sum(), 1))
    checks.append(
        Check(
            "stockout",
            "Cờ hết hàng (giờ có đáp án)",
            f"recall trên het_hang {_pct(recall)}; định nghĩa so được: precision "
            f"{_pct(prec_c)}, recall {_pct(rec_c)}",
            f"precision, recall ≥ {_pct(T_STOCKOUT)}",
            recall >= T_STOCKOUT and prec_c >= T_STOCKOUT and rec_c >= T_STOCKOUT,
            {
                "hours": len(h),
                "het_hang": int(truth.sum()),
                "panel_stockout": int(pred.sum()),
                "comparable": int(comparable.sum()),
                "note": "het_hang chỉ đánh dấu giờ nhu cầu thật > tồn (app không quan sát "
                "được); định nghĩa so được thêm giờ kệ trống đã bán hết nhu cầu "
                "(docs/quyet-dinh/M1-panel.md P7).",
            },
        )
    )
    return checks


# ====================================================================== live timing
def time_live(
    conn: duckdb.DuckDBPyConnection, size: str, work: Path, hours: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    live = work / "live_stream"
    for h in range(hours):
        write_live_stream(size, live, h)
        t0 = time.perf_counter()
        rep = ingest_live_delta(conn, live)
        sec = time.perf_counter() - t0
        out.append(
            {
                "hour": h,
                "seconds": round(sec, 2),
                "status": rep.status,
                "new_rows": rep.new_rows,
                "timings_ms": rep.ingest.timings_ms if rep.ingest else {},
                "panel_rows": rep.ingest.panel.rows_written
                if rep.ingest and rep.ingest.panel
                else 0,
            }
        )
    return out


# ====================================================================== report
def _summary(report: IngestReport, seconds: float, matching: dict[str, Any], planted: list[Check],
             panel: list[Check], live: list[dict[str, Any]]) -> list[Check]:  # fmt: skip
    hard = matching.get("inventory+cost", {})
    allm = matching.get("all", {})
    caught = sum(c.ok for c in planted)
    out = [
        Check(
            "ingest_time",
            "Nạp 6 file lịch sử (đơn, ads, tồn, giá vốn, chiến dịch, sale)",
            f"{seconds:.1f} giây".replace(".", ","),
            f"< {T_INGEST_S:g} giây",
            seconds < T_INGEST_S and all(f.status == "loaded" for f in report.files),
        ),
        Check(
            "sku_auto",
            "Ghép SKU tự động đúng (mã nội bộ + giá vốn)",
            f"{_pct(hard.get('auto_rate', 0))} ({hard.get('auto_ok', 0)}/{hard.get('total', 0)}); "
            f"cả 3 nguồn {_pct(allm.get('auto_rate', 0))}",
            f"≥ {_pct(T_AUTO)}",
            hard.get("auto_rate", 0) >= T_AUTO,
        ),
        Check(
            "sku_wrong",
            "Ghép SKU tự động sai",
            f"{_pct(hard.get('wrong_rate', 0))} ({hard.get('auto_wrong', 0)} alias, "
            f"{hard.get('variant_wrong', 0)} nhầm biến thể)",
            f"≤ {_pct(T_WRONG)}",
            hard.get("wrong_rate", 1) <= T_WRONG,
        ),
        Check(
            "sku_top1",
            "Đúng nếu người xem chọn gợi ý đầu",
            _pct(hard.get("top1_rate", 0)),
            f"≥ {_pct(T_TOP1)}",
            hard.get("top1_rate", 0) >= T_TOP1,
            {"pending": hard.get("pending", 0)},
        ),
        Check(
            "planted",
            "Lỗi cài sẵn bắt được",
            f"{caught}/{len(planted)}",
            f"{len(planted)}/{len(planted)}",
            caught == len(planted) == 12,
        ),
        *[c for c in panel if c.key == "panel_shape"],
        *[c for c in planted if c.key == "du_lieu_ca_nhan"],
    ]
    if live:
        secs = sorted(x["seconds"] for x in live)
        worst = secs[-1]
        p50 = secs[(len(secs) - 1) // 2]
        p95 = secs[min(len(secs) - 1, round(0.95 * (len(secs) - 1)))]
        out.append(
            Check(
                "live_hour",
                "Nạp 1 giờ live 11.11 (PNL-04)",
                (
                    f"p50 {p50:.2f} · p95 {p95:.2f} · tối đa {worst:.2f} giây / {len(live)} giờ"
                ).replace(".", ","),
                f"< {T_LIVE_S:g} giây",
                worst < T_LIVE_S,
                {"p50": p50, "p95": p95, "max": worst},
            )
        )
    return out


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def _table(checks: list[Check]) -> list[str]:
    lines = ["| Tiêu chí | Kết quả | Ngưỡng | Đạt |", "|---|---|---|---|"]
    lines += [f"| {c.label} | {c.value} | {c.target} | {_mark(c.ok)} |" for c in checks]
    return lines


def render_markdown(result: dict[str, Any]) -> str:
    summary = [Check(**c) for c in result["summary"]]
    planted = [Check(**c) for c in result["planted"]]
    panel = [Check(**c) for c in result["panel"]]
    m = result["matching"]
    lines = [
        f"# Chấm lớp dữ liệu · {result['size']} · {result['created_at_local']}",
        "",
        "Pipeline thật (`ingest_files`) trên DuckDB tạm, so với `ground_truth/` "
        "(trên dữ liệu mô phỏng).",
        "",
        "## Tóm tắt (tiêu chí M1)",
        "",
        *_table(summary),
        "",
        "## Lỗi cài sẵn (manifest.json)",
        "",
        *_table(planted),
        "",
        "## Ghép SKU theo nguồn",
        "",
        "| Nguồn | Alias | Tự động đúng | Sai | Chờ xác nhận | Gợi ý đầu đúng |",
        "|---|---|---|---|---|---|",
    ]
    for g in ("orders", "inventory", "cost", "inventory+cost", "all"):
        a = m.get(g)
        if not a:
            continue
        lines.append(
            f"| {g} | {a['total']} | {_pct(a['auto_rate'])} | {a.get('auto_wrong', 0)} | "
            f"{a.get('pending', 0)} | {_pct(a['top1_rate'])} |"
        )
    lines += ["", "## Panel so với đáp án", "", *_table(panel), ""]
    if result["live"]:
        lines += [
            "## Nạp live 11.11 theo giờ (PNL-04)",
            "",
            "| Giờ | Giây | Dòng mới | Các bước (ms) |",
            "|---|---|---|---|",
        ]
        for x in result["live"]:
            steps = ", ".join(f"{k} {v}" for k, v in x["timings_ms"].items())
            rows = ", ".join(f"{k.split('.')[0]} +{v}" for k, v in x["new_rows"].items())
            lines.append(f"| {x['hour']:02d} | {x['seconds']} | {rows} | {steps} |")
        lines.append("")
    ing = result["ingest"]
    lines += [
        "## Nạp lịch sử",
        "",
        f"Tổng {ing['duration_ms']} ms. Các bước (ms): "
        + ", ".join(f"{k} {v}" for k, v in ing["timings_ms"].items()),
        "",
        "| File | Nguồn | Dòng | Ghi | Chờ SKU | Vấn đề |",
        "|---|---|---|---|---|---|",
    ]
    for f in ing["files"]:
        written = sum(f["rows_written"].values())
        issues = ", ".join(f"{k}×{v}" for k, v in f["issues"].items())
        lines.append(
            f"| {f['file_name']} | {f['source_type']} | {f['rows_in']} | {written} | "
            f"{f['rows_held']} | {issues} |"
        )
    lines += ["", "Vấn đề dữ liệu theo mã: "
              + ", ".join(f"`{k}` {v}" for k, v in ing["issues_by_code"].items()), ""]  # fmt: skip
    return "\n".join(lines)


def run(size: str, *, live_hours: int = 3, keep_db: Path | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="eval-data-") as tmp:
        work = Path(tmp)
        db_path = keep_db or work / "eval.duckdb"
        if db_path.exists():
            db_path.unlink()
        database = Database(db_path)
        database.migrate()
        conn = database.write
        repo.seed_bundles(conn)  # onboarding declaration (combo), as `cli reset` does
        t0 = time.perf_counter()
        report = ingest_files(conn, mock_dir(size))
        seconds = time.perf_counter() - t0
        matching = score_matching(conn, size)
        planted = check_planted(conn, size, matching)
        panel = score_panel(conn, size)
        live = time_live(conn, size, work, live_hours) if live_hours > 0 else []
        pii_after = scan_pii(conn)
        database.close()  # Windows locks an open DuckDB file
        file_hits = scan_file(db_path)
        for c in planted:
            if c.key == "du_lieu_ca_nhan":
                c.detail["after_live"] = pii_after
                c.detail["file_bytes"] = file_hits
                c.ok = c.ok and pii_after["hits"] == 0 and not any(file_hits.values())
                c.value += f"; file DuckDB: {sum(file_hits.values())} lần thấy chuỗi PII"
        summary = _summary(report, seconds, matching, planted, panel, live)
    now = datetime.now(UTC)
    return {
        "size": size,
        "created_at": now.isoformat(),
        "created_at_local": pd.Timestamp(now).tz_convert(VN).strftime("%Y-%m-%d %H:%M"),
        "ingest_seconds": seconds,
        "summary": [c.__dict__ for c in summary],
        "planted": [c.__dict__ for c in planted],
        "panel": [c.__dict__ for c in panel],
        "matching": matching,
        "live": live,
        "ingest": report.model_dump(mode="json", exclude={"events"}),
        "passed": all(c.ok for c in summary),
    }


def write_report(result: dict[str, Any], out_dir: Path = REPORTS_DIR) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp(result["created_at"]).tz_convert(VN).strftime("%Y%m%d-%H%M")
    md = out_dir / f"data_{result['size']}_{stamp}.md"
    js = out_dir / f"data_{result['size']}_{stamp}.json"
    md.write_text(render_markdown(result), encoding="utf-8")
    js.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return md, js


def main(argv: list[str] | None = None) -> int:
    # DuckDB's .df() triggers a pandas 3.1 deprecation warning from inside the library.
    warnings.filterwarnings("ignore", message="The inplace keyword")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Chấm lớp dữ liệu bằng ground_truth (02 §8).")
    ap.add_argument("--size", default="demo")
    ap.add_argument("--live-hours", type=int, default=3)
    ap.add_argument("--out", type=Path, default=REPORTS_DIR)
    ap.add_argument("--keep-db", type=Path, default=None, help="Giữ DuckDB ở đường dẫn này.")
    args = ap.parse_args(argv)
    if not (mock_dir(args.size) / "don_hang_seller_center.csv").exists():
        print(f"Không thấy mock-data/out/{args.size}. Chạy node generate.js --size {args.size}.")
        return 2
    result = run(args.size, live_hours=args.live_hours, keep_db=args.keep_db)
    md, js = write_report(result, args.out)
    print(render_markdown(result))
    print(f"\nĐã ghi {md} và {js}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
