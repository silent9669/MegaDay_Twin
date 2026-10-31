"""Adstock + saturation model with a posterior on a grid (ADS-03, plan 04 §5).

Per campaign ``c``, hour ``t`` (kept hours only)::

    Y_t ~ quasi-Poisson(μ_t)
    log μ_t = α_{d(t)} + log B_{ctx(t), h(t)} + log g_t
    g_t = 1 + a · (1 − exp(−A_t / K)),   K = κ · spend_ref,   A_t = spend_t + δ · A_{t−1}

``α_d`` (one level per local day) has the closed-form Poisson MLE
``exp(α_d) = Σ_d Y / Σ_d B·g``, so the profile log-likelihood of a grid cell is
``Σ_t Y_t log g_t − Σ_d Y_d log Σ_{t∈d} B_t g_t`` (+ terms that do not depend on the cell).

Step 1: ``δ`` shared by the shop = argmax of Σ_c max_{κ,a} ll_c / φ_c.
Step 2: per campaign at ``δ̂``: posterior(κ, a) ∝ exp(ll / φ) · prior,
``φ`` = Pearson χ² / df of the best cell, floored at 1 (quasi-likelihood).

Everything is vectorised over the ``a`` axis (61 values) with numpy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from app.ads_effect.dataset import CampaignHours, HourRhythm

FloatArray = npt.NDArray[np.float64]

DELTA_GRID: tuple[float, ...] = (0.4, 0.5, 0.6, 0.7, 0.8)
KAPPA_GRID: tuple[float, ...] = (0.5, 1.0, 1.5, 2.5, 4.0, 6.0)
A_GRID: FloatArray = np.round(np.arange(61) * 0.05, 2)
PRIOR_A_MEDIAN = 0.4
PRIOR_A_SIGMA = 0.8
MIN_POST_WEIGHT = 1e-4  # cells kept in iroas_estimates.posterior (04 §7)


# ---------------------------------------------------------------------- building blocks
def adstock(spend: FloatArray, delta: float) -> FloatArray:
    """``A_t = spend_t + δ · A_{t−1}``, ``A_{−1} = 0``."""
    out = np.empty_like(spend, dtype=np.float64)
    prev = 0.0
    for t, s in enumerate(spend.tolist()):
        prev = s + delta * prev
        out[t] = prev
    return out


def _lognorm_cdf(x: float, mu: float, sigma: float) -> float:
    from math import erf, log, sqrt

    if x <= 0:
        return 0.0
    return 0.5 * (1.0 + erf((log(x) - mu) / (sigma * sqrt(2.0))))


def prior_a(a_grid: FloatArray = A_GRID) -> FloatArray:
    """``a ~ LogNormal(log 0.4, 0.8)`` cut to ``[0, 3]`` and discretised on ``a_grid``: each
    cell takes the mass of its half-step neighbourhood (``a=0`` takes ``[0, 0.025)``)."""
    mu, sg = float(np.log(PRIOR_A_MEDIAN)), PRIOR_A_SIGMA
    edges = np.concatenate(([0.0], (a_grid[:-1] + a_grid[1:]) / 2, [float(a_grid[-1])]))
    cdf = np.array([_lognorm_cdf(float(e), mu, sg) for e in edges])
    mass = np.maximum(np.diff(cdf), 1e-12)
    out: FloatArray = mass / mass.sum()
    return out


def prior_grid() -> FloatArray:
    """Prior weights ``[κ, a]`` (κ uniform)."""
    pa = prior_a()
    p = np.tile(pa, (len(KAPPA_GRID), 1)) / len(KAPPA_GRID)
    out: FloatArray = p / p.sum()
    return out


@dataclass
class FitInput:
    """Arrays of the kept hours of one campaign, ready for the grid."""

    y: FloatArray
    b: FloatArray
    adstock: FloatArray
    seg_starts: npt.NDArray[np.intp]  # first index of each day segment
    spend_ref: float

    @property
    def n(self) -> int:
        return int(self.y.size)


def spend_ref_of(ch: CampaignHours) -> float:
    """Mean spend per hour over the hours with spend (``s̄_c``, 04 §5.1)."""
    s = ch.spend0
    pos = s[s > 0]
    return float(pos.mean()) if pos.size else 1.0


def fit_input(ch: CampaignHours, rhythm: HourRhythm, delta: float) -> FitInput:
    a_full = adstock(ch.spend0, delta)
    k = ch.keep
    day = ch.day[k]
    seg = np.flatnonzero(np.r_[True, day[1:] != day[:-1]]) if day.size else np.array([], int)
    return FitInput(
        y=ch.y[k],
        b=rhythm.at(ch.ctx[k], ch.hour_local[k]),
        adstock=a_full[k],
        seg_starts=seg.astype(np.intp),
        spend_ref=spend_ref_of(ch),
    )


@dataclass
class GridFit:
    ll: FloatArray  # [κ, a] profile log-likelihood
    phi: float
    best: tuple[int, int]
    mu_best: FloatArray  # fitted mean at the best cell (kept hours)
    level_best: FloatArray  # exp(α_d) per kept hour at the best cell
    g_best: FloatArray  # g_t at the best cell (kept hours)


def grid_loglik(fi: FitInput, a_grid: FloatArray = A_GRID) -> GridFit:
    """Profile log-likelihood on the ``(κ, a)`` grid; φ from the best cell.

    ``Σ_{t∈d} B_t g_t = Σ_d B + a · Σ_d B·e`` is linear in ``a``, so the per-day sums need
    one pass per ``κ``; only ``Σ_t Y_t log g_t`` needs the ``[a, T]`` matrix, on hours
    with ``Y > 0``.
    """
    n_k, n_a = len(KAPPA_GRID), len(a_grid)
    ll = np.zeros((n_k, n_a))
    if fi.n == 0:
        z = np.zeros(0)
        return GridFit(ll, 1.0, (0, 0), z, z, z)
    yd = np.add.reduceat(fi.y, fi.seg_starts)
    pos = yd > 0
    seg_len = np.diff(np.r_[fi.seg_starts, fi.n])
    db = np.add.reduceat(fi.b, fi.seg_starts)[pos]
    nz = fi.y > 0
    y_nz = fi.y[nz]
    for i, kappa in enumerate(KAPPA_GRID):
        e = 1.0 - np.exp(-fi.adstock / (kappa * fi.spend_ref))  # [T]
        dbe = np.add.reduceat(fi.b * e, fi.seg_starts)[pos]  # [D]
        s1 = np.log1p(a_grid[:, None] * e[nz][None, :]) @ y_nz  # [A]
        dsum = db[None, :] + a_grid[:, None] * dbe[None, :]  # [A, D]
        ll[i] = s1 - np.log(dsum) @ yd[pos]
    bi, bj = np.unravel_index(int(np.argmax(ll)), ll.shape)
    kappa = KAPPA_GRID[int(bi)]
    g = 1.0 + a_grid[int(bj)] * (1.0 - np.exp(-fi.adstock / (kappa * fi.spend_ref)))
    bg = fi.b * g
    level = yd / np.add.reduceat(bg, fi.seg_starts)
    level_t = np.repeat(level, seg_len)
    mu = level_t * bg
    ok = mu > 0
    n_days = int(pos.sum())
    df = max(1, int(ok.sum()) - n_days - 2)
    phi = float(((fi.y[ok] - mu[ok]) ** 2 / mu[ok]).sum() / df) if ok.any() else 1.0
    return GridFit(ll, max(1.0, phi), (int(bi), int(bj)), mu, level_t, g)


def prior_grid_with(kappa_prior: FloatArray | None) -> FloatArray:
    """Prior ``[κ, a]`` with a given κ distribution (default uniform)."""
    if kappa_prior is None:
        return prior_grid()
    p = kappa_prior[:, None] * prior_a()[None, :]
    out: FloatArray = p / p.sum()
    return out


EB_ITER = 500
EB_FLOOR = 0.02  # every κ keeps at least this prior mass


def shop_kappa_prior(fits: list[GridFit]) -> FloatArray:
    """Empirical-Bayes κ distribution shared by the shop (decision log M3-ads B4).

    ``κ`` has no unit (``K = κ · spend_ref``), so like ``δ`` it is a property of how buyers
    react to ads on this platform, not of one campaign. The mixing weights ``π(κ)``
    maximise ``Σ_c log Σ_κ π(κ) L_c(κ)``, with ``L_c(κ)`` the campaign's quasi-likelihood
    integrated over the ``a`` prior (EM on the 6 grid points), then floored at
    ``EB_FLOOR`` so no κ is ruled out."""
    pa = prior_a()
    rows = []
    for f in fits:
        if f.mu_best.size == 0:
            continue
        lp = f.ll / f.phi
        lp = lp - lp.max()
        lk = np.exp(lp) @ pa  # [κ]
        rows.append(lk / lk.sum())
    n_k = len(KAPPA_GRID)
    if not rows:
        return np.full(n_k, 1.0 / n_k)
    lik = np.array(rows)  # [C, κ]
    pi = np.full(n_k, 1.0 / n_k)
    for _ in range(EB_ITER):
        resp = lik * pi[None, :]
        resp /= resp.sum(axis=1, keepdims=True)
        pi = resp.mean(axis=0)
    pi = np.maximum(pi, EB_FLOOR)
    out: FloatArray = pi / pi.sum()
    return out


def posterior(fit: GridFit, prior: FloatArray | None = None) -> FloatArray:
    """``∝ exp(ll / φ) · prior`` on ``[κ, a]``, normalised."""
    p = prior_grid() if prior is None else prior
    lp = fit.ll / fit.phi + np.log(p)
    lp -= lp.max()
    w = np.exp(lp)
    out: FloatArray = w / w.sum()
    return out


def choose_delta(
    campaigns: list[CampaignHours], rhythm: HourRhythm
) -> tuple[float, dict[float, float], dict[float, list[tuple[FitInput, GridFit]]]]:
    """Shop-wide ``δ̂`` = argmax over ``DELTA_GRID`` of Σ_c (max ll_c − ll_c(a=0)) / φ_c
    (04 §5.2 step 1). Also returns the fits per ``δ``."""
    totals: dict[float, float] = {}
    fits: dict[float, list[tuple[FitInput, GridFit]]] = {}
    for d in DELTA_GRID:
        tot = 0.0
        fits[d] = []
        for ch in campaigns:
            fi = fit_input(ch, rhythm, d)
            f = grid_loglik(fi)
            fits[d].append((fi, f))
            if fi.n:
                tot += float((f.ll.max() - f.ll[:, 0].max()) / f.phi)
        totals[d] = tot
    best = max(totals, key=lambda k: totals[k])
    return best, totals, fits


def fit_shop(
    campaigns: list[CampaignHours], rhythm: HourRhythm, *, delta: float | None = None
) -> tuple[float, dict[float, float], list[GridFit]]:
    """Shop-level step: ``δ̂`` (unless given) and the campaigns' grid fits at ``δ̂``."""
    if delta is None:
        if not campaigns:
            return 0.6, {}, []
        best, scores, fits = choose_delta(campaigns, rhythm)
        return best, scores, [f for _, f in fits[best]]
    return delta, {}, [grid_loglik(fit_input(c, rhythm, delta)) for c in campaigns]


# ---------------------------------------------------------------------- per-cell outcomes
def cell_incremental(
    adst: FloatArray, spend_ref: float, revenue: FloatArray, a_grid: FloatArray = A_GRID
) -> FloatArray:
    """``Σ_t revenue_t · (1 − 1/g_t)`` for every cell, shape ``[κ, a]``: the part of what was
    sold that the ads added."""
    out = np.empty((len(KAPPA_GRID), len(a_grid)))
    nz = revenue > 0
    r = revenue[nz]
    for i, kappa in enumerate(KAPPA_GRID):
        u = a_grid[:, None] * (1.0 - np.exp(-adst[nz] / (kappa * spend_ref)))[None, :]
        out[i] = (u / (1.0 + u)) @ r
    return out


def weighted_quantile(values: FloatArray, weights: FloatArray, q: float) -> float:
    v = values.ravel()
    w = weights.ravel()
    order = np.argsort(v, kind="stable")
    cw = np.cumsum(w[order])
    cw /= cw[-1]
    idx = int(np.searchsorted(cw, q, side="left"))
    return float(v[order][min(idx, v.size - 1)])


@dataclass
class PairedCheck:
    """Crude check from budget-exhausted hours matched with the same hour on days with
    spend (04 §5.3)."""

    n_off: int
    n_on: int
    crude_lift: float | None
    model_lift: float | None

    @property
    def ratio(self) -> float | None:
        if self.crude_lift is None:
            return None
        return 1.0 / (1.0 + self.crude_lift)

    @property
    def ok(self) -> bool:
        """Disagree when the two lifts differ by more than 2× (and by > 0.1 in absolute)."""
        c, m = self.crude_lift, self.model_lift
        if c is None or m is None:
            return True
        if abs(c - m) <= 0.1:
            return True
        lo, hi = sorted((max(c, 1e-6), max(m, 1e-6)))
        return hi / lo <= 2.0


MIN_CHECK_HOURS = 10


def paired_check(
    ch: CampaignHours,
    fi: FitInput,
    fit: GridFit,
    a_hat: float,
    kappa_hat: float,
) -> PairedCheck:
    """For every kept ``budget_exhausted`` hour, compare ``Y / (e^α B)`` with the same local
    hour on kept days where the campaign was spending. Model lift = same ratio with the
    fitted ``g``. ``lift = mean_on / mean_off − 1``."""
    k = ch.keep
    status = ch.status[k]
    hour = ch.hour_local[k]
    days = ch.day[k]
    spend = ch.spend0[k]
    denom = fit.level_best * fi.b
    good = denom > 0
    g = 1.0 + a_hat * (1.0 - np.exp(-fi.adstock / (kappa_hat * fi.spend_ref)))
    off = (status == "budget_exhausted") & good
    on_all = (spend > 0) & good
    off_hours = np.unique(hour[off])
    m_off_obs: list[float] = []
    m_on_obs: list[float] = []
    m_off_mod: list[float] = []
    m_on_mod: list[float] = []
    n_off = n_on = 0
    for h in off_hours.tolist():
        o = off & (hour == h)
        on = on_all & (hour == h) & ~np.isin(days, days[o])
        if not on.any():
            continue
        wo = denom[o]
        wn = denom[on]
        m_off_obs.append(float(fi.y[o].sum()))
        m_on_obs.append(float(fi.y[on].sum() / wn.sum() * wo.sum()))
        m_off_mod.append(float((g[o] * wo).sum()))
        m_on_mod.append(float((g[on] * wn).sum() / wn.sum() * wo.sum()))
        n_off += int(o.sum())
        n_on += int(on.sum())
    if n_off < MIN_CHECK_HOURS or sum(m_off_obs) <= 0:
        return PairedCheck(n_off, n_on, None, None)
    crude = sum(m_on_obs) / sum(m_off_obs) - 1.0
    model = sum(m_on_mod) / sum(m_off_mod) - 1.0
    return PairedCheck(n_off, n_on, float(crude), float(model))
