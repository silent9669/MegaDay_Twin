"""Kiểm lại các con số pitch bằng phép đo có sẵn (M5, docs/quyet-dinh/M5-chuan-bi-demo.md).

Không viết lại phép đo: gọi ``eval.eval_data.run``, ``eval.eval_forecast.run``,
``eval.eval_ads.run`` và các hàm của ``eval.demo_whatif`` (``build_db``, ``run_variant``,
``variants_cd002``), rồi so với con số pitch đang dùng (docs/quyet-dinh/*.md, README.md).
Mỗi dòng: tên | pitch | đo được | ĐẠT / LỆCH (dung sai ghi trong cột "dung sai").

    cd backend
    uv run python -m eval.pitch_numbers                         # đủ, ~6–20 phút (forecast nặng)
    uv run python -m eval.pitch_numbers --skip forecast         # bỏ phần dự báo
    uv run python -m eval.pitch_numbers --reuse-db PATH         # what-if trên bản sao DB có sẵn
    uv run python -m eval.pitch_numbers --light                 # bản nhẹ: số KHÁC pitch

What-if mặc định dựng DB ĐẦY ĐỦ như ``scripts/demo.mjs`` (``app.demo_import --replay-hour 0
--models``: backtest + fit + iROAS, ~3–5 phút; "phương án A" trong M5-chuan-bi-demo.md).
``--reuse-db``: DB demo 01:00 đầy đủ có sẵn (vd ``data/pitch-<thời gian>/demo.duckdb`` của
``demo.mjs``). Script chép sang thư mục tạm rồi mới dùng, không ghi vào file gốc.
Chỉ phần what-if / ``/scenarios/run`` dùng DB này; phần dữ liệu, dự báo, iROAS luôn tự dựng DB
tạm vì chúng đo cả thời gian nạp hoặc cần đúng lịch sử.

Kết quả: bảng in ra màn hình + ``eval/reports/pitch_numbers.json``. Số trên dữ liệu mô phỏng.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import statistics
import sys
import tempfile
import time
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from eval import REPORTS_DIR

SECTIONS = ("data", "forecast", "ads", "whatif", "api")
OUT_JSON = REPORTS_DIR / "pitch_numbers.json"

OK, OFF, TODO, ERR, SKIP = "ĐẠT", "LỆCH", "chưa tự động", "LỖI", "bỏ qua"


@dataclass
class Item:
    section: str
    name: str
    pitch: str
    measured: str
    status: str
    tolerance: str
    source: str
    note: str = ""


def vn(x: float, d: int = 1) -> str:
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(x: float, d: int = 1) -> str:
    return vn(100 * x, d) + "%"


def tr(x: float) -> str:
    return vn(x / 1e6, 2) + " tr"


def show(text: str = "") -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.flush()


class Board:
    def __init__(self) -> None:
        self.items: list[Item] = []

    def add(
        self,
        section: str,
        name: str,
        pitch: str,
        measured: str,
        ok: bool,
        tolerance: str,
        source: str,
        note: str = "",
    ) -> None:
        self.items.append(
            Item(section, name, pitch, measured, OK if ok else OFF, tolerance, source, note)
        )

    def near(
        self,
        section: str,
        name: str,
        pitch: float,
        value: float,
        tol: float,
        fmt: Callable[[float], str],
        source: str,
        note: str = "",
    ) -> None:
        self.add(
            section,
            name,
            fmt(pitch),
            fmt(value),
            abs(value - pitch) <= tol,
            f"± {fmt(tol)}",
            source,
            note,
        )

    def mark(self, section: str, names: list[tuple[str, str]], status: str, note: str) -> None:
        for name, pitch in names:
            self.items.append(Item(section, name, pitch, "—", status, "—", "—", note))


# ---------------------------------------------------------------------- 1. data
DATA_ITEMS = [
    ("Ghép SKU tự động đúng (mã nội bộ + giá vốn)", "96,4%"),
    ("Ghép SKU tự động đúng (cả 3 nguồn)", "97,6%"),
    ("Ghép SKU tự động sai", "0"),
    ("Lỗi cài sẵn bắt được", "12/12"),
    ("PII trong DuckDB", "0"),
    ("Nạp 6 file lịch sử", "< 60 s"),
    ("Nạp 1 giờ live (giờ chậm nhất)", "< 5 s"),
]


def section_data(b: Board, size: str, live_hours: int) -> None:
    from eval import eval_data

    src = "eval.eval_data (M1-pipeline.md, README)"
    r = eval_data.run(size, live_hours=live_hours)
    m = r["matching"]
    hard, allm = m.get("inventory+cost", {}), m.get("all", {})
    b.near("data", DATA_ITEMS[0][0], 0.964, hard.get("auto_rate", 0.0), 0.005, pct, src)
    b.near("data", DATA_ITEMS[1][0], 0.976, allm.get("auto_rate", 0.0), 0.005, pct, src)
    wrong = int(hard.get("auto_wrong", 0)) + int(hard.get("variant_wrong", 0))
    wrong_all = int(allm.get("auto_wrong", 0)) + int(allm.get("variant_wrong", 0))
    b.add("data", DATA_ITEMS[2][0], "0", str(wrong), wrong == 0 and wrong_all == 0, "= 0", src,
          f"cả 3 nguồn: {wrong_all}")  # fmt: skip
    planted = r["planted"]
    caught = sum(bool(c["ok"]) for c in planted)
    missed = [c["key"] for c in planted if not c["ok"]]
    b.add("data", DATA_ITEMS[3][0], "12/12", f"{caught}/{len(planted)}",
          caught == len(planted) == 12, "= 12/12", src,
          ("trượt: " + ", ".join(missed)) if missed else "")  # fmt: skip
    pii = next((c for c in planted if c["key"] == "du_lieu_ca_nhan"), None)
    b.add("data", DATA_ITEMS[4][0], "0", pii["value"] if pii else "không có kiểm tra",
          bool(pii and pii["ok"]), "0 ô, 0 chuỗi trong file", src)  # fmt: skip
    secs = float(r["ingest_seconds"])
    b.add("data", DATA_ITEMS[5][0], "< 60 s", vn(secs) + " s", secs < 60, "ngưỡng", src,
          "phụ thuộc tải máy")  # fmt: skip
    live = [float(x["seconds"]) for x in r["live"]]
    if live:
        worst = max(live)
        b.add("data", DATA_ITEMS[6][0], "< 5 s", f"tối đa {vn(worst, 2)} s / {len(live)} giờ",
              worst < 5, "ngưỡng", src,
              f"p50 {vn(statistics.median(live), 2)} s; phụ thuộc tải máy")  # fmt: skip
    else:
        b.mark("data", [DATA_ITEMS[6]], SKIP, "--live-hours 0")


# ---------------------------------------------------------------------- 2. forecast
FORECAST_ITEMS = [
    ("WAPE 10.10 champion", "~37%"),
    ("WAPE 10.10 baseline 7 ngày cùng giờ", "68,6%"),
    ("Phân nhóm nhu cầu đúng (SKU ≥ 28 ngày)", "88,3%"),
    ("Nhãn tin cậy verified / reference / scenario", "44 / 242 / 14"),
]
_PCT = re.compile(r"(\d+(?:\.\d+)?)%")


def _check(rep: Any, prefix: str) -> Any:
    return next(c for c in rep.checks if c.name.startswith(prefix))


def section_forecast(b: Board, size: str) -> None:
    from eval import eval_forecast

    src = "eval.eval_forecast (M2-du-bao.md, README)"
    with (
        tempfile.TemporaryDirectory(prefix="pitch-fc-") as tmp,
        contextlib.redirect_stdout(io.StringIO()),
    ):
        rep = eval_forecast.run(size, None, Path(tmp))
    wape = _PCT.findall(_check(rep, "M2 · WAPE 10.10 champion / baseline 7 ngày").value)
    champ, base = float(wape[0]) / 100, float(wape[1]) / 100
    b.near("forecast", FORECAST_ITEMS[0][0], 0.37, champ, 0.015, pct, src)
    b.near("forecast", FORECAST_ITEMS[1][0], 0.686, base, 0.015, pct, src,
           f"tốt hơn {pct(1 - champ / base)}")  # fmt: skip
    cls = float(_PCT.findall(_check(rep, "03 §6 · Phân nhóm đúng").value)[0]) / 100
    b.near("forecast", FORECAST_ITEMS[2][0], 0.883, cls, 0.01, pct, src)
    lab = {str(k): int(v) for k, v in rep.data.get("labels", {}).items()}
    got = (lab.get("verified", 0), lab.get("reference", 0), lab.get("scenario", 0))
    b.add("forecast", FORECAST_ITEMS[3][0], "44 / 242 / 14", " / ".join(map(str, got)),
          got == (44, 242, 14), "khớp đúng", src, f"chạy {rep.data.get('seconds')} s")  # fmt: skip


# ---------------------------------------------------------------------- 3. ads
ADS_ITEMS = [
    ("iROAS: sai số tương đối trung vị", "~24,8%"),
    ("iROAS gần thật hơn ROAS sàn", "40/49"),
    ("iROAS thật trong [p10, p90] (chi ≥ 2 tr)", "~68,8%"),
    ("CD002 iROAS ước lượng (chỉ lịch sử)", "~4,4–4,5"),
    ("CD002 iROAS thật", "5,27"),
    ("CD002 ROAS sàn", "8,11"),
]


def section_ads(b: Board, size: str) -> None:
    from eval import eval_ads

    src = "eval.eval_ads (M3-ads.md)"
    r = eval_ads.run(size)
    ck = {c["key"]: c for c in r["checks"]}
    camps = {c["campaign_id"]: c for c in r["campaigns"]}
    n = len(camps)
    errs = sorted(abs(c["iroas_mean"] - c["iroas_true"]) / c["iroas_true"] for c in camps.values())
    med = statistics.median(errs)
    b.near("ads", ADS_ITEMS[0][0], 0.248, med, 0.01, pct, src, ck["median_err"]["value"])
    closer = sum(
        abs(c["iroas_mean"] - c["iroas_true"]) < abs(c["roas_platform"] - c["iroas_true"])
        for c in camps.values()
    )
    b.add("ads", ADS_ITEMS[1][0], "40/49", f"{closer}/{n}", abs(closer - 40) <= 1 and n == 49,
          "± 1 chiến dịch", src)  # fmt: skip
    big = [c for c in camps.values() if c["spend"] >= eval_ads.T_COVER_MIN_SPEND]
    cov = sum(c["iroas_p10"] <= c["iroas_true"] <= c["iroas_p90"] for c in big) / len(big)
    b.near("ads", ADS_ITEMS[2][0], 0.688, cov, 0.021, pct, src,
           f"{ck['coverage']['value']}; dung sai = 1 chiến dịch")  # fmt: skip
    cd = camps["CD002"]
    est = float(cd["iroas_mean"])
    b.add("ads", ADS_ITEMS[3][0], "~4,4–4,5", vn(est, 2), 4.35 <= est <= 4.55, "4,35–4,55",
          src, f"[{vn(cd['iroas_p10'], 2)}–{vn(cd['iroas_p90'], 2)}]")  # fmt: skip
    b.near("ads", ADS_ITEMS[4][0], 5.27, float(cd["iroas_true"]), 0.005,
           lambda x: vn(x, 2), "ground_truth/chien_dich_that.csv")  # fmt: skip
    b.near("ads", ADS_ITEMS[5][0], 8.11, float(cd["roas_platform"]), 0.005,
           lambda x: vn(x, 2), "ground_truth/chien_dich_that.csv")  # fmt: skip


# ---------------------------------------------------------------------- 4. what-if + API
WHATIF_ITEMS = [
    ("CD002 iROAS ước lượng (DB demo 11/11 01:00)", "~4,4–4,5"),
    ("[1] CD002 11.11 01:00 profit: đề xuất", "schedule_hours 0-2,12-14"),
    ("[1] hơn chạy tiếp", "~3,97 tr"),
    ("[1] hơn giảm 50% (runner-up)", "~1,22 tr"),
    ("[1] xác suất p", "≈ 1"),
    ("[1] tạm dừng bị cấm (flash)", "cấm"),
    ("[4g] chỉ tiêu 97 sp: kết luận", "none_meets_goal (không phương án nào đạt chắc 80%)"),
    ("[4g] phương án có xác suất đạt cao nhất", "continue ~70%"),
    ("[4g] cái giá so với phương án lời nhất", "~3,97 tr"),
    ("[5] bỏ ràng buộc flash: đề xuất", "pause"),
    ("[5] cái giá của cam kết flash", "~1,1 tr"),
    ("[2a] hàng về sớm 1 ngày: kết luận", "không đổi (schedule_hours)"),
    ("[8a] ZoneBar CD020 06/11, V = 0", "pause_better"),
]
API_ITEMS = [("POST /scenarios/run (CD002, 1.000 mẫu, đã có dự báo)", "~0,3 s")]


def _db(
    tmp: Path, reuse: Path | None, light: bool = False
) -> tuple[duckdb.DuckDBPyConnection, Path, str]:
    from eval import demo_whatif

    if reuse is not None:
        return demo_whatif.copy_db(tmp, reuse), tmp / "demo.duckdb", f"bản sao của {reuse}"
    if light:
        conn = demo_whatif.build_db(tmp, backtest=False)
        return conn, tmp / "demo.duckdb", "BẢN NHẸ demo_whatif.build_db (không backtest/fit)"
    conn = demo_whatif.build_full_db(tmp)
    return (
        conn,
        tmp / "import" / "demo.duckdb",
        "DB đầy đủ như demo.mjs (demo_import --replay-hour 0 --models)",
    )


def _actions(s: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["key"]: a for a in s["actions"]}


def section_whatif(
    b: Board, tmp: Path, reuse: Path | None, run_api: bool, light: bool = False
) -> None:
    from eval import demo_whatif
    from eval.demo_whatif import DAY_06, END_06, Variant

    src = "eval.demo_whatif (M3-mo-phong.md 'Sau sửa lỗi M3-whatif')"
    conn, path, origin = _db(tmp, reuse, light)
    goal_id: str | None = None
    try:
        df = demo_whatif.ads_table(conn)
        est = float(df["iroas"].astype(float)["CD002"])
        b.add("whatif", WHATIF_ITEMS[0][0], "~4,4–4,5", vn(est, 2), 4.35 <= est <= 4.55,
              "4,35–4,55", "eval.demo_whatif.ads_table", origin)  # fmt: skip
        vs = {v.vid: v for v in demo_whatif.variants_cd002({})}
        vs["8a"] = Variant("8a", "ZoneBar CD020 06/11, V = 0", "CD020", DAY_06, END_06,
                           overrides={"leftover_value_per_unit": 0})  # fmt: skip
        rows = {k: demo_whatif.run_variant(conn, vs[k]).summary() for k in ("1", "4g", "5", "2a")}
        rows["8a"] = demo_whatif.run_variant(conn, vs["8a"]).summary()
        s1, a1 = rows["1"], _actions(rows["1"])
        best1 = s1["best"] or ""
        b.add("whatif", WHATIF_ITEMS[1][0], "schedule_hours:0-2,12-14", f"{best1} ({s1['status']})",
              best1 == "schedule_hours:0-2,12-14", "khớp đúng", src)  # fmt: skip
        b.near("whatif", WHATIF_ITEMS[2][0], 3.97e6, float(s1["gain_vs_continue"] or 0), 0.1e6,
               tr, src)  # fmt: skip
        b.near("whatif", WHATIF_ITEMS[3][0], 1.22e6, float(s1["gain_vs_runner_up"] or 0), 0.1e6,
               tr, src, f"runner-up {s1['runner_up']}")  # fmt: skip
        p = s1["p"]
        b.add("whatif", WHATIF_ITEMS[4][0], "≈ 1", "—" if p is None else vn(p, 2),
              p is not None and p >= 0.99, "≥ 0,99", src)  # fmt: skip
        pause = a1.get("pause", {})
        b.add("whatif", WHATIF_ITEMS[5][0], "cấm",
              "cấm" if not pause.get("feasible", True) else "được phép",
              not pause.get("feasible", True), "khớp đúng", src,
              ", ".join(pause.get("violations") or []))  # fmt: skip
        # [4g] trên DB đầy đủ (phương án A): không phương án nào đạt P(bán đủ 97) ≥ 80%; app
        # nói phương án có xác suất cao nhất và cái giá của nó so với phương án lời nhất
        s4, a4 = rows["4g"], _actions(rows["4g"])
        b.add("whatif", WHATIF_ITEMS[6][0], "none_meets_goal",
              f"{s4['status']} (best {s4['best']})",
              s4["status"] == "none_meets_goal" and s4["best"] is None, "khớp đúng", src,
              "; ".join(f"{k} {pct(a['p_target'], 0)}" for k, a in a4.items()
                        if a["feasible"] and a["p_target"] is not None))  # fmt: skip
        ok4 = [a for a in a4.values() if a["feasible"] and a["p_target"] is not None]
        top_p = max(ok4, key=lambda a: (a["p_target"], a["J"]))
        top_j = max(ok4, key=lambda a: a["J"])
        b.add("whatif", WHATIF_ITEMS[7][0], "continue ~70%",
              f"{top_p['key']} {pct(top_p['p_target'], 1)}",
              top_p["key"] == "continue" and abs(top_p["p_target"] - 0.70) <= 0.02,
              "khớp phương án, ± 2 điểm", src)  # fmt: skip
        b.near("whatif", WHATIF_ITEMS[8][0], 3.97e6, top_j["J"] - top_p["J"], 0.1e6, tr, src,
               f"E[J] lời nhất ({top_j['key']}) − E[J] xác suất cao nhất")  # fmt: skip
        s5, a5 = rows["5"], _actions(rows["5"])
        b.add("whatif", WHATIF_ITEMS[9][0], "pause", f"{s5['best']} ({s5['status']})",
              s5["best"] == "pause", "khớp đúng", src)  # fmt: skip
        commit = a5["pause"]["J"] - a1[best1]["J"]
        b.near("whatif", WHATIF_ITEMS[10][0], 1.1e6, commit, 0.1e6, tr, src,
               "E[J] pause (bỏ flash) − E[J] đề xuất [1]")  # fmt: skip
        s2 = rows["2a"]
        b.add("whatif", WHATIF_ITEMS[11][0], best1, f"{s2['best']} ({s2['status']})",
              s2["best"] == best1, "cùng đề xuất với [1]", src,
              f"hơn chạy tiếp {tr(float(s2['gain_vs_continue'] or 0))}")  # fmt: skip
        zb = rows["8a"]["zone_bar"]
        verdict = zb["verdict"] if zb else "không có thanh"
        note = (
            f"x* = {vn(zb['threshold'], 2)}; pause p10–p90 = "
            f"{vn(zb['interval'][0], 2)}–{vn(zb['interval'][-1], 2)}"
            if zb
            else ""
        )
        b.add("whatif", WHATIF_ITEMS[12][0], "pause_better", verdict, verdict == "pause_better",
              "khớp đúng", src, note)  # fmt: skip
        if run_api:
            from app.economics.goals import create_goal
            from eval.demo_whatif import END_1111

            goal_id = create_goal(conn, scope_id="CD002", kind="profit", deadline=END_1111,
                                  confirmed_by="lead-eval").goal_id  # fmt: skip
    finally:
        conn.close()
    if goal_id is not None:
        section_api(b, path, goal_id)


def section_api(b: Board, path: Path, goal_id: str) -> None:
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app
    from app.warehouse.db import Database

    src = "TestClient trên DB what-if (M3-api.md §4: 0,27 s curl)"
    settings = Settings(duckdb_path=path, ai_enabled=False, live_enabled=False, app_env="demo")
    headers = {"X-User": "pitch-eval", "X-Role": "operator"}
    body: dict[str, Any] = {
        "campaign_id": "CD002",
        "goal_id": goal_id,
        "start": "2026-11-11T01:00:00+07:00",
        "samples": 1000,
    }
    times: list[float] = []
    with TestClient(create_app(settings, db=Database(path))) as api:
        for seed in (1, 2, 3, 4):  # seed khác nhau: không trúng cache; lần 1 khởi động
            t0 = time.perf_counter()
            res = api.post("/api/v1/scenarios/run", json={**body, "seed": seed}, headers=headers)
            times.append(time.perf_counter() - t0)
            res.raise_for_status()
    warm = statistics.median(times[1:])
    got = f"trung vị {vn(warm, 2)} s (lần đầu {vn(times[0], 2)} s)"
    b.add("api", API_ITEMS[0][0], "~0,3 s", got, warm <= 0.5, "≤ 0,5 s", src, "phụ thuộc tải máy")


# ---------------------------------------------------------------------- report
def render(b: Board) -> str:
    head = ["Con số", "Pitch", "Đo được", "Kết quả", "Dung sai"]
    rows = [[i.name, i.pitch, i.measured, i.status, i.tolerance] for i in b.items]
    w = [max(len(r[k]) for r in [head, *rows]) for k in range(len(head))]
    w[0] = min(w[0], 52)
    lines = [" | ".join(h.ljust(w[k]) for k, h in enumerate(head))]
    lines.append("-+-".join("-" * x for x in w))
    for i, r in zip(b.items, rows, strict=True):
        lines.append(" | ".join(c[: w[k]].ljust(w[k]) for k, c in enumerate(r)))
        if i.note:
            lines.append(" " * 4 + "↳ " + i.note)
    n = {s: sum(i.status == s for i in b.items) for s in (OK, OFF, TODO, ERR, SKIP)}
    lines.append("")
    lines.append(", ".join(f"{k} {v}" for k, v in n.items() if v))
    return "\n".join(lines)


def run(
    size: str,
    *,
    reuse_db: Path | None,
    skip: set[str],
    live_hours: int,
    out: Path = OUT_JSON,
    light: bool = False,
) -> dict[str, Any]:
    b = Board()
    t0 = time.perf_counter()
    timings: dict[str, float] = {}

    def guarded(section: str, items: list[tuple[str, str]], fn: Callable[[], None]) -> None:
        if section in skip:
            b.mark(section, items, SKIP, f"--skip {section}")
            return
        t = time.perf_counter()
        before = len(b.items)
        try:
            fn()
        except Exception as e:  # report and go on with the other sections
            del b.items[before:]
            b.mark(section, items, ERR, f"{type(e).__name__}: {e}")
        timings[section] = round(time.perf_counter() - t, 1)
        show(f"[{section}] xong sau {timings[section]} s")

    guarded("data", DATA_ITEMS, lambda: section_data(b, size, live_hours))
    guarded("forecast", FORECAST_ITEMS, lambda: section_forecast(b, size))
    guarded("ads", ADS_ITEMS, lambda: section_ads(b, size))
    with tempfile.TemporaryDirectory(prefix="pitch-whatif-") as tmp:
        api = "api" not in skip
        items = WHATIF_ITEMS + (API_ITEMS if api else [])
        guarded("whatif", items, lambda: section_whatif(b, Path(tmp), reuse_db, api, light))
        if not api:
            b.mark("api", API_ITEMS, SKIP, "--skip api")
    result = {
        "size": size,
        "created_at": datetime.now(UTC).isoformat(),
        "reuse_db": str(reuse_db) if reuse_db else None,
        "light": light,
        "skip": sorted(skip),
        "seconds": round(time.perf_counter() - t0, 1),
        "section_seconds": timings,
        "items": [asdict(i) for i in b.items],
        "passed": all(i.status in (OK, SKIP) for i in b.items),
        "table": render(b),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    warnings.filterwarnings("ignore", message="The inplace keyword")
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    ap = argparse.ArgumentParser(description="Kiểm con số pitch bằng các eval có sẵn.")
    ap.add_argument("--size", default="demo")
    ap.add_argument("--reuse-db", type=Path, default=None, help="DB demo 01:00 có sẵn (chép)")
    ap.add_argument("--skip", default="", help=f"bỏ phần, phẩy: {','.join(SECTIONS)}")
    ap.add_argument("--live-hours", type=int, default=3, help="số giờ live đo (eval_data)")
    ap.add_argument("--out", type=Path, default=OUT_JSON)
    ap.add_argument("--light", action="store_true", help="what-if trên bản nhẹ (số KHÁC pitch)")
    args = ap.parse_args(argv)
    skip = {s for s in args.skip.split(",") if s}
    unknown = skip - set(SECTIONS)
    if unknown:
        ap.error(f"--skip không biết: {', '.join(sorted(unknown))}")
    if args.reuse_db is not None and not args.reuse_db.is_file():
        ap.error(f"Không thấy {args.reuse_db}")
    if args.light:
        show("CẢNH BÁO: --light = bản nhẹ không backtest/fit, số what-if KHÁC pitch.")
    result = run(
        args.size,
        reuse_db=args.reuse_db,
        skip=skip,
        live_hours=args.live_hours,
        out=args.out,
        light=args.light,
    )
    show("\n=== Con số pitch (trên dữ liệu mô phỏng)\n")
    show(result["table"])
    show(f"\nĐã ghi {args.out} ({result['seconds']} s)")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
