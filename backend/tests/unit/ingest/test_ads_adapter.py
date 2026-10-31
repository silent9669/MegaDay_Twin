"""ING-03 · ads adapter + timezone check + ADS_GAP_DAY / ADS_LAG."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ingest.adapters import AdsAdapter, NormFrame
from app.ingest.timezone_check import detect_offset_hours, orders_hour_profile
from tests.unit.ingest import adapter_data as d

HEADER = (
    "Thời gian,Mã chiến dịch,Tên chiến dịch,Trạng thái,Ngân sách ngày,Chi phí,"
    "Lượt hiển thị,Lượt click,Đơn hàng,Doanh thu\n"
)
# VN hour-of-day rhythm: low at night, peaks at noon and evening
RHYTHM = np.array(
    [2, 1, 1, 1, 1, 2, 4, 8, 12, 14, 15, 18, 17, 15, 13, 12, 13, 14, 16, 18, 19, 20, 16, 8],
    dtype=float,
)


def _synthetic_ads(tmp_path: Path, *, utc: bool, skip_local_day: str | None = None) -> Path:
    lines = []
    start = datetime(2026, 8, 12)
    for day in range(10):
        for h in range(24):
            local = start + timedelta(days=day, hours=h)
            if skip_local_day and local.strftime("%Y-%m-%d") == skip_local_day:
                continue
            shown = local - timedelta(hours=7) if utc else local
            for cid, night_pause in (("CD1", False), ("CD2", True)):
                budget = 100_000 + 1_000 * day
                if night_pause and h < 6:
                    status, spend = "Tạm dừng", 0
                elif h >= 21:
                    status, spend = "Hết ngân sách", 0
                else:
                    status, spend = "Đang chạy", int(RHYTHM[h] * 300)
                lines.append(
                    f"{shown:%Y-%m-%d %H:%M},{cid},Chiến dịch {cid},{status},{budget},{spend},"
                    f"{spend // 10},{spend // 100},0,0"
                )
    src = tmp_path / "ads.csv"
    src.write_text("﻿" + HEADER + "\n".join(lines) + "\n", encoding="utf-8")
    return src


def _normalize(src: Path, **ctx_kw: object) -> NormFrame:
    a = AdsAdapter()
    return a.normalize(a.read(src), d.ctx(**ctx_kw))  # type: ignore[arg-type]


PROFILE = [float(x) for x in RHYTHM * 10]


def test_utc_file_detected_plus_seven(tmp_path: Path) -> None:
    norm = _normalize(_synthetic_ads(tmp_path, utc=True), orders_hour_profile=PROFILE)
    assert norm.tz_offset_hours == 7
    issue = d.codes(norm, "ADS_TZ_SHIFT")[0]
    assert issue.detail["suggestion"] == "+7h"
    assert issue.detail["accepted"] and issue.detail["needs_user_confirmation"]
    t = norm.tables["ad_hourly"]
    # file "2026-08-11 17:00" (UTC) = 00:00 VN 12/08 = 2026-08-11T17:00Z
    assert t["hour_utc"].min() == pd.Timestamp("2026-08-11 17:00", tz="UTC")


def test_local_file_has_no_shift(tmp_path: Path) -> None:
    norm = _normalize(_synthetic_ads(tmp_path, utc=False), orders_hour_profile=PROFILE)
    assert norm.tz_offset_hours == 0
    assert "ADS_TZ_SHIFT" not in norm.issue_codes()
    assert norm.tables["ad_hourly"]["hour_utc"].min() == pd.Timestamp("2026-08-11 17:00", tz="UTC")


def test_confirmed_offset_from_context(tmp_path: Path) -> None:
    norm = _normalize(_synthetic_ads(tmp_path, utc=True), tz_offsets={"ads": 7})
    assert norm.tz_offset_hours == 7 and "ADS_TZ_SHIFT" not in norm.issue_codes()


def test_gap_day_filled_unknown(tmp_path: Path) -> None:
    src = _synthetic_ads(tmp_path, utc=True, skip_local_day="2026-08-15")
    norm = _normalize(src, tz_offsets={"ads": 7})
    gap = d.codes(norm, "ADS_GAP_DAY")
    assert len(gap) == 1 and gap[0].detail["local_dates"] == ["2026-08-15"]
    filled = norm.tables["ad_hourly"].query("src_fill == 'gap'")
    assert len(filled) == 2 * 24
    assert (filled["status"] == "unknown").all() and filled["spend"].isna().all()


def test_status_mapping_and_attributed_columns(tmp_path: Path) -> None:
    norm = _normalize(_synthetic_ads(tmp_path, utc=True), tz_offsets={"ads": 7})
    t = norm.schema_frame("ad_hourly")
    assert set(t["status"]) == {"running", "paused", "budget_exhausted"}
    assert "attributed_orders" in t.columns and "attributed_revenue" in t.columns
    assert not any(c.startswith("src_") for c in t.columns)


def test_detect_offset_rhythm_basic() -> None:
    spend = np.roll(RHYTHM, -7)  # file written 7 h behind
    best, corr, _ = detect_offset_hours(RHYTHM, spend)
    assert best == 7 and corr > 0.99


# ------------------------------------------------------------ real mock data
@d.needs_nho
@pytest.mark.parametrize("size", ["nho", "demo"])
def test_mock_ads_offset_gap_lag(size: str) -> None:
    if size == "demo" and not d.data_dir("demo").is_dir():
        pytest.skip("demo missing")
    norm = d.ads(size)
    assert norm.tz_offset_hours == 7  # 02 §8: offset = +7
    tz = d.codes(norm, "ADS_TZ_SHIFT")[0].detail
    assert tz["method"] == "day_boundary" and tz["accepted"]

    gap = d.codes(norm, "ADS_GAP_DAY")
    assert [g.entity_id for g in gap] == ["2026-09-20"]
    assert gap[0].detail["start_utc"] == "2026-09-19T17:00:00+00:00"
    assert gap[0].detail["end_utc"] == "2026-09-20T17:00:00+00:00"
    t = norm.tables["ad_hourly"]
    assert (t["src_fill"] == "gap").sum() == d.planted(size, "thieu_ngay_ads")["so_dong_thieu"]

    lag = d.codes(norm, "ADS_LAG")[0]
    assert lag.detail["lag_hours"] == 3
    assert (t["src_fill"] == "lag").sum() == d.planted(size, "do_tre_ads")["so_dong_bi_giu"]
    lag_rows = t[t["src_fill"] == "lag"]
    assert (lag_rows["status"] == "unknown").all() and lag_rows["spend"].isna().all()
    # VN 21:00–23:00 on 10/11
    assert lag_rows["hour_utc"].min() == pd.Timestamp("2026-11-10 14:00", tz="UTC")
    assert not t.duplicated(["campaign_id", "hour_utc"]).any()


@d.needs_nho
def test_ads_hours_align_with_orders_after_shift() -> None:
    """After +7 h the ads day rhythm lines up with orders (ING-03 acceptance)."""
    o = d.orders()
    t = d.ads().tables["ad_hourly"]
    running = t[t["status"] == "running"]
    local_hour = running["hour_utc"].dt.tz_convert("Asia/Ho_Chi_Minh").dt.hour
    spend = running["spend"].astype(float).groupby(local_hour).mean().reindex(range(24))
    best, _, _ = detect_offset_hours(
        orders_hour_profile(o.tables["orders"]["ordered_at"]), spend.fillna(0).to_numpy()
    )
    assert abs(best) <= 2  # residual bias: budgets run out in the evening


@d.needs_nho
def test_live_ads_with_confirmed_offset() -> None:
    a = AdsAdapter()
    norm = a.normalize(
        a.read(d.data_dir() / "live_11_11" / "bao_cao_ads.csv"), d.ctx(tz_offsets={"ads": 7})
    )
    t = norm.tables["ad_hourly"]
    # first live row "2026-11-10 17:00" (UTC) = 00:00 VN on 11/11
    assert t["hour_utc"].min() == pd.Timestamp("2026-11-10 17:00", tz="UTC")
    assert len(t) == d.manifest()["so_dong"]["liveAds"]
