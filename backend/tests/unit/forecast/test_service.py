"""Backtest → model_scores → champion → labelled forecasts → drift on ``nho`` with 11.11."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from app.forecast import service
from app.forecast.drift import active_up
from app.forecast.registry import MODELS, latest_rows
from tests.unit.forecast.conftest import ORIGIN_1111, Live


def test_backtest_writes_registry_and_scores(nho_scored: Live) -> None:
    conn = nho_scored.conn
    reg = conn.execute("SELECT model_id, code_hash FROM model_registry").fetchall()
    assert {r[0] for r in reg} == {m.model_id for m in MODELS.values()}
    rows = latest_rows(conn)
    assert rows
    names = {r.backtest for r in rows}
    assert {"dd_0909", "dd_1010", "normal_roll", "pooled_double_day", "pooled_normal"} <= names
    # exactly one champion per (group, context, backtest)
    df = pd.DataFrame([r.as_dict() for r in rows])
    champ = df[df["is_champion"]].groupby(["demand_group", "context", "backtest"]).size()
    assert (champ == 1).all()
    # every champion is applicable to its group
    for r in rows:
        if r.is_champion:
            assert r.demand_group in MODELS[r.model_id.split("@")[0]].applicable_groups


@pytest.fixture(scope="module")
def forecasts(nho_scored: Live):  # type: ignore[no-untyped-def]
    return service.forecast_skus(
        nho_scored.conn, origin=ORIGIN_1111, horizon_hours=24, matrix=nho_scored.matrix
    )


def test_every_sku_labelled_and_persisted(nho_scored: Live, forecasts) -> None:  # type: ignore[no-untyped-def]
    assert len(forecasts) == len(nho_scored.matrix.sku_ids)
    assert {r.confidence for r in forecasts} <= {"verified", "reference", "scenario"}
    n = nho_scored.conn.execute(
        "SELECT count(*) FROM forecasts WHERE origin = ?", [ORIGIN_1111]
    ).fetchone()
    assert n is not None and n[0] >= len(forecasts)
    payload = nho_scored.conn.execute("SELECT payload FROM forecasts LIMIT 1").fetchone()
    assert payload is not None and '"cum_p90"' in payload[0]
    groups = dict(nho_scored.conn.execute("SELECT sku_id, demand_group FROM skus").fetchall())
    assert groups["COCMEME-A"] == "trend" and groups["MAYHUTMINI"] == "moi"


def test_mayhutmini_is_scenario(forecasts) -> None:  # type: ignore[no-untyped-def]
    may = next(r for r in forecasts if r.sku_id == "MAYHUTMINI")
    assert may.confidence == "scenario"


def test_noichien_demand_exceeds_stock(nho_scored: Live, forecasts) -> None:  # type: ignore[no-untyped-def]
    m = nho_scored.matrix
    noi = next(r for r in forecasts if r.sku_id == "NOICHIEN2L")
    stock = m.stock_end[m.index["NOICHIEN2L"], m.hour_index(ORIGIN_1111) - 1]
    assert noi.cum_p50[-1] > stock
    assert any("giá" in s.lower() for s in noi.reasons)  # committed 11.11 price applied


def test_binhnuoc_drift_up_then_nowcast(nho_scored: Live, forecasts) -> None:  # type: ignore[no-untyped-def]
    m = nho_scored.matrix
    end = ORIGIN_1111 + timedelta(hours=24)
    b = [r for r in forecasts if r.sku_id.startswith("BINHNUOC500")]
    alarms = service.scan_drift(m, b, end)
    for r in b:
        ups = [a for a in alarms if a.sku_id == r.sku_id and a.direction == "up"]
        assert ups, r.sku_id
        local_hour = (pd.Timestamp(ups[0].hour) + pd.Timedelta(hours=7)).hour
        # product shock detector (M2 "Hoàn thiện M2"): 10:00 for all three on ``nho``;
        # the per-SKU CUSUM alone found XANH only at 15:00
        assert 8 <= local_hour <= 11, (r.sku_id, local_hour)
        at = ups[0].hour + timedelta(hours=1)
        nf = service.forecast_sku(
            nho_scored.conn,
            r.sku_id,
            at,
            24,
            drift_active=active_up(ups, at),
            matrix=m,
            persist=False,
        )
        assert nf.model_id.startswith("nowcast")
        assert nf.confidence in ("reference", "scenario")
        assert any("lệch mạnh" in s for s in nf.reasons)
        # nowcast is above the morning forecast at once, and far above it after two more
        # hours of the shock (it is re-fitted every hour while drift is up)
        assert np.mean(nf.mean[:6]) > 1.2 * np.mean(r.mean[local_hour + 1 : local_hour + 7])
        at2 = at + timedelta(hours=1)
        nf2 = service.forecast_sku(
            nho_scored.conn, r.sku_id, at2, 24, drift_active={r.sku_id}, matrix=m, persist=False
        )
        assert np.mean(nf2.mean[:6]) > 1.5 * np.mean(r.mean[local_hour + 2 : local_hour + 8])


def test_aogiunhiet_m_no_false_down(nho_scored: Live, forecasts) -> None:  # type: ignore[no-untyped-def]
    ao = [r for r in forecasts if r.sku_id == "AOGIUNHIET-M"]
    alarms = service.scan_drift(nho_scored.matrix, ao, ORIGIN_1111 + timedelta(hours=24))
    assert not [a for a in alarms if a.direction == "down"]


def test_user_group_override_survives_refit(nho_live: Live) -> None:
    conn = nho_live.conn
    service.set_demand_group(conn, "GIAYLOT50", "ban_thua")
    ctx = service.build_context(conn, ORIGIN_1111, matrix=nho_live.matrix)
    assert ctx.groups[ctx.idx("GIAYLOT50")] == "ban_thua"
    service.write_demand_groups(conn, ctx)
    row = conn.execute(
        "SELECT demand_group, demand_group_source FROM skus WHERE sku_id = 'GIAYLOT50'"
    ).fetchone()
    assert row == ("ban_thua", "user")
    service.set_demand_group(conn, "GIAYLOT50", None)
    ctx = service.build_context(conn, ORIGIN_1111, matrix=nho_live.matrix)
    assert ctx.groups[ctx.idx("GIAYLOT50")] == "on_dinh"
    with pytest.raises(ValueError):
        service.set_demand_group(conn, "GIAYLOT50", "xyz")
