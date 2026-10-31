"""Ads lag of the 11.11 replay from the mock files (M5, docs/quyet-dinh/M5-chuan-bi-demo.md).

Design (06 §4, ``_replay.json`` ``adsLagHours``): after replay hour H the orders are up to
H:59 (``as_of`` H+1:00) and ads up to (H - 2):59, so ``GET /freshness`` shows ads 2 h behind,
under the 3 h threshold. The ads file is UTC (planted error, +7 h); ``as_of`` of an ads batch is
the end of its last hour (``AdsAdapter``: last ``hour_utc`` + 1 h).

Known gap: the history holds back its last 3 ads hours (planted ``do_tre_ads``, 21:00-23:59 on
10/11) and ``live_11_11`` starts at 11/11 00:00, so those hours are never delivered. Until the
live ads catch up (H = 0, 1) ads ``as_of`` stays at 21:00 on 10/11: lag 4 h / 5 h, ``stale``.
From H = 2 the lag is the designed 2 h. If the mock generator starts delivering the held-back
hours in the live stream (proposal in the M5 decision log), ``KNOWN_GAP`` becomes empty.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta

import pytest

from tests.mockdata import require

ADS_FILE_OFFSET = timedelta(hours=7)  # ads "Thời gian" is UTC (planted error)
THRESHOLD = timedelta(hours=3)  # app.api.common.STALE_MINUTES["ads"]
KNOWN_GAP = {0: timedelta(hours=4), 1: timedelta(hours=5)}


def _vn(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp) + ADS_FILE_OFFSET  # naive VN wall clock


def _ads_lags(size: str) -> dict[int, timedelta]:
    base = require(size)
    with (base / "bao_cao_ads.csv").open(encoding="utf-8-sig", newline="") as f:
        history_as_of = max(_vn(r["Thời gian"]) for r in csv.DictReader(f)) + timedelta(hours=1)
    replay = json.loads((base / "live_11_11" / "_replay.json").read_text(encoding="utf-8"))
    day = datetime.fromisoformat(replay["ngay"])
    lag_hours = int(replay["adsLagHours"])
    lags: dict[int, timedelta] = {}
    for hour in range(24):
        orders_as_of = day + timedelta(hours=hour + 1)
        live = [
            _vn(line.split(",", 1)[0]) for h, line in replay["rows"]["ads"] if h <= hour - lag_hours
        ]
        ads_as_of = max([history_as_of, *(t + timedelta(hours=1) for t in live)])
        lags[hour] = orders_as_of - ads_as_of
    return lags


@pytest.mark.parametrize("size", ["nho", "demo"])
def test_replay_ads_lag_is_two_hours_once_live_ads_arrive(size: str) -> None:
    lags = _ads_lags(size)
    for hour, lag in lags.items():
        if hour in KNOWN_GAP:
            continue
        assert lag == timedelta(hours=2), (hour, lag)
        assert lag <= THRESHOLD


@pytest.mark.parametrize("size", ["nho", "demo"])
def test_replay_opening_hours_show_the_history_ads_gap(size: str) -> None:
    """01:00 and 02:00 on 11.11: ads still end at 20:59 on 10/11 (held-back history hours)."""
    lags = _ads_lags(size)
    for hour, expected in KNOWN_GAP.items():
        assert lags[hour] == expected, (hour, lags[hour])
        assert lags[hour] > THRESHOLD  # -> "Dữ liệu Ads cũ hơn ngưỡng" banner, truthful
