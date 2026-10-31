"""What-if cho kịch bản demo (pitch 31/10) bằng bộ mô phỏng THẬT.

Mặc định dựng DB ``demo`` ĐẦY ĐỦ như ``scripts/demo.mjs`` (``app.demo_import --replay-hour 0
--models``: lịch sử + live 11.11 tới 01:00 qua thư mục phát lại, backtest + fit + ước lượng
iROAS; "phương án A", docs/quyet-dinh/M5-chuan-bi-demo.md), rồi chạy
:func:`app.simulator.service.run_simulation` cho các biến thể:
gốc CD002, hàng về sớm/trễ, giá trị hàng dư V, đổi mục tiêu, bỏ ràng buộc flash, CD002 ngày
thường, CD004 ngày thường, chiến dịch ngày thường có iROAS biên cao hơn hòa vốn.

Cột "thật" lấy từ ``ground_truth/chien_dich_that.csv`` (chỉ eval/ được đọc).

    cd backend
    uv run python -m eval.demo_whatif                 # DB đầy đủ như demo.mjs, ~5–15 phút
    uv run python -m eval.demo_whatif --db PATH       # bản sao DB demo có sẵn (đầy đủ)
    uv run python -m eval.demo_whatif --light         # bản nhẹ, không backtest/fit (~1–2 phút):
                                                      # số KHÁC pitch
    uv run python -m eval.demo_whatif --json out.json # thêm bản JSON
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from app.ads_effect.iroas import estimate_all, write_estimates
from app.economics.goals import create_goal
from app.ingest.pipeline import ingest_files, ingest_live_delta
from app.panel.build import build_hourly_panel
from app.simulator import engine
from app.simulator.schemas import SimOutput, SimRequest
from app.simulator.service import run_simulation
from app.warehouse import repo
from app.warehouse.db import migrate
from eval import ground_truth_dir, mock_dir
from eval.live_replay import write_live_stream

TZ = "+07:00"


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s + TZ)


START_1111 = _t("2026-11-11T01:00:00")
END_1111 = _t("2026-11-12T00:00:00")
END_1211 = _t("2026-11-13T00:00:00")
NORMAL_05 = _t("2026-11-05T09:00:00")
END_05 = _t("2026-11-06T00:00:00")
DAY_06 = _t("2026-11-06T00:00:00")
END_06 = _t("2026-11-07T00:00:00")


def show(text: str = "") -> None:
    try:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.flush()
    except (AttributeError, ValueError):
        print(text.encode("ascii", "backslashreplace").decode())


def _r2(x: float | None) -> float | None:
    return None if x is None else round(x, 2)


def tr(x: float) -> str:
    """VND → '2,51 tr' / '-33 k'."""
    if abs(x) >= 1e6:
        return f"{x / 1e6:,.2f} tr".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{x / 1e3:,.0f} k".replace(",", ".")


# ---------------------------------------------------------------------- DB
BACKEND = Path(__file__).resolve().parents[1]


def build_full_db(root: Path) -> duckdb.DuckDBPyConnection:
    """DB như ``scripts/demo.mjs`` mặc định (phương án A): ``app.demo_import --replay-hour 0
    --models`` vào ``root/import`` (backtest + fit + ước lượng iROAS)."""
    t0 = time.perf_counter()
    out = root / "import"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "app.demo_import",
            "--shop",
            str(mock_dir("demo")),
            "--output",
            str(out),
            "--replay-hour",
            "0",
            "--models",
        ],
        cwd=BACKEND,
        check=True,
    )
    conn = duckdb.connect(str(out / "demo.duckdb"))
    engine.cache_clear()
    show(f"DB demo đầy đủ dựng xong trong {time.perf_counter() - t0:.0f} s")
    return conn


def copy_db(root: Path, src: Path) -> duckdb.DuckDBPyConnection:
    """Bản sao của một DB demo có sẵn (không ghi vào file gốc)."""
    path = root / "demo.duckdb"
    shutil.copy2(src, path)
    wal = src.with_name(src.name + ".wal")
    if wal.exists():
        shutil.copy2(wal, path.with_name(path.name + ".wal"))
    conn = duckdb.connect(str(path))
    n = conn.execute("SELECT count(*) FROM iroas_estimates").fetchone()
    if not n or not n[0]:
        write_estimates(conn, estimate_all(conn).estimates)
    engine.cache_clear()
    return conn


def build_db(root: Path, *, backtest: bool) -> duckdb.DuckDBPyConnection:
    """Bản nhẹ (không fit; backtest tùy chọn): số what-if KHÁC pitch, chỉ để thử nhanh."""
    t0 = time.perf_counter()
    conn = duckdb.connect(str(root / "demo.duckdb"))
    migrate(conn)
    repo.seed_bundles(conn)
    ingest_files(conn, mock_dir("demo"), build_panel=False)
    write_live_stream("demo", root / "live_stream", 0)  # đơn + tồn tới 00:59 ngày 11/11
    delta = ingest_live_delta(conn, root / "live_stream", build_panel=False)
    assert delta.status == "loaded", delta
    build_hourly_panel(conn)
    if backtest:
        from app.forecast.service import run_backtests

        run_backtests(conn)
    run = estimate_all(conn)
    write_estimates(conn, run.estimates)
    engine.cache_clear()
    show(f"DB demo dựng xong trong {time.perf_counter() - t0:.0f} s (backtest={backtest})")
    return conn


# ---------------------------------------------------------------------- variants
@dataclass
class Variant:
    vid: str
    title: str
    campaign: str
    start: datetime
    deadline: datetime
    kind: str = "profit"
    target_units: int | None = None
    overrides: dict[str, float] = field(default_factory=dict)
    goal_params: dict[str, Any] = field(default_factory=dict)
    drop_must_run: bool = False


@dataclass
class Row:
    v: Variant
    out: SimOutput
    secs: float

    def summary(self) -> dict[str, Any]:
        rec = self.out.recommendation
        by = {r.action.key: r for r in self.out.results}
        cont = by.get("continue")
        best = by.get(rec.best.key) if rec.best else None
        return {
            "id": self.v.vid,
            "title": self.v.title,
            "campaign": self.v.campaign,
            "start": self.v.start.isoformat(),
            "kind": self.v.kind,
            "target_units": self.v.target_units,
            "overrides": self.v.overrides,
            "drop_must_run": self.v.drop_must_run,
            "status": rec.status,
            "best": rec.best.key if rec.best else None,
            "runner_up": rec.runner_up.key if rec.runner_up else None,
            "p": rec.p_best_beats_runner_up,
            "gain_vs_runner_up": rec.expected_gain_vnd,
            "gain_vs_continue": (
                best.objective.mean - cont.objective.mean if best and cont else None
            ),
            "confidence": rec.confidence,
            "zone_bar": rec.zone_bar.model_dump() if rec.zone_bar else None,
            "flips": [f.text for f in rec.flip_conditions],
            "reasons": rec.reasons,
            "actions": [
                {
                    "key": r.action.key,
                    "feasible": r.feasible,
                    "eligible": r.goal_eligible,
                    "violations": r.violations,
                    "J": r.objective.mean,
                    "J_p10": r.objective.p10,
                    "J_p90": r.objective.p90,
                    "contribution": r.contribution.mean,
                    "spend": r.spend.mean,
                    "sold": r.units_sold.mean,
                    "leftover_units": r.leftover_units.mean,
                    "leftover_value": r.leftover_value.mean,
                    "p_target": r.p_target,
                }
                for r in self.out.results
            ],
            "per_sku_continue": [
                {
                    "sku": s.sku_id,
                    "m": s.contribution_per_unit,
                    "V": s.leftover_value_per_unit,
                    "demand_p50": s.demand.p50,
                    "sold": s.units_sold.mean,
                    "leftover": s.leftover_units.mean if s.leftover_units else None,
                    "stockout_p50": s.stockout_hour_p50.isoformat()
                    if s.stockout_hour_p50
                    else None,
                }
                for s in (cont.per_sku if cont else [])
            ],
        }


def run_variant(conn: duckdb.DuckDBPyConnection, v: Variant) -> Row:
    g = create_goal(
        conn,
        scope_id=v.campaign,
        kind=v.kind,  # type: ignore[arg-type]
        deadline=v.deadline,
        target_units=v.target_units,
        params=v.goal_params,
        confirmed_by="lead-eval",
    )
    req = SimRequest(
        campaign_id=v.campaign,
        goal_id=g.goal_id,
        start=v.start,
        param_overrides=v.overrides,
    )
    t0 = time.perf_counter()
    if v.drop_must_run:
        conn.execute("BEGIN TRANSACTION")
        conn.execute(
            "DELETE FROM sale_commitments WHERE kind = 'campaign_must_run' AND campaign_id = ?",
            [v.campaign],
        )
        try:
            out = run_simulation(conn, req, persist=False, use_cache=False)
        finally:
            conn.execute("ROLLBACK")
    else:
        out = run_simulation(conn, req, persist=False, use_cache=False)
    return Row(v, out, time.perf_counter() - t0)


def print_row(row: Row) -> None:
    s = row.summary()
    show(f"\n=== [{s['id']}] {s['title']}  ({row.secs:.1f} s)")
    show(
        f"    {s['campaign']} từ {row.v.start:%d/%m %H:%M} tới {row.v.deadline:%d/%m %H:%M},"
        f" mục tiêu {s['kind']}"
        + (f" N={s['target_units']}" if s["target_units"] else "")
        + (f" overrides={s['overrides']}" if s["overrides"] else "")
        + (" [bỏ campaign_must_run]" if s["drop_must_run"] else "")
    )
    for a in s["actions"]:
        pt = f" P(đạt N)={a['p_target']:.2f}" if a["p_target"] is not None else ""
        show(
            f"    {a['key']:30s} {'ok ' if a['feasible'] else 'CẤM'}"
            f"{'' if a['eligible'] else ' (không đạt mục tiêu)'}"
            f"  E[J]={tr(a['J']):>10s} [{tr(a['J_p10'])} … {tr(a['J_p90'])}]"
            f"  đóng góp={tr(a['contribution']):>9s} chi={tr(a['spend']):>9s}"
            f" bán={a['sold']:6.1f} dư={a['leftover_units']:6.1f} ({tr(a['leftover_value'])}){pt}"
        )
    p = f"{s['p']:.2f}" if s["p"] is not None else "—"
    gv = tr(s["gain_vs_continue"]) if s["gain_vs_continue"] is not None else "—"
    show(
        f"  → {s['status']} best={s['best']} runner_up={s['runner_up']} p={p}"
        f" lợi hơn runner-up={tr(s['gain_vs_runner_up'] or 0)} lợi hơn continue={gv}"
        f" nhãn={s['confidence']}"
    )
    zb = s["zone_bar"]
    if zb:
        alts = "; ".join(
            f"{a['label']}: x*={_r2(a['threshold'])} ({a['status']})" for a in zb["alt_thresholds"]
        )
        show(
            f"    ZoneBar: x*={zb['threshold']:.2f} sp/giờ, khoảng pause p10/p50/p90 ="
            f" {', '.join(f'{x:.2f}' for x in zb['interval'])} → {zb['verdict']}"
            + (f" | {alts}" if alts else "")
        )
    else:
        show("    ZoneBar: không có")
    for f in s["flips"]:
        show(f"    flip: {f}")
    for r in s["reasons"]:
        show(f"    - {r}")
    for k in s["per_sku_continue"]:
        show(
            f"      [continue] {k['sku']:16s} m={k['m']:>9,.0f} V={k['V']:>9,.0f}"
            f" cầu p50={k['demand_p50']:6.1f} bán={k['sold']:6.1f}"
            f" dư={k['leftover'] if k['leftover'] is None else round(k['leftover'], 1)}"
            f" hết hàng p50={k['stockout_p50']}"
        )


# ---------------------------------------------------------------------- ads table
def ads_table(conn: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    run = estimate_all(conn)
    truth = pd.read_csv(
        ground_truth_dir("demo") / "chien_dich_that.csv", encoding="utf-8-sig"
    ).set_index("ma_chien_dich")
    rows = []
    for e in run.estimates:
        t = truth.loc[e.campaign_id] if e.campaign_id in truth.index else None
        rows.append(
            {
                "campaign": e.campaign_id,
                "name": str(t["ten_chien_dich"]) if t is not None else "",
                "iroas": e.iroas_mean,
                "p10": e.iroas_p10,
                "p90": e.iroas_p90,
                "marginal": e.marginal_iroas,
                "breakeven": e.breakeven_iroas,
                "true": float(t["iroas_that"]) if t is not None else None,
                "label": e.confidence,
                "budget": float(t["ngan_sach_ngay"]) if t is not None else None,
            }
        )
    df = pd.DataFrame(rows).set_index("campaign")
    df["ratio_marg"] = df.marginal / df.breakeven
    df["ratio_p10"] = df.p10 / df.breakeven
    return df


def candidates(df: pd.DataFrame, k: int = 3) -> list[str]:
    """Chiến dịch ngày thường mà ads lời rõ: iROAS ước lượng và thật đều > hòa vốn, xếp
    theo iROAS biên / hòa vốn (quyết định continue vs giảm chi phụ thuộc iROAS biên)."""
    ok = df[df.breakeven.notna() & (df.iroas > df.breakeven) & (df["true"] > df.breakeven)]
    return list(ok.sort_values("ratio_marg", ascending=False).index[:k])


def scan(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> None:
    """Mọi chiến dịch, 4 bối cảnh: tìm ca continue thắng và ca ZoneBar có verdict rõ."""
    camps = [r[0] for r in conn.execute("SELECT campaign_id FROM campaigns ORDER BY 1").fetchall()]
    ctxs = [
        ("11.11", START_1111, END_1111, {}),
        ("11.11 V=0", START_1111, END_1111, {"leftover_value_per_unit": 0}),
        ("06/11", DAY_06, END_06, {}),
        ("06/11 V=0", DAY_06, END_06, {"leftover_value_per_unit": 0}),
        ("06–09/11 V=0", DAY_06, _t("2026-11-09T00:00:00"), {"leftover_value_per_unit": 0}),
    ]
    show("\n=== Quét 49 chiến dịch (best, status, p, lợi hơn runner-up, lợi hơn continue, ZoneBar)")
    for name, st, dl, ov in ctxs:
        show(f"--- {name}")
        for c in camps:
            try:
                row = run_variant(conn, Variant("scan", name, c, st, dl, overrides=dict(ov)))
            except Exception as e:
                show(f"    {c}: lỗi {type(e).__name__}: {e}")
                continue
            sm = row.summary()
            zb = sm["zone_bar"]
            z = (
                f"x*={zb['threshold']:.2f} [{zb['interval'][0]:.2f}–{zb['interval'][2]:.2f}]"
                f" {zb['verdict']}"
                if zb
                else "—"
            )
            gv = tr(sm["gain_vs_continue"]) if sm["gain_vs_continue"] is not None else "—"
            p = f"{sm['p']:.2f}" if sm["p"] is not None else "—"
            r = df.loc[c] if c in df.index else None
            ir = (
                f"iROAS {r.iroas:.2f}/hv {r.breakeven:.2f}/thật {r['true']}"
                if r is not None and not pd.isna(r.breakeven)
                else ""
            )
            show(
                f"    {c} {sm['best']!s:34s} {sm['status']:15s} p={p:4s}"
                f" +{tr(sm['gain_vs_runner_up'] or 0):>9s} vs {sm['runner_up']!s:26s}"
                f" vsC {gv:>9s} zone {z} {ir}"
            )


# ---------------------------------------------------------------------- main
def variants_cd002(v_normal: dict[str, float]) -> list[Variant]:
    c = "CD002"
    base = dict(campaign=c, start=START_1111, deadline=END_1111)
    vs = [
        Variant("1", "Gốc: CD002 11.11 01:00, profit", **base),  # type: ignore[arg-type]
        Variant(
            "2a",
            "Hàng về sớm 1 ngày (11/11 10:00, +400 nồi)",
            **base,  # type: ignore[arg-type]
            overrides={"restock_delay_days": -1},
        ),
        Variant(
            "2b",
            "Hàng về sớm 2 ngày (ngay đầu horizon)",
            **base,  # type: ignore[arg-type]
            overrides={"restock_delay_days": -2},
        ),
        Variant(
            "2c",
            "Hàng về trễ 2 ngày",
            **base,  # type: ignore[arg-type]
            overrides={"restock_delay_days": 2},
        ),
        Variant(
            "2d",
            "Hàng về sớm 1 ngày + V = 0",
            **base,  # type: ignore[arg-type]
            overrides={"restock_delay_days": -1, "leftover_value_per_unit": 0},
        ),
        Variant(
            "2e",
            "Hàng về sớm 1 ngày + V = đóng góp ngày thường",
            **base,  # type: ignore[arg-type]
            overrides={"restock_delay_days": -1, **v_normal},
        ),
        Variant(
            "3a",
            "V = 0 (hàng dư vô giá trị)",
            **base,  # type: ignore[arg-type]
            overrides={"leftover_value_per_unit": 0},
        ),
        Variant(
            "3b",
            "V = 20.000đ/sp",
            **base,  # type: ignore[arg-type]
            overrides={"leftover_value_per_unit": 20_000},
        ),
        Variant(
            "3c",
            "V = đóng góp ngày thường của từng SKU",
            **base,  # type: ignore[arg-type]
            overrides=v_normal,
        ),
        Variant(
            "4a",
            "sell_target N=100 (bán đủ 100 sp trước 24:00)",
            **base,  # type: ignore[arg-type]
            kind="sell_target",
            target_units=100,
        ),
        Variant(
            "4b",
            "sell_target N=103",
            **base,
            kind="sell_target",  # type: ignore[arg-type]
            target_units=103,
        ),
        Variant(
            "4c",
            "sell_target N=105",
            **base,
            kind="sell_target",  # type: ignore[arg-type]
            target_units=105,
        ),
        Variant(
            "4f",
            "sell_target N=95",
            **base,
            kind="sell_target",  # type: ignore[arg-type]
            target_units=95,
        ),
        Variant(
            "4g",
            "sell_target N=97",
            **base,
            kind="sell_target",  # type: ignore[arg-type]
            target_units=97,
        ),
        Variant(
            "4h",
            "sell_target N=100, chấp nhận xác suất 70%",
            **base,  # type: ignore[arg-type]
            kind="sell_target",
            target_units=100,
            goal_params={"sell_target_prob": 0.7},
        ),
        Variant("4d", "clear_stock (lỗ tối đa 0đ)", **base, kind="clear_stock"),  # type: ignore[arg-type]
        Variant(
            "4e",
            "clear_stock, chấp nhận lỗ 2 triệu",
            **base,  # type: ignore[arg-type]
            kind="clear_stock",
            overrides={"max_loss_vnd": 2_000_000},
        ),
        Variant(
            "5",
            "Bỏ ràng buộc flash (campaign_must_run)",
            **base,  # type: ignore[arg-type]
            drop_must_run=True,
        ),
        Variant(
            "5b",
            "Bỏ ràng buộc flash + hàng về sớm 1 ngày",
            **base,  # type: ignore[arg-type]
            overrides={"restock_delay_days": -1},
            drop_must_run=True,
        ),
        Variant(
            "5c",
            "Horizon tới hết 12/11 (hàng về 12/11 10:00 trong horizon)",  # type: ignore[arg-type]
            campaign=c,
            start=START_1111,
            deadline=END_1211,
        ),
    ]
    return vs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="What-if cho kịch bản demo (pitch).")
    ap.add_argument("--db", type=Path, default=None, help="DB demo 01:00 có sẵn (được chép)")
    ap.add_argument(
        "--light", action="store_true", help="bản nhẹ không backtest/fit: số KHÁC pitch"
    )
    ap.add_argument("--backtest", action="store_true", help="với --light: thêm backtest")
    ap.add_argument("--json", type=Path, default=None, help="ghi kết quả JSON")
    ap.add_argument("--only", default=None, help="danh sách id biến thể, phẩy")
    ap.add_argument("--scan", action="store_true", help="quét mọi chiến dịch (thêm ~1 phút)")
    args = ap.parse_args(argv)
    rows: list[Row] = []
    with tempfile.TemporaryDirectory(prefix="whatif_") as tmp:
        if args.db is not None:
            conn = copy_db(Path(tmp), args.db)
            show(f"Dùng bản sao của {args.db}")
        elif args.light:
            show("CẢNH BÁO: bản nhẹ (không backtest/fit), số khác pitch.")
            conn = build_db(Path(tmp), backtest=args.backtest)
        else:
            conn = build_full_db(Path(tmp))
        df = ads_table(conn)
        show("\n=== iROAS ước lượng vs hòa vốn (thật = ground_truth)")
        top = df[df.breakeven.notna()].sort_values("ratio_marg", ascending=False)
        for cid in dict.fromkeys(["CD002", "CD004", *top.index[:10]]):
            r = df.loc[cid]
            show(
                f"    {cid} {r['name'][:40]:40s} iROAS={r.iroas:5.2f} [{r.p10:.2f}–{r.p90:.2f}]"
                f" biên={r.marginal if r.marginal is None else round(r.marginal, 2)}"
                f" hòa vốn={r.breakeven if pd.isna(r.breakeven) else round(r.breakeven, 2)}"
                f" thật={r['true']} nhãn={r.label}"
            )
        # 6. CD002 ngày thường trước, để lấy đóng góp ngày thường làm V
        normal = [
            Variant(
                "6a", "CD002 ngày thường 05/11 09:00 → 24:00, profit", "CD002", NORMAL_05, END_05
            ),
            Variant("6b", "CD002 ngày thường 06/11 cả ngày, profit", "CD002", DAY_06, END_06),
            Variant(
                "6c",
                "CD002 06/11, V = 0",
                "CD002",
                DAY_06,
                END_06,
                overrides={"leftover_value_per_unit": 0},
            ),
            Variant(
                "6d",
                "CD002 06/11, V = 0, hạn 3 ngày (tới 09/11)",
                "CD002",
                DAY_06,
                _t("2026-11-09T00:00:00"),
                overrides={"leftover_value_per_unit": 0},
            ),
            Variant("7a", "CD004 cốc meme 05/11 09:00, profit", "CD004", NORMAL_05, END_05),
            Variant("7b", "CD004 cốc meme 06/11 cả ngày, profit", "CD004", DAY_06, END_06),
            # ZoneBar cho sân khấu (M3-whatif §4c)
            Variant(
                "8a",
                "ZoneBar 'nên dừng': CD020 06/11, V = 0",
                "CD020",
                DAY_06,
                END_06,
                overrides={"leftover_value_per_unit": 0},
            ),
            Variant(
                "8b",
                "ZoneBar 'nên chạy': CD023 11.11, V = 0",
                "CD023",
                START_1111,
                END_1111,
                overrides={"leftover_value_per_unit": 0},
            ),
        ]
        for i, cid in enumerate(candidates(df, 3)):
            normal.append(
                Variant(
                    f"7c{i + 1}",
                    f"{cid} ngày thường 06/11 cả ngày, profit (ads lời?)",
                    cid,
                    DAY_06,
                    END_06,
                )
            )
            normal.append(
                Variant(
                    f"7d{i + 1}",
                    f"{cid} 06/11, V = 0",
                    cid,
                    DAY_06,
                    END_06,
                    overrides={"leftover_value_per_unit": 0},
                )
            )
        only = set(args.only.split(",")) if args.only else None
        r6 = run_variant(conn, normal[1])  # 6b: đóng góp ngày thường
        v_normal = {
            f"leftover_value_per_unit@{k.sku_id}": round(max(k.contribution_per_unit, 0.0))
            for k in next(r for r in r6.out.results if r.action.kind == "continue").per_sku
            if not k.is_bundle
        }
        show(f"\nV theo đóng góp ngày thường (06/11): {v_normal}")
        normal.insert(
            3,
            Variant(
                "6e",
                "CD002 06/11, V = đóng góp ngày thường",
                "CD002",
                DAY_06,
                END_06,
                overrides=v_normal,
            ),
        )
        for v in [*variants_cd002(v_normal), *normal]:
            if only and v.vid not in only:
                continue
            row = r6 if v.vid == "6b" else run_variant(conn, v)
            rows.append(row)
            print_row(row)
            r = df.loc[v.campaign] if v.campaign in df.index else None
            if r is not None:
                show(
                    f"    [đáp án thật] iROAS thật {r['true']} vs hòa vốn ngày thường"
                    f" {r.breakeven if pd.isna(r.breakeven) else round(r.breakeven, 2)}"
                    f" (ước lượng {r.iroas:.2f}, biên {_r2(r.marginal)})"
                )
        if args.scan:
            scan(conn, df)
        conn.close()
    show("\n=== Tóm tắt")
    for row in rows:
        s = row.summary()
        gv = tr(s["gain_vs_continue"]) if s["gain_vs_continue"] is not None else "—"
        p = f"{s['p']:.2f}" if s["p"] is not None else "—"
        show(
            f"  [{s['id']:4s}] {s['title'][:58]:58s} best={s['best']} ({s['status']}, p={p},"
            f" +{tr(s['gain_vs_runner_up'] or 0)} vs {s['runner_up']}; vs continue {gv})"
            f" zone={'có' if s['zone_bar'] else 'không'} flips={len(s['flips'])}"
        )
    if args.json:
        args.json.write_text(
            json.dumps([r.summary() for r in rows], ensure_ascii=False, indent=1, default=str),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
