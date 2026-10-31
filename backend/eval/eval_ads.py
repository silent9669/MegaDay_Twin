"""EVAL-07 · Chấm iROAS bằng đáp án (plan 04 §9, 12 · M3).

Chạy ``app.ads_effect.iroas.estimate_all`` trên kho đã nạp lịch sử (tự nạp vào DuckDB tạm
bằng pipeline thật, hoặc đọc ``--db`` có sẵn ở chế độ chỉ đọc), rồi so với
``mock-data/out/<cỡ>/ground_truth/chien_dich_that.csv`` (``iroas_that``,
``roas_san_bao_cao``). Chỉ thư mục ``eval/`` và test được đọc đáp án.

Chạy thêm một lượt **không loại giờ sắp hết hàng** (04 §4 tắt) để in mức sai số tăng lên.

Lệnh::

    uv run python -m eval.eval_ads --size demo
    uv run python -m app.cli eval --size demo --kind ads

Báo cáo: ``eval/reports/ads_<cỡ>_<YYYYMMDD-HHMM>.md`` + ``.json`` (không commit).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import warnings
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from app.ads_effect.iroas import AdsRun, estimate_all
from app.ads_effect.schemas import IroasEstimate
from app.ingest.pipeline import ingest_files
from app.warehouse import repo
from app.warehouse.db import Database
from eval import REPORTS_DIR, ground_truth_dir, mock_dir

VN = "Asia/Ho_Chi_Minh"
PERSONAS = ("CD001", "CD002", "CD003", "CD004", "CD005", "CD006")
PERSONA_NAMES = {
    "CD001": "Bình nước giữ nhiệt",
    "CD002": "Nồi chiên mini + giấy lót",
    "CD003": "Đèn LED Tết",
    "CD004": "Cốc in hình meme",
    "CD005": "Áo giữ nhiệt nam",
    "CD006": "Máy hút bụi mini (mới)",
}

# Thresholds (04 §9, 12 · M3). Not relaxed: a miss is reported as a miss.
T_MEDIAN_ERR = 0.25
T_CLOSER = 0.80
T_COVER = 0.70
T_COVER_MIN_SPEND = 2_000_000
T_PERSONA_ERR = 0.30
T_DIRECTION = 1.0
T_DECISION = 0.90
T_DELTA = (0.5, 0.6, 0.7)
T_SECONDS = 10.0


@dataclass
class Check:
    key: str
    name: str
    value: str
    threshold: str
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)


def _truth(size: str) -> pd.DataFrame:
    df = pd.read_csv(ground_truth_dir(size) / "chien_dich_that.csv", encoding="utf-8-sig")
    return df.set_index("ma_chien_dich")


def _pct(x: float | None) -> str:
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:.1f} %".replace(".", ",")


def _n(x: float | None, d: int = 2) -> str:
    return "—" if x is None or not np.isfinite(x) else f"{x:.{d}f}".replace(".", ",")


def table(run: AdsRun, truth: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for e in run.estimates:
        if e.campaign_id not in truth.index:
            continue
        t = truth.loc[e.campaign_id]
        rows.append(
            {
                "campaign_id": e.campaign_id,
                "name": str(t["ten_chien_dich"]),
                "spend": float(t["chi_lich_su"]),
                "roas_platform": float(t["roas_san_bao_cao"]),
                "iroas_true": float(t["iroas_that"]),
                "iroas_mean": e.iroas_mean,
                "iroas_p10": e.iroas_p10,
                "iroas_p90": e.iroas_p90,
                "marginal": e.marginal_iroas,
                "breakeven": e.breakeven_iroas,
                "a_mean": e.a_mean,
                "kappa_mean": e.kappa_mean,
                "label": e.confidence,
                "n_off": e.n_clean_off_hours,
                "excluded_stock": e.excluded_hours.get("low_stock", 0)
                + e.excluded_hours.get("stock_pause", 0),
                "flags": ",".join(e.flags),
            }
        )
    df = pd.DataFrame(rows).set_index("campaign_id")
    df["rel_err"] = (df.iroas_mean - df.iroas_true).abs() / df.iroas_true
    df["covered"] = (df.iroas_p10 <= df.iroas_true) & (df.iroas_true <= df.iroas_p90)
    df["closer"] = (df.iroas_mean - df.iroas_true).abs() < (df.roas_platform - df.iroas_true).abs()
    return df


def checks(run: AdsRun, df: pd.DataFrame, ablation: pd.DataFrame) -> list[Check]:
    out: list[Check] = []
    med = float(df.rel_err.median())
    out.append(Check("median_err", "Sai số tương đối trung vị |ước lượng − thật| / thật",
                     _pct(med), f"≤ {_pct(T_MEDIAN_ERR)}", med <= T_MEDIAN_ERR))  # fmt: skip
    closer = float(df.closer.mean())
    out.append(Check("closer", "Ước lượng gần iROAS thật hơn ROAS sàn",
                     f"{_pct(closer)} ({int(df.closer.sum())}/{len(df)})",
                     f"≥ {_pct(T_CLOSER)}", closer >= T_CLOSER))  # fmt: skip
    big = df[df.spend >= T_COVER_MIN_SPEND]
    cov = float(big.covered.mean()) if len(big) else float("nan")
    out.append(Check("coverage", "iROAS thật nằm trong [p10, p90] (chi lịch sử ≥ 2 triệu đ)",
                     f"{_pct(cov)} ({int(big.covered.sum())}/{len(big)})",
                     f"≥ {_pct(T_COVER)}", cov >= T_COVER))  # fmt: skip
    pers = df.loc[[c for c in PERSONAS if c in df.index]]
    bad = [c for c in pers.index if pers.loc[c, "rel_err"] > T_PERSONA_ERR]
    out.append(Check("personas", "6 chiến dịch nhân vật (CD001–CD006) mỗi cái sai ≤ 30 %",
                     ", ".join(f"{c} {_pct(float(pers.loc[c, 'rel_err']))}" for c in pers.index),
                     "mọi chiến dịch ≤ 30 %", not bad, {"over": bad}))  # fmt: skip
    inflated = df[df.iroas_true < df.roas_platform]
    dir_ok = float((inflated.iroas_mean < inflated.roas_platform).mean())
    out.append(Check("direction", "iROAS ước lượng < ROAS sàn (khi iROAS thật < ROAS sàn)",
                     f"{_pct(dir_ok)} ({len(inflated)} chiến dịch)", "100 %",
                     dir_ok >= T_DIRECTION))  # fmt: skip
    be = df[df.breakeven.notna()]
    same = np.sign(be.iroas_mean - be.breakeven) == np.sign(be.iroas_true - be.breakeven)
    dec = float(same.mean()) if len(be) else float("nan")
    cd004 = df.loc["CD004"] if "CD004" in df.index else None
    cd004_loss = bool(
        cd004 is not None
        and cd004["breakeven"] is not None
        and np.isfinite(cd004["breakeven"])
        and cd004["iroas_mean"] < cd004["breakeven"]
    )
    out.append(Check("decision", "Dấu (iROAS − hòa vốn) đúng như thật; CD004 ra 'lỗ'",
                     f"{_pct(dec)} ({int(same.sum())}/{len(be)}, {len(df) - len(be)} thiếu "
                     f"giá vốn); CD004 {'lỗ' if cd004_loss else 'không lỗ'}",
                     f"≥ {_pct(T_DECISION)} và CD004 lỗ", dec >= T_DECISION and cd004_loss,
                     {"wrong": [c for c in be.index[~same]]}))  # fmt: skip
    trap = df[df.excluded_stock > 0].index.intersection(ablation.index)
    med_rules = float(df.loc[trap, "rel_err"].median()) if len(trap) else float("nan")
    med_none = float(ablation.loc[trap, "rel_err"].median()) if len(trap) else float("nan")
    out.append(Check("stock_trap", "Bẫy gây nhiễu: tắt luật loại giờ sắp hết hàng",
                     f"sai số trung vị {_pct(med_rules)} → {_pct(med_none)} trên {len(trap)} "
                     "chiến dịch có giờ bị loại vì tồn",
                     "tăng rõ", med_none > med_rules * 1.1,
                     {"campaigns": list(trap)}))  # fmt: skip
    out.append(Check("delta", "δ̂ (ads còn tác dụng sau mỗi giờ)", _n(run.delta, 1),
                     "∈ {0,5; 0,6; 0,7}", run.delta in T_DELTA,
                     {"scores": {str(k): v for k, v in run.delta_scores.items()}}))  # fmt: skip
    out.append(Check("seconds", "Thời gian ước lượng toàn bộ chiến dịch",
                     f"{_n(run.seconds, 1)} giây", f"< {_n(T_SECONDS, 0)} giây",
                     run.seconds < T_SECONDS))  # fmt: skip
    return out


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# Chấm iROAS · `{result['size']}` · {result['created_at_local']}",
        "",
        f"δ̂ = {_n(result['delta'], 1)}; nhịp giờ từ `{result['rhythm_source']}`; "
        f"nhãn: {result['labels']}; {result['n_campaigns']} chiến dịch. "
        "Số trên dữ liệu mô phỏng.",
        "",
        "| Kiểm tra | Kết quả | Ngưỡng | Đạt |",
        "|---|---|---|---|",
    ]
    for c in result["checks"]:
        lines.append(f"| {c['name']} | {c['value']} | {c['threshold']} | {_mark(c['ok'])} |")
    lines += [
        "",
        "## Bảng cho slide (chiến dịch nhân vật)",
        "",
        "| Chiến dịch | ROAS sàn | iROAS ước lượng [p10–p90] | iROAS thật | Hòa vốn | Nhãn |",
        "|---|---|---|---|---|---|",
    ]
    rows = {r["campaign_id"]: r for r in result["campaigns"]}
    for cid in PERSONAS:
        r = rows.get(cid)
        if r is None:
            continue
        lines.append(
            f"| {cid} {PERSONA_NAMES[cid]} | {_n(r['roas_platform'])} | {_n(r['iroas_mean'])} "
            f"[{_n(r['iroas_p10'])}–{_n(r['iroas_p90'])}] | {_n(r['iroas_true'])} | "
            f"{_n(r['breakeven'])} | {r['label']} |"
        )
    lines += [
        "",
        "## Mọi chiến dịch",
        "",
        "| Chiến dịch | Chi (tr đ) | ROAS sàn | iROAS [p10–p90] | Thật | Sai | Phủ | Gần hơn "
        "| a | κ | Giờ tắt sạch | Nhãn | Không loại nhiễu |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in result["campaigns"]:
        lines.append(
            f"| {r['campaign_id']} | {_n(r['spend'] / 1e6, 1)} | {_n(r['roas_platform'])} | "
            f"{_n(r['iroas_mean'])} [{_n(r['iroas_p10'])}–{_n(r['iroas_p90'])}] | "
            f"{_n(r['iroas_true'])} | {_pct(r['rel_err'])} | {_mark(r['covered'])} | "
            f"{_mark(r['closer'])} | {_n(r['a_mean'])} | {_n(r['kappa_mean'], 1)} | "
            f"{r['n_off']} | {r['label']} | {_n(r.get('iroas_no_rules'))} |"
        )
    return "\n".join(lines) + "\n"


def _estimate(conn: duckdb.DuckDBPyConnection) -> tuple[AdsRun, AdsRun]:
    run = estimate_all(conn)
    ablation = estimate_all(conn, apply_stock_rules=False, delta=run.delta)
    return run, ablation


def run(size: str, *, db: Path | None = None) -> dict[str, Any]:
    if db is not None:
        conn = duckdb.connect(str(db), read_only=True)
        conn.execute("SET TimeZone = 'UTC'")
        try:
            main_run, abl = _estimate(conn)
        finally:
            conn.close()
    else:
        with tempfile.TemporaryDirectory(prefix="eval-ads-") as tmp:
            database = Database(Path(tmp) / "eval.duckdb")
            database.migrate()
            repo.seed_bundles(database.write)
            ingest_files(database.write, mock_dir(size))
            main_run, abl = _estimate(database.write)
            database.close()
    truth = _truth(size)
    df = table(main_run, truth)
    ab = table(abl, truth)
    df["iroas_no_rules"] = ab.iroas_mean.reindex(df.index)
    cks = checks(main_run, df, ab)
    now = datetime.now(UTC)
    labels: dict[str, int] = main_run.labels
    persona_ests: dict[str, IroasEstimate] = {
        e.campaign_id: e for e in main_run.estimates if e.campaign_id in PERSONAS
    }
    return {
        "size": size,
        "created_at": now.isoformat(),
        "created_at_local": pd.Timestamp(now).tz_convert(VN).strftime("%Y-%m-%d %H:%M"),
        "delta": main_run.delta,
        "rhythm_source": main_run.rhythm_source,
        "labels": ", ".join(f"{k} {v}" for k, v in sorted(labels.items())),
        "n_campaigns": len(df),
        "seconds": main_run.seconds,
        "checks": [asdict(c) for c in cks],
        "campaigns": df.reset_index().replace({np.nan: None}).to_dict(orient="records"),
        "persona_reasons": {k: v.reasons for k, v in persona_ests.items()},
        "passed_m3": all(c.ok for c in cks if c.key in ("median_err", "closer")),
        "passed": all(c.ok for c in cks),
    }


def write_report(result: dict[str, Any], out_dir: Path = REPORTS_DIR) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp(result["created_at"]).tz_convert(VN).strftime("%Y%m%d-%H%M")
    md = out_dir / f"ads_{result['size']}_{stamp}.md"
    js = out_dir / f"ads_{result['size']}_{stamp}.json"
    md.write_text(render_markdown(result), encoding="utf-8")
    js.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return md, js


def main(argv: list[str] | None = None) -> int:
    warnings.filterwarnings("ignore", message="The inplace keyword")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Chấm iROAS bằng ground_truth (04 §9).")
    ap.add_argument("--size", default="demo")
    ap.add_argument("--db", type=Path, default=None, help="DuckDB đã nạp lịch sử (chỉ đọc).")
    ap.add_argument("--out", type=Path, default=REPORTS_DIR)
    args = ap.parse_args(argv)
    if not (ground_truth_dir(args.size) / "chien_dich_that.csv").exists():
        print(f"Không thấy mock-data/out/{args.size}. Chạy node generate.js --size {args.size}.")
        return 2
    result = run(args.size, db=args.db)
    md, js = write_report(result, args.out)
    print(render_markdown(result))
    print(f"\nĐã ghi {md} và {js}")
    return 0 if result["passed_m3"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
