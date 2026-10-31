"""Demo characters on ``demo`` (300 SKUs, 11.11 included). Marker ``e2e``."""

from __future__ import annotations

import time
from datetime import timedelta

import pandas as pd
import pytest

from app.forecast import service
from app.forecast.base import ForecastRequest
from app.forecast.drift import active_up
from tests.unit.forecast.conftest import ORIGIN_1111, Live

pytestmark = pytest.mark.e2e

CHARACTERS = {
    "BINHNUOC500": "on_dinh",
    "COCMEME": "trend",
    "AOGIUNHIET": "mua_vu",
    "DENLEDTET": "mua_vu",
    "MAYHUTMINI": "moi",
}


def test_demo_characters(demo_live: Live) -> None:
    conn, m = demo_live.conn, demo_live.matrix
    t0 = time.perf_counter()
    fa = service.fit_all(conn, ORIGIN_1111, matrix=m, horizon_hours=24)
    fit_s = time.perf_counter() - t0
    ctx = fa.ctx
    for sku in ctx.sku_ids:
        for prefix, group in CHARACTERS.items():
            if sku.startswith(prefix):
                assert ctx.groups[ctx.idx(sku)] == group, sku
    jobs = [
        (
            service.get_model(fa.champions[sku].model_id),
            st,
            ForecastRequest(sku_id=sku, origin=ORIGIN_1111, horizon_hours=72),
        )
        for sku, st in fa.states.items()
    ]
    t1 = time.perf_counter()
    service.predict_many(jobs)
    pred_s = time.perf_counter() - t1
    # 03 §10 (< 30 s fit, < 2 s predict) is measured by eval_forecast on an idle machine;
    # here only a sanity bound, the test runner shares the CPU.
    assert fit_s < 30 and pred_s < 10

    res = service.forecast_skus(
        conn, origin=ORIGIN_1111, horizon_hours=24, fitted=fa, persist=False
    )
    by = {r.sku_id: r for r in res}
    assert len(res) == len(m.sku_ids)
    assert by["MAYHUTMINI"].confidence == "scenario"
    stock = m.stock_end[m.index["NOICHIEN2L"], m.hour_index(ORIGIN_1111) - 1]
    assert by["NOICHIEN2L"].cum_p50[-1] > stock

    end = ORIGIN_1111 + timedelta(hours=24)
    b = [r for r in res if r.sku_id.startswith("BINHNUOC500")]
    alarms = service.scan_drift(m, b, end)
    for r in b:
        ups = [a for a in alarms if a.sku_id == r.sku_id and a.direction == "up"]
        assert ups, r.sku_id
        h = (pd.Timestamp(ups[0].hour) + pd.Timedelta(hours=7)).hour
        # product shock detector: ≤ 3 h after the viral start (7–8h), M2 "Hoàn thiện M2"
        assert 7 <= h <= 10, (r.sku_id, h)
        at = ups[0].hour + timedelta(hours=1)
        nf = service.forecast_sku(
            conn, r.sku_id, at, 24, drift_active=active_up(ups, at), matrix=m, persist=False
        )
        assert nf.model_id.startswith("nowcast")
    ao = [r for r in res if r.sku_id == "AOGIUNHIET-M"]
    assert not [a for a in service.scan_drift(m, ao, end) if a.direction == "down"]
