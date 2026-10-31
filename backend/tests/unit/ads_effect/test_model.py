"""Grid posterior recovers known (δ, κ, a) on seeded synthetic data (ADS-03/04)."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from app.ads_effect import model as m
from app.ads_effect.dataset import CTX_DOUBLE_DAY, N_CTX, HourRhythm, build_campaign_hours
from app.ads_effect.iroas import estimate_campaign
from tests.unit.ads_effect.synth import B_TRUE, make_panel

RHYTHM = HourRhythm(table=np.tile(B_TRUE, (N_CTX, 1)), source="no_ads_skus", units=1e6)


def _campaigns(n: int = 5, **kw: object) -> list:  # type: ignore[type-arg]
    out = []
    for i in range(n):
        syn = make_panel(100 + i, night_off=i % 2 == 0, campaign=f"C{i}", **kw)  # type: ignore[arg-type]
        ch = build_campaign_hours(syn.frame, f"C{i}", syn.started_at)
        assert ch is not None
        out.append((syn, ch))
    return out


def test_adstock_recursion() -> None:
    np.testing.assert_allclose(m.adstock(np.array([10.0, 0, 0, 5]), 0.5), [10, 5, 2.5, 6.25])


def test_prior_a_shape() -> None:
    p = m.prior_a()
    assert p.shape == (61,) and abs(p.sum() - 1) < 1e-12
    assert m.A_GRID[int(np.argmax(p))] in (0.2, 0.25, 0.3)  # mode of LogNormal(log .4, .8)
    assert p[0] < 1e-3  # a = 0 only takes the mass of [0, 0.025)


def test_closed_form_alpha_matches_brute_force() -> None:
    syn = make_panel(5, days=12)
    ch = build_campaign_hours(syn.frame, "C1", syn.started_at)
    assert ch is not None
    fi = m.fit_input(ch, RHYTHM, 0.6)
    fit = m.grid_loglik(fi)
    i, j = 2, 12  # κ = 1.5, a = 0.6
    g = 1 + m.A_GRID[j] * (1 - np.exp(-fi.adstock / (m.KAPPA_GRID[i] * fi.spend_ref)))
    # full Poisson log-likelihood with α_d at its MLE, minus the cell-free terms
    seg = np.r_[fi.seg_starts, fi.n]
    full = 0.0
    for s, e in itertools.pairwise(seg):
        y, bg = fi.y[s:e], fi.b[s:e] * g[s:e]
        if y.sum() == 0:
            continue
        mu = y.sum() / bg.sum() * bg
        full += float((y * np.log(mu) - mu).sum())
    const = float((fi.y * np.log(fi.b)).sum())
    yd = np.add.reduceat(fi.y, fi.seg_starts)
    yd = yd[yd > 0]
    const += float((yd * np.log(yd) - yd).sum())
    assert fit.ll[i, j] == pytest.approx(full - const, abs=1e-6)


def test_delta_and_parameters_recovered() -> None:
    camps = _campaigns(5)
    delta, scores, _ = m.choose_delta([c for _, c in camps], RHYTHM)
    assert delta == 0.6, scores
    fits = [m.grid_loglik(m.fit_input(c, RHYTHM, delta)) for _, c in camps]
    kp = m.shop_kappa_prior(fits)
    assert abs(kp.sum() - 1) < 1e-9 and kp.min() > 0
    covered = 0
    for (syn, ch), _f in zip(camps, fits, strict=True):
        est = estimate_campaign(ch, RHYTHM, delta, kappa_prior=kp)
        assert est.a_mean == pytest.approx(syn.a, abs=0.15)
        assert 0.75 <= est.kappa_mean <= 3.0  # true κ = 1.5
        assert est.iroas_mean == pytest.approx(syn.true_iroas, rel=0.2)
        covered += est.iroas_p10 <= syn.true_iroas <= est.iroas_p90
        assert est.marginal_iroas is not None and est.marginal_iroas < est.iroas_mean
        assert est.platform_roas == 0.0  # synthetic panel has no attributed revenue
    assert covered >= 4


def test_no_ads_effect_gives_small_a() -> None:
    syn = make_panel(9, a=0.0)
    ch = build_campaign_hours(syn.frame, "C1", syn.started_at)
    assert ch is not None
    est = estimate_campaign(ch, RHYTHM, 0.6)
    assert est.a_mean < 0.12
    small = sum(c.w for c in est.posterior if c.a <= 0.1)
    assert small > 0.5


def test_stock_trap_is_removed_by_the_rules() -> None:
    """04 §4: pausing ads when stock runs out inflates the effect unless those hours go."""
    errs = {}
    for rules in (True, False):
        e = []
        for i in range(3):
            syn = make_panel(200 + i, stock_trap=True, night_off=False, n_episodes=0)
            ch = build_campaign_hours(syn.frame, "C1", syn.started_at, apply_stock_rules=rules)
            assert ch is not None
            est = estimate_campaign(ch, RHYTHM, 0.6)
            e.append(est.a_mean / syn.a - 1)
        errs[rules] = float(np.mean(e))
    assert abs(errs[True]) < 0.25
    assert errs[False] > errs[True] + 0.3  # the confounder inflates a


def test_double_day_context_uses_its_own_rhythm() -> None:
    table = np.tile(B_TRUE, (N_CTX, 1))
    table[CTX_DOUBLE_DAY] = 1.0
    rh = HourRhythm(table=table, source="no_ads_skus", units=1.0)
    assert rh.at(np.array([CTX_DOUBLE_DAY, 0]), np.array([3, 3])).tolist() == [1.0, B_TRUE[3]]


def test_paired_check_agrees_on_clean_data() -> None:
    syn = make_panel(11, night_off=False, n_episodes=0, base_per_day=400)
    ch = build_campaign_hours(syn.frame, "C1", syn.started_at)
    assert ch is not None
    fi = m.fit_input(ch, RHYTHM, 0.6)
    fit = m.grid_loglik(fi)
    bi, bj = fit.best
    chk = m.paired_check(ch, fi, fit, float(m.A_GRID[bj]), m.KAPPA_GRID[bi])
    assert chk.n_off >= m.MIN_CHECK_HOURS
    assert chk.ok and chk.ratio is not None and 0 < chk.ratio < 1


def test_paired_check_flags_disagreement() -> None:
    chk = m.PairedCheck(n_off=50, n_on=500, crude_lift=1.2, model_lift=0.3)
    assert not chk.ok
    assert m.PairedCheck(50, 500, 0.05, 0.12).ok  # small absolute gap
    assert m.PairedCheck(5, 50, None, None).ok  # too few hours: no verdict
