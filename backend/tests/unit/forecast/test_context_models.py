"""FitContext, classification and the six models on ``nho`` (history, shared DB)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from app.forecast.base import ForecastRequest
from app.forecast.context import build_fit_context
from app.forecast.data import PanelMatrix, vn_midnight_utc
from app.forecast.registry import BACKTEST_MODELS, MODELS
from tests.unit.forecast.conftest import ORIGIN_1111

CHARACTERS = {
    "BINHNUOC500": "on_dinh",
    "COCMEME": "trend",
    "AOGIUNHIET": "mua_vu",
    "DENLEDTET": "mua_vu",
    "MAYHUTMINI": "moi",
}


@pytest.fixture(scope="module")
def ctx(nho_matrix: PanelMatrix):  # type: ignore[no-untyped-def]
    return build_fit_context(nho_matrix, ORIGIN_1111)


def test_shop_factors(ctx) -> None:  # type: ignore[no-untyped-def]
    assert ctx.p_shop.sum() == pytest.approx(1.0)
    assert ctx.p_shop[2:5].max() < ctx.p_shop[10:13].min()  # nights are quiet
    assert ctx.weekday.mean() == pytest.approx(1.0)
    assert ctx.n_past_dd == 3  # 8.8, 9.9, 10.10
    assert 2.0 < float(np.median(ctx.e_dd_for("on_dinh"))) < 8.0
    assert 0.5 <= ctx.k_for("on_dinh") <= 50
    assert -3.0 <= ctx.beta_for("on_dinh") <= -0.5
    assert np.allclose(ctx.p_sku.sum(axis=1), 1.0)


def test_demo_characters_classified(ctx) -> None:  # type: ignore[no-untyped-def]
    for i, sku in enumerate(ctx.sku_ids):
        for prefix, group in CHARACTERS.items():
            if sku.startswith(prefix):
                assert ctx.groups[i] == group, (sku, ctx.groups[i], ctx.features[sku])
    i = ctx.idx("DENLEDTET-5M")
    assert ctx.kinds[i] == "tet"
    assert ctx.kinds[ctx.idx("AOGIUNHIET-M")] == "mua_dong"


def test_censoring_raises_demand_of_stockout_days(ctx) -> None:  # type: ignore[no-untyped-def]
    so_days = ctx.stockout_hours > 0
    assert (ctx.demand_adj[so_days] >= ctx.raw_day[so_days] - 1e-9).all()
    assert (ctx.demand_adj[so_days] > ctx.raw_day[so_days]).any()


def _forecast(model: str, ctx, sku: str, hours: int = 72):  # type: ignore[no-untyped-def]
    m = MODELS[model]
    st = m.fit(None, sku, ctx.origin_dt, ctx)
    return m.predict(st, ForecastRequest(sku_id=sku, origin=ctx.origin_dt, horizon_hours=hours))


@pytest.mark.parametrize(
    ("model", "sku"),
    [
        ("profile_ewma", "BINHNUOC500-XANH"),
        ("seasonal_calendar", "AOGIUNHIET-M"),
        ("trend_damped", "COCMEME-A"),
        ("tsb", "COCKHUAY-HONG"),
        ("analog", "MAYHUTMINI"),
        ("nowcast", "BINHNUOC500-XANH"),
    ],
)
def test_each_model_runs_on_a_character(model: str, sku: str, ctx) -> None:  # type: ignore[no-untyped-def]
    r = _forecast(model, ctx, sku)
    assert r.model_id == MODELS[model].model_id
    assert len(r.hours) == len(r.mean) == len(r.p50) == len(r.cum_p90) == 72
    assert r.hours[0] == ORIGIN_1111
    assert all(a <= b <= c for a, b, c in zip(r.p10, r.p50, r.p90, strict=True))
    assert r.cum_p10[-1] <= r.cum_p50[-1] <= r.cum_p90[-1]
    assert sum(r.mean) > 0
    assert r.context == "double_day"  # horizon contains 11.11
    assert r.reasons


def test_double_day_lifts_the_forecast(ctx) -> None:  # type: ignore[no-untyped-def]
    r = _forecast("profile_ewma", ctx, "NOICHIEN2L")
    day1 = sum(r.mean[:24])  # 11.11
    day2 = sum(r.mean[24:48])  # 12.11 (post double day)
    assert day1 > 2.5 * day2


def test_seasonal_reasons(ctx) -> None:  # type: ignore[no-untyped-def]
    tet = _forecast("seasonal_calendar", ctx, "DENLEDTET-5M")
    assert any("Tết" in s for s in tet.reasons)


def test_trend_phase_cooling(nho_matrix: PanelMatrix) -> None:
    """03 §3.3: COCMEME-* in phase 'nguội' from about 25/10."""
    for d in (date(2026, 10, 26), date(2026, 11, 1), date(2026, 11, 11)):
        c = build_fit_context(nho_matrix, vn_midnight_utc(d))
        for sku in ("COCMEME-A", "COCMEME-B", "COCMEME-C"):
            st = MODELS["trend_damped"].fit(None, sku, c.origin_dt, c)
            assert st.phase == "nguội", (d, sku, st.reasons)
    c = build_fit_context(nho_matrix, vn_midnight_utc(date(2026, 10, 12)))
    st = MODELS["trend_damped"].fit(None, "COCMEME-A", c.origin_dt, c)
    assert st.phase == "tăng"


def test_analog_new_item(ctx) -> None:  # type: ignore[no-untyped-def]
    r = _forecast("analog", ctx, "MAYHUTMINI")
    assert r.history_days < 14
    assert r.confidence == "scenario"
    assert any("Hàng mới" in s for s in r.reasons)
    assert r.level_sigma > 0.3


def test_no_future_leak(nho_matrix: PanelMatrix) -> None:
    """03 §7.1: data after origin (NaN / ×10 / random) changes nothing."""
    origin = vn_midnight_utc(date(2026, 10, 9))
    base = build_fit_context(nho_matrix, origin)
    t0 = nho_matrix.hour_index(origin)
    m2 = PanelMatrix(**{**nho_matrix.__dict__})
    m2.y = nho_matrix.y.copy()
    m2.y[:, t0:] = m2.y[:, t0:] * 10 + 3
    m2.stockout = nho_matrix.stockout.copy()
    m2.stockout[:, t0:] = ~m2.stockout[:, t0:]
    m2.disc = nho_matrix.disc.copy()
    m2.disc[:, t0:] = 0.5
    other = build_fit_context(m2, origin)
    m3 = PanelMatrix(**{**nho_matrix.__dict__})
    m3.y = nho_matrix.y.copy()
    m3.y[:, t0:] = np.nan
    third = build_fit_context(m3, origin)
    assert base.groups == other.groups == third.groups
    for name in BACKTEST_MODELS:
        model = MODELS[name]
        for sku in base.sku_ids:
            i = base.idx(sku)
            if base.groups[i] not in model.applicable_groups:
                continue
            req = ForecastRequest(sku_id=sku, origin=origin, horizon_hours=72)
            a = model.mean_forecast(model.fit(None, sku, origin, base), req)
            b = model.mean_forecast(model.fit(None, sku, origin, other), req)
            c = model.mean_forecast(model.fit(None, sku, origin, third), req)
            assert np.allclose(a.mean, b.mean) and np.allclose(a.mean, c.mean), (name, sku)
            assert a.level_sigma == pytest.approx(b.level_sigma)
