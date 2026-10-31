"""(3) Forecast scoring against the mock answer key (EVF-01, plans 03 §10, 11 §2, 12 · M2).

Runs the app's rolling-origin backtest (``app.forecast.backtest``) and scores the very same
forecasts against ``nhu_cau_that`` (true demand) **and** ``ban_duoc`` (what could be sold),
to show what the stockout correction buys. Only this package reads the answer key.

Data: ``--db <duckdb>`` (built by the ingest pipeline) or, until the pipeline is merged, an
in-memory DuckDB filled by the test loader ``tests/unit/panel/mock_loader.py`` (history +
the 11.11 day, panel built with ``app.panel``).

    uv run python -m eval.eval_forecast --size demo
    uv run python -m eval.eval_forecast --size nho --quick

Report: ``backend/eval/reports/forecast_<size>_<YYYYMMDD-HHMM>.md`` + ``.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from app.core.timeutil import utcnow
from app.forecast import service
from app.forecast.backtest import BacktestResult, default_specs, run_backtest
from app.forecast.base import ForecastRequest
from app.forecast.context import build_fit_context
from app.forecast.data import PanelMatrix, vn_midnight_utc
from app.forecast.drift import active_up
from app.forecast.labels import WAPE_FLOOR_TOL, coverage_ok, wape_ok
from app.forecast.metrics import bias as m_bias
from app.forecast.metrics import floor_stats, pinball, pit_coverage, randomized_pit, wape_floor
from app.forecast.metrics import wape as m_wape
from app.forecast.models.trend_damped import TrendDamped
from app.forecast.registry import write_scores
from eval import REPORTS_DIR, ground_truth_dir, mock_dir

VN = "Asia/Ho_Chi_Minh"
CHARACTERS = {
    "BINHNUOC500": "on_dinh",
    "COCMEME": "trend",
    "AOGIUNHIET": "mua_vu",
    "DENLEDTET": "mua_vu",
    "MAYHUTMINI": "moi",
}
M2_GAIN = 0.15
COVERAGE_BAND = (0.72, 0.88)


# ---------------------------------------------------------------------- data
def open_db(size: str, db: str | None) -> duckdb.DuckDBPyConnection:
    if db:
        return duckdb.connect(db, read_only=True)
    from app.panel.build import build_hourly_panel
    from tests.mockdata import load_mock

    conn = duckdb.connect()
    load_mock(conn, size, live=True)
    build_hourly_panel(conn)
    return conn


def load_truth(size: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    gt = ground_truth_dir(size)
    hourly = pd.read_csv(gt / "nhu_cau_theo_gio.csv", encoding="utf-8-sig")
    hourly["hour_utc"] = pd.to_datetime(hourly["thoi_gian"]).dt.tz_localize(VN).dt.tz_convert("UTC")
    hourly["day"] = pd.to_datetime(hourly["thoi_gian"]).dt.date
    hourly = hourly.rename(columns={"sku": "sku_id"})
    daily = pd.read_csv(gt / "nhu_cau_theo_ngay.csv", encoding="utf-8-sig")
    daily["day"] = pd.to_datetime(daily["ngay"]).dt.date
    daily = daily.rename(columns={"sku": "sku_id"})
    skus = pd.read_csv(gt / "sku_that.csv", encoding="utf-8-sig", keep_default_na=False)
    return hourly, daily, skus


# ---------------------------------------------------------------------- baselines
def naive_baselines(m: PanelMatrix, res: BacktestResult) -> pd.DataFrame:
    """Seasonal naive forecasts per (SKU, day) of every backtest origin, from observed
    sales before the origin: ``naive7`` = mean of the same hour over the 7 previous days
    (03 §10, 11 §2); ``naive_week`` = same hour one week earlier."""
    rows = []
    keys = res.days[["backtest", "origin"]].drop_duplicates()
    for bt, origin in keys.itertuples(index=False):
        spec = next(s for s in default_specs() if s.name == bt)
        t0 = m.hour_index(origin)
        hrs = spec.horizon_hours
        hist = m.y[:, max(0, t0 - 7 * 24) : t0]
        n_hist_days = hist.shape[1] // 24
        prof = hist[:, -n_hist_days * 24 :].reshape(len(m.sku_ids), n_hist_days, 24).mean(axis=1)
        for k in range(hrs // 24):
            day = (pd.Timestamp(origin) + pd.Timedelta(hours=7 + 24 * k)).date()
            lw = t0 + 24 * k - 7 * 24
            week = m.y[:, lw : lw + 24].sum(axis=1)
            n7 = prof.sum(axis=1)
            for i, sku in enumerate(m.sku_ids):
                rows.append(
                    {
                        "backtest": bt,
                        "origin": origin,
                        "sku_id": sku,
                        "day": day,
                        "naive7": float(n7[i]),
                        "naive_week": float(week[i]),
                    }
                )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------- scoring
def champion_days(res: BacktestResult) -> pd.DataFrame:
    d = res.days
    champ = [
        res.champions.get((g, c)) == mid
        for g, c, mid in zip(d["demand_group"], d["context"], d["model_id"], strict=True)
    ]
    return d[np.asarray(champ, dtype=bool)].copy()


def attach_truth(days: pd.DataFrame, truth_day: pd.DataFrame) -> pd.DataFrame:
    return days.merge(truth_day, on=["sku_id", "day"], how="inner")


def truth_by_day(hourly: pd.DataFrame) -> pd.DataFrame:
    return hourly.groupby(["sku_id", "day"], as_index=False).agg(
        true=("nhu_cau_that", "sum"),
        sold=("ban_duoc", "sum"),
        so_hours=("het_hang", "sum"),
        lam_true=("lambda_co_ads", "sum"),
    )


def metrics(
    df: pd.DataFrame, pred: str, target: str, lo: str | None, hi: str | None
) -> dict[str, Any]:
    y = df[target].to_numpy(float)
    p = df[pred].to_numpy(float)
    out: dict[str, Any] = {
        "n_sku_days": len(df),
        "n_skus": int(df["sku_id"].nunique()) if len(df) else 0,
        "wape": m_wape(y, p),
        "bias": m_bias(y, p),
    }
    if lo and hi:
        out["coverage"] = float(np.mean((y >= df[lo]) & (y <= df[hi]))) if len(df) else None
    return out


def eligible_skus(days: pd.DataFrame) -> pd.DataFrame:
    """03 §7.2: WAPE only on SKUs with ≥ 1 unit/day (flag computed by the app before origin)."""
    return days[days["wape_eligible"].astype(bool)]


@dataclass
class Check:
    name: str
    value: str
    threshold: str
    ok: bool | None
    note: str = ""


@dataclass
class Report:
    size: str
    started: datetime
    checks: list[Check] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


def pct(v: float | None) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.1%}"


def table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        cells = []
        for v in row:
            if isinstance(v, float):
                cells.append("—" if np.isnan(v) else f"{v:.3f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------- main parts
def score_backtests(
    rep: Report,
    m: PanelMatrix,
    res: BacktestResult,
    res_raw: BacktestResult,
    hourly: pd.DataFrame,
) -> None:
    tday = truth_by_day(hourly)
    cd = attach_truth(champion_days(res), tday)
    base = naive_baselines(m, res)
    cd = cd.merge(base, on=["backtest", "origin", "sku_id", "day"], how="left")
    rep.data["champions"] = {f"{g}|{c}": v for (g, c), v in res.champions.items()}

    # --- M2: champion vs naive baseline on 10.10 (shop) ---------------------------
    dd = cd[cd["backtest"] == "dd_1010"]
    dde = eligible_skus(dd)
    w_ch = metrics(dde, "full_p50", "true", "full_p10", "full_p90")
    w_n7 = metrics(dde, "naive7", "true", None, None)
    w_nw = metrics(dde, "naive_week", "true", None, None)
    w_all = metrics(dd, "full_p50", "true", "full_p10", "full_p90")
    w_all_n7 = metrics(dd, "naive7", "true", None, None)
    gain7 = 1 - w_ch["wape"] / w_n7["wape"] if w_n7["wape"] else None
    gainw = 1 - w_ch["wape"] / w_nw["wape"] if w_nw["wape"] else None
    rep.checks.append(
        Check(
            "M2 · WAPE 10.10 champion / baseline 7 ngày cùng giờ (toàn shop, SKU ≥ 1 sp/ngày)",
            f"{pct(w_ch['wape'])} / {pct(w_n7['wape'])} (tốt hơn {pct(gain7)})",
            "tốt hơn ≥ 15%",
            gain7 is not None and gain7 >= M2_GAIN,
            f"tất cả SKU: {pct(w_all['wape'])} / {pct(w_all_n7['wape'])}",
        )
    )
    rep.checks.append(
        Check(
            "M2 · WAPE 10.10 champion / baseline cùng giờ tuần trước",
            f"{pct(w_ch['wape'])} / {pct(w_nw['wape'])} (tốt hơn {pct(gainw)})",
            "tốt hơn ≥ 15%",
            gainw is not None and gainw >= M2_GAIN,
        )
    )
    nr = cd[cd["backtest"] == "normal_roll"]
    cov_nr = metrics(nr, "full_p50", "true", "full_p10", "full_p90")
    rep.checks.append(
        Check(
            "(cách đo cũ) Coverage [p10, p90] theo ngày, normal_roll",
            pct(cov_nr.get("coverage")),
            "0,72–0,88 (chỉ để so)",
            None,
            f"10.10: {pct(w_all.get('coverage'))}; số đếm nhỏ làm cách đo này lệch lên, xem §D",
        )
    )
    # 03 §10 checks
    nr_od = eligible_skus(nr[nr["demand_group"] == "on_dinh"])
    w_nr = metrics(nr_od, "full_p50", "true", None, None)
    rep.checks.append(
        Check(
            "(cách đo cũ) WAPE ngày thường ≤ 30% (normal_roll, on_dinh)",
            pct(w_nr["wape"]),
            "≤ 30% (chỉ để so)",
            None,
            "thay bằng WAPE ≤ 1,15 × WAPE_floor, xem §D",
        )
    )
    dd_os = eligible_skus(dd[dd["demand_group"].isin(["on_dinh", "mua_vu"])])
    w_os = metrics(dd_os, "full_p50", "true", None, None)
    rep.checks.append(
        Check(
            "03 §10 · WAPE 10.10 (on_dinh + mua_vu)",
            pct(w_os["wape"]),
            "≤ 40%",
            w_os["wape"] is not None and w_os["wape"] <= 0.40,
        )
    )

    # --- detail: model × group × backtest, against true demand ---------------------
    alld = attach_truth(res.days, tday)
    rows = []
    for (bt, g, mid), part in alld.groupby(["backtest", "demand_group", "model_id"]):
        e = eligible_skus(part)
        mt = metrics(e, "full_p50", "true", "full_p10", "full_p90") if len(e) else {}
        ms = metrics(e, "full_p50", "sold", None, None) if len(e) else {}
        rows.append(
            {
                "backtest": bt,
                "nhóm": g,
                "model": mid.split("@")[0]
                + (" ★" if res.champions.get((g, part["context"].iat[0])) == mid else ""),
                "SKU-ngày": len(part),
                "WAPE thật": mt.get("wape", np.nan) if mt.get("wape") is not None else np.nan,
                "bias thật": mt.get("bias", np.nan) if mt.get("bias") is not None else np.nan,
                "WAPE bán được": ms.get("wape", np.nan) if ms.get("wape") is not None else np.nan,
                "coverage": metrics(part, "full_p50", "true", "full_p10", "full_p90")["coverage"],
            }
        )
    detail = pd.DataFrame(rows).sort_values(["backtest", "nhóm", "model"])
    # baseline rows per backtest (shop)
    brow = []
    for bt, part in cd.groupby("backtest"):
        e = eligible_skus(part)
        brow.append(
            {
                "backtest": bt,
                "champion": metrics(e, "full_p50", "true", None, None)["wape"],
                "oracle λ thật": metrics(e, "lam_true", "true", None, None)["wape"],
                "naive7": metrics(e, "naive7", "true", None, None)["wape"],
                "naive_week": metrics(e, "naive_week", "true", None, None)["wape"],
                "coverage": metrics(part, "full_p50", "true", "full_p10", "full_p90")["coverage"],
                "WAPE champion vs bán được": metrics(e, "full_p50", "sold", None, None)["wape"],
            }
        )
    shop = pd.DataFrame(brow)
    rep.sections.append(
        "## Toàn shop: champion so với baseline ngây thơ (WAPE theo SKU × ngày, so với "
        "`nhu_cau_that`, SKU ≥ 1 sp/ngày). `oracle λ thật` = dự báo bằng chính λ của bộ "
        "sinh: mức nhiễu không thể giảm (sàn WAPE).\n\n" + table(shop.fillna(np.nan))
    )
    rep.sections.append(
        "## Chi tiết model × nhóm × đợt (★ = champion app chọn bằng backtest nội bộ)\n\n"
        + table(detail)
    )
    rep.data["shop"] = shop.to_dict(orient="records")
    rep.data["detail"] = detail.to_dict(orient="records")

    # --- pinball (hour level) for champions vs true demand -------------------------
    if res.hours is not None:
        hh = res.hours
        ch_ids = set(res.champions.values())
        hh = hh[hh["model_id"].isin(ch_ids)].merge(
            hourly[["sku_id", "hour_utc", "nhu_cau_that"]], on=["sku_id", "hour_utc"], how="inner"
        )
        pins = []
        for bt, part in hh.groupby("backtest"):
            y = part["nhu_cau_that"].to_numpy(float)
            pins.append(
                {
                    "backtest": bt,
                    "pinball_10": pinball(y, part["p10"].to_numpy(float), 0.1),
                    "pinball_50": pinball(y, part["p50"].to_numpy(float), 0.5),
                    "pinball_90": pinball(y, part["p90"].to_numpy(float), 0.9),
                    "coverage giờ": float(np.mean((y >= part["p10"]) & (y <= part["p90"]))),
                }
            )
        rep.sections.append(
            "## Pinball theo giờ (mọi model ứng viên champion, so với `nhu_cau_that`)\n\n"
            + table(pd.DataFrame(pins))
        )

    # --- censoring: corrected vs uncorrected on SKUs with ≥ 5 stockout hours --------
    so = hourly[(hourly["day"] >= date(2026, 10, 9)) & (hourly["day"] <= date(2026, 10, 11))]
    so_n = so.groupby("sku_id")["het_hang"].sum()
    heavy = set(so_n[so_n >= 5].index)
    pe = "profile_ewma@1.0.0"
    cor = attach_truth(
        res.days[(res.days["backtest"] == "dd_1010") & (res.days["model_id"] == pe)], tday
    )
    raw = attach_truth(
        res_raw.days[(res_raw.days["backtest"] == "dd_1010") & (res_raw.days["model_id"] == pe)],
        tday,
    )
    cor_h, raw_h = cor[cor["sku_id"].isin(heavy)], raw[raw["sku_id"].isin(heavy)]
    b_cor = metrics(cor_h, "full_p50", "true", None, None)
    b_raw = metrics(raw_h, "full_p50", "true", None, None)
    bm_cor = metrics(cor_h, "full_mean", "true", None, None)
    bm_raw = metrics(raw_h, "full_mean", "true", None, None)
    ok_c = (
        b_cor["bias"] is not None
        and abs(b_cor["bias"]) <= 0.10
        and b_raw["bias"] is not None
        and b_raw["bias"] < b_cor["bias"]
    )
    rep.checks.append(
        Check(
            "03 §10 · Censoring 10.10: |bias| có hiệu chỉnh vs không"
            f" ({len(heavy)} SKU ≥ 5 giờ hết hàng)",
            f"{pct(b_cor['bias'])} vs {pct(b_raw['bias'])}",
            "|bias| hiệu chỉnh ≤ 10%; bản không hiệu chỉnh âm rõ",
            ok_c if heavy else None,
            f"theo mean: {pct(bm_cor['bias'])} vs {pct(bm_raw['bias'])};"
            " profile_ewma, p50 tổng ngày",
        )
    )
    # all SKUs (any stockouts) across every backtest: corrected vs uncorrected
    comp = []
    for bt in sorted(set(res_raw.days["backtest"])):
        a = attach_truth(
            res.days[(res.days["backtest"] == bt) & (res.days["model_id"] == pe)], tday
        )
        b = attach_truth(
            res_raw.days[(res_raw.days["backtest"] == bt) & (res_raw.days["model_id"] == pe)], tday
        )
        sk = a.groupby("sku_id")["so_hours"].sum()
        hv = set(sk[sk >= 5].index)
        a, b = a[a["sku_id"].isin(hv)], b[b["sku_id"].isin(hv)]
        comp.append(
            {
                "backtest": bt,
                "SKU ≥ 5 giờ hết": len(hv),
                "bias hiệu chỉnh": metrics(a, "full_p50", "true", None, None)["bias"],
                "bias không hiệu chỉnh": metrics(b, "full_p50", "true", None, None)["bias"],
                "WAPE hiệu chỉnh": metrics(a, "full_p50", "true", None, None)["wape"],
                "WAPE không hiệu chỉnh": metrics(b, "full_p50", "true", None, None)["wape"],
            }
        )
    rep.sections.append(
        "## Hiệu chỉnh nhu cầu bị chặn bởi tồn"
        " (profile_ewma, SKU có ≥ 5 giờ hết hàng trong cửa sổ)\n\n"
        + table(pd.DataFrame(comp).astype({"SKU ≥ 5 giờ hết": int}))
    )
    rep.data["censoring"] = comp
    # NOICHIEN2L illustration on 10.10
    ill = []
    for lab, src in (("có hiệu chỉnh", cor), ("không hiệu chỉnh", raw)):
        part = src[src["sku_id"] == "NOICHIEN2L"]
        for r in part.itertuples(index=False):
            ill.append(
                {
                    "bản": lab,
                    "ngày": str(r.day),
                    "nhu cầu thật": r.true,
                    "bán được": r.sold,
                    "giờ hết hàng": int(r.so_hours),
                    "dự báo p50": r.full_p50,
                    "p10–p90": f"{r.full_p10:.0f}–{r.full_p90:.0f}",
                }
            )
    if ill:
        rep.sections.append(
            "## Minh họa censoring: NOICHIEN2L, 09–11/10\n\n" + table(pd.DataFrame(ill))
        )

    # --- 'Lệch do hết hàng' (11 §2): WAPE vs ban_duoc − vs nhu_cau_that on het_hang hours
    if res.hours is not None:
        hh = res.hours.merge(
            hourly[["sku_id", "hour_utc", "nhu_cau_that", "ban_duoc", "het_hang"]],
            on=["sku_id", "hour_utc"],
        )
        champ_ids = {(g, c): mm for (g, c), mm in res.champions.items()}
        hh = hh[hh["model_id"].isin(set(champ_ids.values()))]
        hs = hh[hh["het_hang"] == 1]
        if len(hs):
            wt = m_wape(hs["nhu_cau_that"].to_numpy(float), hs["mean"].to_numpy(float))
            ws = m_wape(hs["ban_duoc"].to_numpy(float), hs["mean"].to_numpy(float))
            rep.sections.append(
                "## Chấm bằng `ban_duoc` thay vì `nhu_cau_that` (giờ hết hàng, dự báo mean)\n\n"
                f"WAPE so với nhu cầu thật {pct(wt)}; so với số bán được {pct(ws)}; "
                f"lệch do hết hàng = {pct(None if ws is None or wt is None else ws - wt)}. "
                "Chấm bằng số bán phạt model vì đã dự báo đúng phần nhu cầu bị mất."
            )
            rep.data["stockout_gap"] = {"wape_true": wt, "wape_sold": ws}


def classification(
    rep: Report, conn: duckdb.DuckDBPyConnection, m: PanelMatrix, skus: pd.DataFrame
) -> None:
    origin = vn_midnight_utc(date(2026, 11, 11))
    ctx = service.build_context(conn, origin, matrix=m)
    truth = dict(zip(skus["sku"], skus["nhom_nhu_cau"], strict=True))
    rows = []
    for i, s in enumerate(ctx.sku_ids):
        rows.append(
            {
                "sku": s,
                "app": ctx.groups[i],
                "that": truth.get(s),
                "age": ctx.history_days(i),
            }
        )
    df = pd.DataFrame(rows)
    old = df[df["age"] >= 28]
    acc = float((old["app"] == old["that"]).mean()) if len(old) else None
    acc_all = float((df["app"] == df["that"]).mean())
    chars = df[df["sku"].map(lambda s: any(s.startswith(k) for k in CHARACTERS))]
    exp = chars["sku"].map(lambda s: next(v for k, v in CHARACTERS.items() if s.startswith(k)))
    char_ok = bool((chars["app"] == exp).all()) and len(chars) > 0
    rep.checks.append(
        Check(
            "03 §6 · Phân nhóm đúng (SKU ≥ 28 ngày)",
            f"{pct(acc)} (tất cả {pct(acc_all)})",
            "≥ 80%",
            acc is not None and acc >= 0.80,
        )
    )
    rep.checks.append(
        Check(
            "03 §6 · Phân nhóm nhân vật demo",
            f"{int((chars['app'] == exp).sum())}/{len(chars)}",
            "100%",
            char_ok,
            ", ".join(f"{a}={b}" for a, b in zip(chars["sku"], chars["app"], strict=True)),
        )
    )
    conf = pd.crosstab(df["that"], df["app"])
    rep.sections.append(
        "## Phân nhóm: đáp án (hàng) × app (cột), mốc 11/11 00:00\n\n" + table(conf.reset_index())
    )


def characters(
    rep: Report, conn: duckdb.DuckDBPyConnection, m: PanelMatrix, hourly: pd.DataFrame
) -> None:
    """Demo characters (03 §3.3, §3.5, §3.6, §9; 05 §10)."""
    # COCMEME phase from 15/10 to 11/11
    phases: dict[str, list[tuple[str, str]]] = {}
    for d in pd.date_range("2026-10-15", "2026-11-11", freq="D"):
        origin = vn_midnight_utc(d.date())
        ctx = build_fit_context(m, origin)
        model = TrendDamped()
        for s in ctx.sku_ids:
            if s.startswith("COCMEME"):
                st = model.fit(None, s, origin, ctx)
                phases.setdefault(s, []).append((str(d.date()), str(st.phase)))
    first_cool: dict[str, str | None] = {}
    later: dict[str, list[str]] = {}
    for s, seq in phases.items():
        # first day of the run of "nguội" that lasts at least until 05/11
        day = None
        for j in range(len(seq)):
            run = [p for d, p in seq[j:] if d <= "2026-11-05"]
            if run and all(p == "nguội" for p in run):
                day = seq[j][0]
                break
        first_cool[s] = day
        later[s] = [f"{d[5:]}={p}" for d, p in seq if d > "2026-11-05" and p != "nguội"]
    ok_cool = bool(first_cool) and all(
        v is not None and v <= "2026-10-28" for v in first_cool.values()
    )
    sample = next(iter(sorted(phases)), None)
    rep.checks.append(
        Check(
            "03 §3.3 · COCMEME-* pha 'nguội' từ khoảng 25/10",
            ", ".join(f"{k}: nguội từ {v}" for k, v in sorted(first_cool.items())),
            "nguội liên tục từ ≤ 28/10 tới 05/11",
            ok_cool,
            "sau 05/11 khác 'nguội': "
            + (", ".join(later.get(sample, [])) or "không" if sample else "—"),
        )
    )
    rep.data["cocmeme_phases"] = phases

    origin = vn_midnight_utc(date(2026, 11, 11))
    fa = service.fit_all(conn, origin, matrix=m, write_groups=False, horizon_hours=24)
    res = service.forecast_skus(conn, origin=origin, horizon_hours=24, fitted=fa, persist=False)
    by = {r.sku_id: r for r in res}
    labels = pd.Series([r.confidence for r in res]).value_counts().to_dict()
    every = len(res) == len(m.sku_ids) and all(r.confidence for r in res)
    rep.checks.append(
        Check(
            "M2 · Mọi SKU có nhãn tin cậy (dự báo 11/11 00:00)",
            f"{len(res)}/{len(m.sku_ids)}: {labels}",
            "100%",
            every,
        )
    )
    rep.data["labels"] = labels
    char_lab = {
        r.sku_id: f"{r.confidence} ({r.model_id.split('@')[0]}, {r.demand_group})"
        for r in res
        if r.sku_id.startswith(tuple(CHARACTERS)) or r.sku_id == "NOICHIEN2L"
    }
    rep.data["character_labels"] = char_lab
    rep.sections.append(
        "## Nhãn của nhân vật demo (dự báo 11/11 00:00, 24 h)\n\n"
        + table(pd.DataFrame(sorted(char_lab.items()), columns=["SKU", "nhãn (model, nhóm)"]))
    )
    may = by.get("MAYHUTMINI")
    rep.checks.append(
        Check(
            "03 §3.5 · MAYHUTMINI nhãn scenario",
            "—" if may is None else f"{may.confidence} ({may.model_id})",
            "scenario",
            may is not None and may.confidence == "scenario",
        )
    )
    noi = by.get("NOICHIEN2L")
    if noi is not None:
        i = m.index["NOICHIEN2L"]
        t0 = m.hour_index(origin)
        stock = float(m.stock_end[i, t0 - 1])
        rep.checks.append(
            Check(
                "05 §10 · NOICHIEN2L dự báo nhu cầu 11.11 vượt tồn đầu ngày",
                f"p50 cả ngày {noi.cum_p50[-1]:.0f} (p10 {noi.cum_p10[-1]:.0f}) vs tồn {stock:.0f}",
                "nhu cầu > tồn",
                noi.cum_p50[-1] > stock,
                f"{noi.model_id}, {noi.confidence}",
            )
        )
    # drift: BINHNUOC500-* on 11.11, hour by hour
    t_end = vn_midnight_utc(date(2026, 11, 12))
    if m.end is not None and pd.Timestamp(m.end) >= pd.Timestamp(t_end):
        b_res = [r for r in res if r.sku_id.startswith("BINHNUOC500")]
        alarms_all = service.scan_drift(m, b_res, t_end)
        lines = []
        oks: list[bool] = []
        in_window: list[bool] = []
        for r in b_res:
            ups = [a for a in alarms_all if a.sku_id == r.sku_id and a.direction == "up"]
            first = ups[0].hour if ups else None
            local = None if first is None else (pd.Timestamp(first) + pd.Timedelta(hours=7)).hour
            # after the alarm hour closes, the next forecast uses nowcast
            switched = None
            if first is not None:
                at = first + timedelta(hours=1)
                active = active_up(ups, at)
                if r.sku_id in active:
                    nf = service.forecast_sku(
                        conn, r.sku_id, at, 24, drift_active=active, matrix=m, persist=False
                    )
                    switched = f"{nf.model_id} ({nf.confidence})"
            # viral start from the answer key: first hour whose true λ is ≥ 2× the forecast
            tl = (
                hourly[
                    (hourly["sku_id"] == r.sku_id) & (hourly["hour_utc"] >= pd.Timestamp(origin))
                ]
                .sort_values("hour_utc")["lambda_co_ads"]
                .to_numpy(float)[: len(r.mean)]
            )
            ratio = tl / np.maximum(np.asarray(r.mean[: len(tl)]), 1e-6)
            hit = np.flatnonzero((ratio >= 2.0) & (tl >= 0.5))
            start = int(hit[0]) if hit.size else None
            lag = None if (local is None or start is None) else local - start
            lines.append(
                f"{r.sku_id}: viral (λ thật ≥ 2× dự báo) từ {start}:00, báo up {local}:00"
                f" (sau {lag} giờ) → {switched}"
            )
            in_window.append(local is not None and 12 <= local <= 15)
            oks.append(
                lag is not None
                and 0 <= lag <= 3
                and switched is not None
                and switched.startswith("nowcast")
            )
        rep.checks.append(
            Check(
                "03 §3.6/§9 · BINHNUOC500-* báo drift up ≤ 3 giờ sau khi viral, chuyển nowcast",
                "; ".join(lines),
                "≤ 3 giờ; plan đoán 12:00–15:00",
                all(oks) if oks else None,
                f"trong khung 12:00–15:00: {sum(in_window)}/{len(in_window)}",
            )
        )
        ao = [r for r in res if r.sku_id == "AOGIUNHIET-M"]
        downs = [a for a in service.scan_drift(m, ao, t_end) if a.direction == "down"]
        rep.checks.append(
            Check(
                "03 §9 · AOGIUNHIET-M không báo down sai ngày 11.11",
                f"{len(downs)} báo down",
                "0",
                len(downs) == 0,
            )
        )


def performance(rep: Report, conn: duckdb.DuckDBPyConnection, m: PanelMatrix) -> None:
    origin = vn_midnight_utc(date(2026, 11, 11))
    t0 = time.perf_counter()
    fa = service.fit_all(conn, origin, matrix=m, write_groups=False)
    t_fit = time.perf_counter() - t0
    jobs = [
        (
            service.get_model(fa.champions[sku].model_id),
            st,
            ForecastRequest(sku_id=sku, origin=origin, horizon_hours=72),
        )
        for sku, st in fa.states.items()
    ]
    t1 = time.perf_counter()
    service.predict_many(jobs)
    t_pred = time.perf_counter() - t1
    rep.checks.append(
        Check(
            f"03 §10 · Hiệu năng fit + predict 72 h ({len(fa.states)} SKU)",
            f"fit {t_fit:.1f} s, predict {t_pred:.1f} s",
            "< 30 s fit, < 2 s predict",
            t_fit < 30 and t_pred < 2,
        )
    )


# ---------------------------------------------------------------------- new measure (M2 §D)
SIM_SAMPLES = 500
TRUE_K = 3.0  # NegBin size of the mock generator (mock-data/config.json demand.dispersion)


def _sim_days(
    lam: np.ndarray, k: np.ndarray, sigma: np.ndarray, rng: np.random.Generator, n: int
) -> np.ndarray:
    """Day totals ``[R, n]`` of 24 hourly NegBin(λ_h·L, k), ``L ~ LogNormal(0, σ)``."""
    out = np.empty((lam.shape[0], n))
    for a in range(0, lam.shape[0], 200):
        b = min(a + 200, lam.shape[0])
        kk = np.maximum(k[a:b], 1e-6)[:, None, None]
        lvl = np.exp(rng.normal(0.0, 1.0, size=(b - a, n, 1)) * sigma[a:b, None, None])
        g = rng.gamma(kk, 1.0 / kk, size=(b - a, n, lam.shape[1]))
        out[a:b] = rng.poisson(lam[a:b, None, :] * lvl * g).sum(axis=2)
    return out


def judge(
    samples: np.ndarray, floor_samples: np.ndarray, y: np.ndarray, rng: np.random.Generator
) -> dict[str, Any]:
    """WAPE of the p50, floor, randomised-PIT and old interval coverage. ``samples`` is the
    predictive ``[R, n]``; ``floor_samples`` the same forecast without level uncertainty."""
    s = samples.T  # [n, R]
    p50 = np.median(s, axis=0)
    fs = floor_samples.T
    fm, fy = floor_stats(fs, np.median(fs, axis=0))
    q10, q90 = np.quantile(s, [0.1, 0.9], axis=0)
    w = m_wape(y, p50)
    fl = wape_floor(fm, fy)
    cov = pit_coverage(randomized_pit(s, y, rng))
    return {
        "n": len(y),
        "wape": w,
        "floor": fl,
        "ratio": None if w is None or not fl else w / fl,
        "cov_pit": cov,
        "cov_old": float(np.mean((y >= q10) & (y <= q90))) if len(y) else None,
        "pass": wape_ok(w, fl) and coverage_ok(cov),
    }


def _ratio(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


def new_measure(rep: Report, m: PanelMatrix, res: BacktestResult, hourly: pd.DataFrame) -> None:
    """Phép thử của cách đo mới: oracle phải đạt, floor của app ≈ WAPE oracle, dự báo kém
    có chủ đích phải trượt (M2 §D)."""
    rng = np.random.default_rng(20261111)
    tday = truth_by_day(hourly)
    cd = attach_truth(champion_days(res), tday)
    cd = cd[cd["full_samples"].notna()].reset_index(drop=True)
    lam_true = (
        hourly.assign(h=hourly["hour_utc"].dt.tz_convert(VN).dt.hour)
        .pivot_table(index=["sku_id", "day"], columns="h", values="lambda_co_ads")
        .reindex(columns=range(24))
        .fillna(0.0)
    )
    assert res.hours is not None
    local = res.hours["hour_utc"] + pd.Timedelta(hours=7)
    hh = res.hours.assign(day=local.dt.date, h=local.dt.hour)
    mean_h = (
        hh.pivot_table(index=["model_id", "backtest", "sku_id", "day"], columns="h", values="mean")
        .reindex(columns=range(24))
        .fillna(0.0)
    )
    key_t = list(zip(cd["sku_id"], cd["day"], strict=True))
    key_m = list(zip(cd["model_id"], cd["backtest"], cd["sku_id"], cd["day"], strict=True))
    lt = lam_true.reindex(key_t).fillna(0.0).to_numpy(float)
    lm = mean_h.reindex(key_m).fillna(0.0).to_numpy(float)
    naive = np.zeros_like(lm)
    for r, (sku, origin) in enumerate(zip(cd["sku_id"], cd["origin"], strict=True)):
        t0 = m.hour_index(origin)
        naive[r] = m.y[m.index[sku], max(0, t0 - 168) : t0].reshape(-1, 24).mean(axis=0)
    y = cd["true"].to_numpy(float)
    k = cd["k"].to_numpy(float)
    sig = cd["sigma"].to_numpy(float)
    n = len(y)
    ch, orc = "champion (app)", "oracle (λ thật, k=3)"
    bad = ["champion × 1,5", "baseline 7 ngày cùng giờ"]
    cands = {
        ch: np.stack(cd["full_samples"].to_list()).astype(float),
        orc: _sim_days(lt, np.full(n, TRUE_K), np.zeros(n), rng, SIM_SAMPLES),
        bad[0]: _sim_days(lm * 1.5, k, sig, rng, SIM_SAMPLES),
        bad[1]: _sim_days(naive, k, np.full(n, 0.1), rng, SIM_SAMPLES),
    }
    zero = np.zeros(n)
    floors = {
        ch: _sim_days(lm, k, zero, rng, SIM_SAMPLES),
        orc: cands[orc],
        bad[0]: _sim_days(lm * 1.5, k, zero, rng, SIM_SAMPLES),
        bad[1]: _sim_days(naive, k, zero, rng, SIM_SAMPLES),
    }
    main, dds = "normal_roll · on_dinh", "dd_1010 · on_dinh + mua_vu"
    scopes = {
        main: (cd["backtest"] == "normal_roll") & (cd["demand_group"] == "on_dinh"),
        "normal_roll · mọi nhóm": cd["backtest"] == "normal_roll",
        dds: (cd["backtest"] == "dd_1010") & cd["demand_group"].isin(["on_dinh", "mua_vu"]),
    }
    rows = []
    tab: dict[tuple[str, str], dict[str, Any]] = {}
    for sname, smask in scopes.items():
        sel = (smask & cd["wape_eligible"].astype(bool)).to_numpy()
        for cname, smp in cands.items():
            j = judge(smp[sel], floors[cname][sel], y[sel], rng)
            tab[(sname, cname)] = j
            rows.append(
                {
                    "phạm vi": sname,
                    "dự báo": cname,
                    "SKU-ngày": j["n"],
                    "WAPE": j["wape"],
                    "WAPE_floor": j["floor"],
                    "WAPE/floor": j["ratio"],
                    "coverage PIT": j["cov_pit"],
                    "coverage cũ [p10,p90]": j["cov_old"],
                    "đạt": "✓" if j["pass"] else "✗",
                }
            )
    rep.sections.append(
        f"## Cách đo mới (M2 §D): WAPE ≤ {1 + WAPE_FLOOR_TOL:.2f} × WAPE_floor và coverage PIT"
        " ∈ [0,72; 0,88]\n\nWAPE_floor = WAPE kỳ vọng của p50 nếu nhu cầu theo đúng phân phối "
        "dự báo của chính dự báo đó (app tự tính, không đọc đáp án). Oracle = λ thật của bộ "
        "sinh với NegBin k = 3. SKU ≥ 1 sp/ngày.\n\n" + table(pd.DataFrame(rows))
    )
    rep.data["new_measure"] = rows
    a, b = tab[(main, orc)], tab[(dds, orc)]
    rep.checks.append(
        Check(
            "M2 §D · (a) oracle đạt cả hai điều kiện (normal_roll on_dinh; 10.10)",
            f"WAPE/floor {_ratio(a['ratio'])}, PIT {pct(a['cov_pit'])};"
            f" 10.10: {_ratio(b['ratio'])}, PIT {pct(b['cov_pit'])}",
            "đạt",
            bool(a["pass"] and b["pass"]),
        )
    )
    fl_n, w_n = tab[(main, ch)]["floor"], a["wape"]
    fl_d, w_d = tab[(dds, ch)]["floor"], b["wape"]
    ok_b = (
        None not in (fl_n, w_n, fl_d, w_d) and abs(fl_n - w_n) <= 0.05 and abs(fl_d - w_d) <= 0.05
    )
    rep.checks.append(
        Check(
            "M2 §D · (b) WAPE_floor app tự tính ≈ WAPE oracle",
            f"normal: {pct(fl_n)} vs {pct(w_n)};  10.10: {pct(fl_d)} vs {pct(w_d)}",
            "|chênh| ≤ 5 điểm",
            bool(ok_b),
            f"chênh normal {pct(None if fl_n is None or w_n is None else fl_n - w_n)},"
            f" 10.10 {pct(None if fl_d is None or w_d is None else fl_d - w_d)}",
        )
    )
    rep.checks.append(
        Check(
            "M2 §D · (c) dự báo kém có chủ đích phải trượt",
            "; ".join(
                f"{c} ({s.split(' ·')[0]}): {'đạt' if tab[(s, c)]['pass'] else 'trượt'}"
                for c in bad
                for s in (main, dds)
            ),
            "trượt",
            all(not tab[(s, c)]["pass"] for c in bad for s in (main, dds)),
        )
    )
    j = tab[(main, ch)]
    rep.checks.append(
        Check(
            "M2 · Champion ngày thường theo cách đo mới (normal_roll, on_dinh)",
            f"WAPE {pct(j['wape'])} / floor {pct(j['floor'])} = {_ratio(j['ratio'])};"
            f" PIT {pct(j['cov_pit'])} (cũ {pct(j['cov_old'])})",
            f"≤ {1 + WAPE_FLOOR_TOL:.2f}; 0,72–0,88",
            bool(j["pass"]),
        )
    )


def render(rep: Report) -> str:
    lines = [
        f"# Chấm dự báo MegaDay Twin · {rep.size} · {rep.started.strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        "Trên dữ liệu mô phỏng (mock-data). Dự báo sinh bởi backtest của app (`app/forecast`), "
        "chấm bằng đáp án `ground_truth`.",
        "",
        "## Tóm tắt",
        "",
        "| Kiểm tra | Kết quả | Ngưỡng | Đạt | Ghi chú |",
        "|---|---|---|---|---|",
    ]
    for c in rep.checks:
        mark = "—" if c.ok is None else ("✓" if c.ok else "✗")
        lines.append(f"| {c.name} | {c.value} | {c.threshold} | {mark} | {c.note} |")
    lines.append("")
    lines += [s + "\n" for s in rep.sections]
    return "\n".join(lines)


def run(size: str, db: str | None = None, out: Path | None = None, quick: bool = False) -> Report:
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    rep = Report(size=size, started=utcnow())
    if not (mock_dir(size) / "ground_truth").exists():
        raise SystemExit(f"Không thấy mock-data/out/{size}/ground_truth")
    t0 = time.perf_counter()
    conn = open_db(size, db)
    m = service.load_matrix(conn)
    hourly, _daily, skus = load_truth(size)
    specs = default_specs()
    if quick:
        specs = [s for s in specs if s.name in ("dd_0808", "dd_0909", "dd_1010", "normal_roll")]
    res = run_backtest(m, specs, hourly=True, keep_samples=True)
    groups = {
        pd.Timestamp(o).to_pydatetime(): res.groups[f"{s.name}@{pd.Timestamp(o).isoformat()}"]
        for s in specs
        for o in s.origins
        if f"{s.name}@{pd.Timestamp(o).isoformat()}" in res.groups
    }
    res_raw = run_backtest(
        m, specs, models=("profile_ewma",), censoring=False, groups_by_origin=groups
    )
    if not db:  # in-memory DB: persist so labels use the scores (03 §7.3)
        from app.forecast.registry import register_models

        register_models(conn)
        write_scores(conn, res.scores, m.panel_version)
    score_backtests(rep, m, res, res_raw, hourly)
    new_measure(rep, m, res, hourly)
    classification(rep, conn, m, skus)
    characters(rep, conn, m, hourly)
    performance(rep, conn, m)
    rep.data["seconds"] = round(time.perf_counter() - t0, 1)
    rep.data["backtest_seconds"] = round(res.seconds, 1)
    rep.data["checks"] = [c.__dict__ for c in rep.checks]
    out_dir = out or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = rep.started.strftime("%Y%m%d-%H%M")
    md = render(rep)
    (out_dir / f"forecast_{size}_{stamp}.md").write_text(md, encoding="utf-8")
    (out_dir / f"forecast_{size}_{stamp}.json").write_text(
        json.dumps(rep.data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(md)
    print(f"\nBáo cáo: {out_dir / f'forecast_{size}_{stamp}.md'} ({rep.data['seconds']} s)")
    return rep


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    p = argparse.ArgumentParser(description="Chấm dự báo bằng ground_truth (03 §10, 11 §2)")
    p.add_argument("--size", default="demo")
    p.add_argument("--db", default=None, help="DuckDB đã ingest (mặc định: nạp mock vào bộ nhớ)")
    p.add_argument("--out", default=None)
    p.add_argument("--quick", action="store_true", help="chỉ dd_* và normal_roll")
    a = p.parse_args(argv)
    rep = run(a.size, a.db, Path(a.out) if a.out else None, a.quick)
    return 0 if all(c.ok is not False for c in rep.checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
