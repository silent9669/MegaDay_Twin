"""IroasEstimate contract: uplift(), sampling, adstock path (04 §7, 05 §5.2–5.3)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from app.ads_effect.iroas import prior_estimate
from app.ads_effect.schemas import IroasEstimate, PosteriorCell

T0 = datetime(2026, 11, 10, 17, tzinfo=UTC)


def _est(cells: list[tuple[float, float, float]], spend_ref: float = 1000.0) -> IroasEstimate:
    return IroasEstimate(
        estimate_id="iroas-x",
        campaign_id="C1",
        period_start=T0,
        period_end=T0,
        delta=0.6,
        spend_ref=spend_ref,
        kappa_mean=1.0,
        a_mean=0.5,
        posterior=[PosteriorCell(kappa=k, a=a, w=w) for k, a, w in cells],
        spend_total=1,
        inc_revenue_mean=1,
        iroas_mean=1.0,
        iroas_p10=0.5,
        iroas_p90=1.5,
        n_hours_used=10,
        n_clean_off_hours=50,
        excluded_hours={},
        confidence="reference",
        adstock_last=500.0,
        panel_version="v",
        created_at=T0,
    )


def test_uplift_single_cell_matches_formula() -> None:
    e = _est([(2.0, 0.5, 1.0)])
    adst = np.array([0.0, 1000.0, 4000.0])
    u = e.uplift(adst)
    np.testing.assert_allclose(u, 0.5 * (1 - np.exp(-adst / 2000.0)))
    # SKU with half the campaign: its own adstock and K are both halved → same uplift
    np.testing.assert_allclose(e.uplift(adst / 2, share=0.5), u)


def test_uplift_expectation_is_posterior_mean() -> None:
    e = _est([(1.0, 0.2, 0.25), (4.0, 1.0, 0.75)])
    adst = np.linspace(0, 5000, 7)
    exp = 0.25 * 0.2 * (1 - np.exp(-adst / 1000)) + 0.75 * 1.0 * (1 - np.exp(-adst / 4000))
    np.testing.assert_allclose(e.uplift(adst), exp)


def test_uplift_draws_shape_and_reproducible() -> None:
    e = _est([(1.0, 0.2, 0.5), (4.0, 1.0, 0.5)])
    adst = np.full(24, 3000.0)
    u1 = e.uplift(adst, draws=1000, rng=np.random.default_rng(7))
    u2 = e.uplift(adst, draws=1000, rng=np.random.default_rng(7))
    assert u1.shape == (1000, 24)
    np.testing.assert_array_equal(u1, u2)  # common random numbers across actions
    # Monte Carlo mean ≈ expectation
    assert abs(u1.mean() - e.uplift(adst).mean()) < 0.03
    # each draw is one of the two cells
    vals = np.unique(np.round(u1[:, 0], 6))
    assert len(vals) == 2


def test_sample_params_then_uplift_at_broadcasts() -> None:
    e = _est([(1.0, 0.2, 0.5), (4.0, 1.0, 0.5)])
    kappa, a = e.sample_params(300, np.random.default_rng(1))
    adst = np.tile(np.linspace(0, 4000, 10), (300, 1))  # per-sample adstock [S, T]
    u = e.uplift_at(adst, kappa[:, None], a[:, None])
    assert u.shape == (300, 10)
    assert np.all(u >= 0) and np.all(u <= 1.0)
    with pytest.raises(ValueError):
        e.uplift_at(adst, kappa[:, None], a[:, None], share=0.0)


def test_adstock_path_uses_last_state() -> None:
    e = _est([(1.0, 0.5, 1.0)])
    path = e.adstock_path(np.array([100.0, 0.0, 0.0]))
    np.testing.assert_allclose(path, [100 + 0.6 * 500, 0.6 * (100 + 300), 0.36 * 400])
    np.testing.assert_allclose(e.adstock_path(np.array([100.0]), share=0.5, start=0.0), [50.0])
    two = e.adstock_path(np.ones((3, 4)))  # last axis = time
    assert two.shape == (3, 4)


def test_prior_estimate_is_scenario_and_usable() -> None:
    e = prior_estimate("C9", spend_ref=5000.0)
    assert e.confidence == "scenario"
    _, _, w = e.grid_arrays()
    assert abs(w.sum() - 1) < 1e-9
    assert 0.3 < e.a_mean < 0.8  # LogNormal(log 0.4, 0.8) cut to [0, 3]
    assert e.uplift(np.array([1e9]))[0] == pytest.approx(e.a_mean, rel=0.03)
