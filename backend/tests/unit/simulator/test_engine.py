"""SIM-02 · Monte Carlo with common random numbers, demand under actions."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from scipy import stats

from app.simulator.engine import Perturb, poisson_inv, simulate
from app.simulator.schemas import ActionSpec, PriorIroas, as_posterior, prior_weights
from tests.unit.simulator.factory import make_inputs, sku

CONT = ActionSpec(kind="continue")
PAUSE = ActionSpec(kind="pause")
HALF = ActionSpec(kind="reduce_budget", pct=0.5)


def test_poisson_inverse_cdf() -> None:
    rng = np.random.default_rng(3)
    u = rng.random(200_000)
    for lam in (0.0, 0.3, 2.5, 12.0, 80.0):
        x = poisson_inv(u, np.full(u.shape, lam))
        assert x.mean() == pytest.approx(lam, abs=0.03 + 0.01 * lam)
        assert x.var() == pytest.approx(lam, abs=0.05 + 0.03 * lam)
    small = np.array([0.1, 0.5, 0.9, 0.999])
    assert poisson_inv(small, np.full(4, 3.0)).tolist() == stats.poisson.ppf(small, 3.0).tolist()
    # monotone in λ for the same uniform (coupling behind common random numbers)
    lam = np.linspace(0, 60, 500)
    uu = np.full(500, 0.37)
    assert (np.diff(poisson_inv(uu, lam)) >= 0).all()


def test_prior_weights() -> None:
    _k, a, w = prior_weights()
    assert len(w) == 6 * 61 and w.sum() == pytest.approx(1.0)
    assert np.sum(w * a) == pytest.approx(0.55, abs=0.1)  # LogNormal(log 0,4; 0,8) mean ≈ 0,55
    up = PriorIroas(campaign_id="X", spend_scale=1e5).uplift(np.array([0.0, 1e5, 1e7]))
    assert up[0] == 0 and up[1] < up[2] < 3


def test_same_seed_identical() -> None:
    inp = make_inputs([sku("A", 24, lam=3.0, stock=40)])
    r1 = simulate(inp, [CONT, PAUSE, HALF], 500, seed=11)
    r2 = simulate(inp, [CONT, PAUSE, HALF], 500, seed=11)
    for a, b in zip(r1.runs, r2.runs, strict=True):
        assert np.array_equal(a.j, b.j) and np.array_equal(a.units_sold, b.units_sold)
    # adding an action does not change the others (common random numbers)
    r3 = simulate(inp, [PAUSE, CONT], 500, seed=11)
    assert np.array_equal(r3.by_key()["continue"].j, r1.by_key()["continue"].j)


def test_common_random_numbers_coupling() -> None:
    """More ads never sells fewer units in the same sample when stock is ample."""
    inp = make_inputs([sku("A", 24, lam=3.0, stock=10_000)])
    by = simulate(inp, [CONT, HALF, PAUSE], 1000, seed=5).by_key()
    assert (by["continue"].units_sold >= by["reduce_budget:0.5"].units_sold).all()
    assert (by["reduce_budget:0.5"].units_sold >= by["pause"].units_sold).all()
    assert by["pause"].spend.max() == 0


def test_p_stable_across_seeds() -> None:
    inp = make_inputs([sku("A", 24, lam=1.5, m=40_000, stock=10_000)], budget=600_000)
    ps = []
    for seed in (1, 2, 3, 4):
        by = simulate(inp, [CONT, PAUSE], 1000, seed=seed).by_key()
        ps.append(float(np.mean(by["pause"].j > by["continue"].j)))
    assert max(ps) - min(ps) <= 0.04  # ±0,02 around the mean


def test_demand_capped_by_stock_and_contribution() -> None:
    T = 23  # noqa: N806
    inp = make_inputs([sku("N", T, lam=6.0, m=9_280, stock=76, v=0)], T=T, budget=5_592_000)
    by = simulate(inp, [CONT, PAUSE], 1000, seed=1).by_key()
    c, p = by["continue"], by["pause"]
    assert c.units_sold.max() <= 76 and p.units_sold.max() <= 76
    assert np.median(c.units_sold) == 76  # demand ~138 ≫ stock
    assert np.allclose(c.contribution, 9_280 * c.units_sold)
    assert np.allclose(c.j, c.contribution - c.spend)
    assert c.spend[0] == pytest.approx(5_592_000 * 23 / 24)


def test_leftover_value_and_penalty() -> None:
    inp = make_inputs([sku("A", 24, lam=1.0, stock=100, v=5_000)])
    inp.penalty = 0.0
    r = simulate(inp, [PAUSE], 300, seed=1).runs[0]
    assert np.allclose(r.leftover_value, 5_000 * r.leftover_units)
    assert np.allclose(r.j, r.contribution + r.leftover_value)


def test_combo_shares_component_stock() -> None:
    T = 24  # noqa: N806
    noi = sku("NOI", T, lam=4.0, stock=30, m=9_280)
    giay = sku("GIAY", T, lam=1.0, stock=300, m=18_600)
    combo = sku("COMBO", T, lam=1.0, m=29_120, bundle=[("NOI", 1), ("GIAY", 1)])
    inp = make_inputs([noi, giay, combo], T=T)
    r = simulate(inp, [CONT], 500, seed=2).runs[0]
    noi_used = r.sold_by_sku["NOI"] + r.sold_by_sku["COMBO"]
    assert noi_used.max() <= 30
    assert np.allclose(
        r.leftover_by_sku["GIAY"], 300 - r.sold_by_sku["GIAY"] - r.sold_by_sku["COMBO"]
    )


def test_restock_and_pause_until_restock() -> None:
    T = 48  # noqa: N806
    plans = [(date(2026, 11, 12), 100)]  # arrives 12/11 10:00 VN = horizon hour 33
    inp = make_inputs([sku("A", T, lam=2.0, stock=5, plans=plans)], T=T)
    assert inp.restock_t() == 33
    by = simulate(inp, [CONT, ActionSpec(kind="pause_until_restock")], 300, seed=1).by_key()
    pur = by["pause_until_restock"]
    assert pur.spend_hourly[:33].sum() == 0 and pur.spend_hourly[33:].sum() > 0
    assert by["continue"].leftover_units.mean() > 50  # restock counted
    late = simulate(inp, [CONT], 300, seed=1, perturb=Perturb(restock_delay=2)).runs[0]
    assert late.leftover_units.mean() < 10  # arrives after the horizon


def test_perturb_level_and_ads() -> None:
    inp = make_inputs([sku("A", 24, lam=2.0, stock=10_000)])
    base = simulate(inp, [CONT], 500, seed=1).runs[0]
    low = simulate(inp, [CONT], 500, seed=1, perturb=Perturb(level=0.5)).runs[0]
    assert low.units_sold.mean() < 0.6 * base.units_sold.mean()
    by0 = simulate(inp, [CONT, PAUSE], 500, seed=1, perturb=Perturb(ads=0.0)).by_key()
    assert np.array_equal(by0["continue"].units_sold, by0["pause"].units_sold)


def test_flash_sale_blocks_pause() -> None:
    inp = make_inputs([sku("A", 23, lam=5.0, stock=50)], T=23, must_run_hours=[0, 1, 12, 13])
    by = simulate(
        inp,
        [PAUSE, ActionSpec(kind="schedule_hours", windows_local=[(0, 2), (12, 14)])],
        200,
    ).by_key()
    assert not by["pause"].feasible and by["pause"].violations[0][0] == "campaign_must_run"
    assert by["schedule_hours:0-2,12-14"].feasible


def test_real_estimate_adapter() -> None:
    """``IroasEstimate`` of app.ads_effect plugs in through ``as_posterior``."""
    from app.ads_effect.iroas import prior_estimate

    est = prior_estimate("CDX", 100_000.0)
    post = as_posterior(est)
    assert post.estimate_id == est.estimate_id and post.spend_scale == 100_000.0
    inp = make_inputs([sku("A", 24, lam=2.0, stock=10_000)])
    inp.iroas = post
    by = simulate(inp, [CONT, PAUSE], 300, seed=1).by_key()
    assert (by["continue"].units_sold >= by["pause"].units_sold).all()
