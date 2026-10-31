"""Vectorised Monte Carlo with common random numbers (SIM-02, plan 05 §5.2–§5.3).

Demand of SKU ``i`` under action ``a`` (sample ``s``, hour ``t``)::

    u_{s,t}(a)  = a_s · (1 − exp(−A_t(a) / (κ_s · s̄)))          # A = campaign adstock
    λ_{s,t}(a)  = λ_sq_t · (1 + u_{s,t}(a)) / (1 + u_{s,t}(sq)) · rank_t(a)
    demand      = PoissonInv( U_{s,t},  λ_{s,t}(a) · Lmult_s · G_{s,t} )

``sq`` = the ``continue`` schedule (the forecast learned under the current policy, 03 §1).
``K_i = K_c · share_i`` and ``A_i = share_i · A_c`` give the same ratio for every SKU, so the
uplift is computed once per campaign. Combo components outside the campaign keep
``λ = λ_sq`` (no ads effect).

**Common random numbers.** ``Lmult ~ LogNormal(0, σ_i)``, ``G ~ Gamma(k_i, 1/k_i)``,
``(κ_s, a_s)`` from the iROAS posterior and the uniforms ``U`` of the Poisson draw are made
once per run and shared by every action. The Poisson count is the inverse CDF of the same
uniform (monotone coupling): an action that raises ``λ`` never sells fewer units in the same
sample, so ``P(A > B) = mean_s[J_s(A) > J_s(B)]`` measures the action, not the noise.
(The plan's "new ``Generator(seed_i)`` per action" desynchronises numpy's Poisson stream
when ``λ`` changes; inverse CDF is the exact version of the same idea — decision log M3.)

``J_s(a) = Σ m·sold − Σ spend + Σ V·stock_end − penalty·Σ lost`` (05 §4).
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime

import numpy as np
import numpy.typing as npt
from scipy.special import ndtri

from app.core.timeutil import to_local
from app.simulator.constraints import StockSetup, check_action, simulate_stock
from app.simulator.inputs import SimInputs
from app.simulator.policies import adstock, on_mask, spend_schedule
from app.simulator.schemas import ActionSpec

FloatArr = npt.NDArray[np.float64]
IntArr = npt.NDArray[np.int64]
BoolArr = npt.NDArray[np.bool_]

DEFAULT_SEED = 20261111
POISSON_NORMAL_ABOVE = 40.0  # λ above this: normal approximation of the inverse CDF
CONTINUE = ActionSpec(kind="continue")


# ---------------------------------------------------------------------- sampling
def poisson_inv(u: FloatArr, lam: FloatArr) -> FloatArr:
    """Poisson quantile ``F⁻¹(u; λ)`` element-wise (vectorised). Small ``λ``: CDF walk
    over the still-unresolved elements; large ``λ``: normal approximation with continuity
    correction (error < 1 unit at λ ≥ 40, negligible next to NegBin dispersion)."""
    lam_f = np.maximum(np.asarray(lam, dtype=np.float64), 0.0).ravel()
    u_f = np.ascontiguousarray(u, dtype=np.float64).ravel()
    out = np.zeros(lam_f.shape)
    if lam_f.size and lam_f.max() > POISSON_NORMAL_ABOVE:
        big = lam_f > POISSON_NORMAL_ABOVE
        lb = lam_f[big]
        out[big] = np.maximum(np.floor(lb + np.sqrt(lb) * ndtri(u_f[big]) + 0.5), 0.0)
        pos = np.flatnonzero(~big)
    else:
        pos = None
    ls = lam_f if pos is None else lam_f[pos]
    us = u_f if pos is None else u_f[pos]
    # k = 0 resolves most hours of slow SKUs; walk the CDF only on the rest (compact arrays)
    p0 = np.exp(-ls)
    rest = np.flatnonzero(us > p0)
    if rest.size:
        idx_out = rest if pos is None else pos[rest]
        ls, us, p = ls[rest], us[rest], p0[rest]
        cdf = p.copy()
        k = np.zeros(ls.shape)
        idx = np.arange(ls.size)
        it = 0
        while idx.size and it < 400:
            it += 1
            k[idx] += 1.0
            p[idx] *= ls[idx] / it
            cdf[idx] += p[idx]
            idx = idx[(us[idx] > cdf[idx]) & (p[idx] > 1e-300)]
        out[idx_out] = k
    return out.reshape(np.shape(lam))


@dataclass(frozen=True)
class Draws:
    """Random numbers shared by every action of a run (``[N_sku, S, …]``)."""

    lmult: FloatArr  # [N, S]
    g: FloatArr  # [N, S, T]
    u: FloatArr  # [N, S, T]
    kappa: FloatArr  # [S]
    a: FloatArr  # [S]

    @property
    def samples(self) -> int:
        return int(self.kappa.shape[0])


def make_draws(inputs: SimInputs, samples: int, seed: int) -> Draws:
    """One child ``SeedSequence`` per SKU (stable order = ``inputs.skus``) plus one for the
    ads posterior: same seed → identical draws."""
    n = len(inputs.skus)
    t = inputs.T
    children = np.random.SeedSequence(seed).spawn(n + 1)
    lm = np.empty((n, samples))
    g = np.empty((n, samples, t))
    u = np.empty((n, samples, t))
    for i, s in enumerate(inputs.skus):
        rng = np.random.default_rng(children[i])
        lm[i] = np.exp(rng.normal(0.0, max(s.sigma, 0.0), size=samples))
        kk = max(s.k, 1e-6)
        g[i] = rng.gamma(kk, 1.0 / kk, size=(samples, t))
        u[i] = rng.random((samples, t))
    ka, aa = inputs.iroas.sample(samples, np.random.default_rng(children[n]))
    return Draws(lm, g, u, np.asarray(ka, dtype=np.float64), np.asarray(aa, dtype=np.float64))


# ---------------------------------------------------------------------- results
@dataclass
class ActionRun:
    action: ActionSpec
    feasible: bool
    violations: list[tuple[str, str]]
    spend_hourly: FloatArr  # [T]
    j: FloatArr  # [S]
    contribution: FloatArr  # [S]
    spend: FloatArr  # [S] (deterministic, broadcast)
    units_sold: FloatArr  # [S] campaign units (combo = 1)
    stock_units_sold: FloatArr  # [S] stock units (combo = Σ qty)
    leftover_units: FloatArr  # [S]
    leftover_value: FloatArr  # [S]
    leftover_by_sku: dict[str, FloatArr]  # stock-holding SKUs
    lost_units: FloatArr  # [S]
    sold_by_sku: dict[str, FloatArr]
    lost_by_sku: dict[str, FloatArr]
    demand_by_sku: dict[str, FloatArr]
    first_lost_t: dict[str, IntArr]


@dataclass
class Perturb:
    """Sensitivity knobs (05 §8). ``level`` multiplies ``Lmult``, ``ads`` multiplies
    ``a``; ``restock_delay`` replaces every SKU's delay (days, may be negative)."""

    level: float = 1.0
    ads: float = 1.0
    restock_delay: float | None = None


@dataclass
class SimRun:
    inputs: SimInputs
    runs: list[ActionRun]
    samples: int
    seed: int
    draws: Draws
    perturb: Perturb = field(default_factory=Perturb)

    def by_key(self) -> dict[str, ActionRun]:
        return {r.action.key: r for r in self.runs}


def _stock_setup(inputs: SimInputs, restock: FloatArr) -> StockSetup:
    js = inputs.stock_skus
    jidx = {s.sku_id: j for j, s in enumerate(js)}
    combos = inputs.combos
    q = np.zeros((len(combos), len(js)))
    for c, s in enumerate(combos):
        for comp, qty in s.components:
            if comp in jidx:
                q[c, jidx[comp]] = qty
    reserve = np.array([s.reserve for s in js], dtype=np.float64)
    for c, s in enumerate(combos):
        reserve += q[c] * s.reserve
    sold_before = np.array([inputs.flash_sold_before.get(s.sku_id, 0.0) for s in js])
    reserve = np.maximum(reserve - sold_before, 0.0)
    return StockSetup(
        stock0=np.array([s.stock0 for s in js], dtype=np.float64),
        restock=restock,
        reserve0=reserve,
        reserve_active=inputs.reserve_active,
        flash=inputs.flash,
        safety=np.array([s.safety for s in js], dtype=np.float64),
        combo_qty=q,
    )


def simulate(
    inputs: SimInputs,
    actions: Sequence[ActionSpec],
    samples: int = 1000,
    seed: int = DEFAULT_SEED,
    *,
    perturb: Perturb | None = None,
    draws: Draws | None = None,
) -> SimRun:
    """Every action on the same random numbers. Pure numpy; no DB access."""
    pt = perturb or Perturb()
    dr = (
        draws
        if draws is not None and draws.samples == samples
        else make_draws(inputs, samples, seed)
    )
    t_len = inputs.T
    spend_ctx = inputs.spend
    if pt.restock_delay is not None:
        spend_ctx = replace(spend_ctx, restock_t=inputs.restock_t(pt.restock_delay))
    acts = list(actions)
    n_a = len(acts)
    labels = [f"{to_local(h):%H:%M %d/%m}" for h in inputs.hours]  # VN time, violation text
    spends = np.vstack([spend_schedule(a, spend_ctx) for a in acts])  # [A, T]
    masks = np.vstack([on_mask(a, spend_ctx) for a in acts])
    sq = spend_schedule(CONTINUE, spend_ctx)
    delta = float(inputs.iroas.delta)
    ad = np.vstack([adstock(s, delta, inputs.adstock0) for s in spends])  # [A, T]
    ad_sq = adstock(sq, delta, inputs.adstock0)
    k_scale = np.maximum(dr.kappa * float(inputs.iroas.spend_scale), 1e-9)[None, :, None]
    a_s = (dr.a * pt.ads)[None, :, None]
    u = a_s * (1.0 - np.exp(-ad[:, None, :] / k_scale))  # [A, S, T]
    u_sq = (a_s * (1.0 - np.exp(-ad_sq[None, None, :] / k_scale)))[0]  # [S, T]
    ratio = (1.0 + u) / (1.0 + u_sq[None])
    if inputs.rank_loss > 0:
        off_days = np.cumsum(~masks, axis=1) / 24.0
        rank = np.maximum(1.0 - inputs.rank_loss * off_days, 0.0)
        ratio = ratio * rank[:, None, :]

    # actions with the same demand driver (spend schedule, and on/off when rank loss is on)
    # share one Poisson draw: e.g. pause ≡ pause_until_restock when nothing arrives in time
    keys = [
        spends[i].tobytes() + (masks[i].tobytes() if inputs.rank_loss > 0 else b"")
        for i in range(n_a)
    ]
    uniq: dict[bytes, int] = {}
    rows = [uniq.setdefault(k, len(uniq)) for k in keys]
    first_of = [rows.index(r) for r in range(len(uniq))]
    inverse = np.array(rows, dtype=np.int64)
    ratio_u = ratio[first_of]
    demand: dict[str, FloatArr] = {}
    for i, s in enumerate(inputs.skus):
        base = s.lam_sq[None, None, :] * (dr.lmult[i] * pt.level)[None, :, None] * dr.g[i][None]
        if s.in_campaign:
            lam = base * ratio_u
            d = poisson_inv(np.broadcast_to(dr.u[i][None], lam.shape), lam)
            demand[s.sku_id] = d[inverse]
        else:  # no ads effect: same demand under every action
            d = poisson_inv(dr.u[i][None], base)
            demand[s.sku_id] = np.broadcast_to(d, (n_a, samples, t_len))

    js = inputs.stock_skus
    cs = inputs.combos
    restock = inputs.restock_matrix(pt.restock_delay)
    setup = _stock_setup(inputs, restock)
    d_single = (
        np.stack([demand[s.sku_id] for s in js]) if js else np.zeros((0, n_a, samples, t_len))
    )
    d_combo = np.stack([demand[s.sku_id] for s in cs]) if cs else np.zeros((0, n_a, samples, t_len))
    m_single = np.stack([s.m for s in js]) if js else np.zeros((0, t_len))
    m_combo = np.stack([s.m for s in cs]) if cs else np.zeros((0, t_len))
    res = simulate_stock(setup, d_single, d_combo, m_single, m_combo)

    v = np.array([s.leftover_value for s in js], dtype=np.float64)
    leftover_val = np.einsum("j,jas->as", v, res.stock_end) if js else np.zeros((n_a, samples))
    leftover_units = res.stock_end.sum(axis=0) if js else np.zeros((n_a, samples))
    lost = res.lost.sum(axis=0) + res.combo_lost.sum(axis=0)
    spend_tot = spends.sum(axis=1)
    j_val = res.contribution - spend_tot[:, None] + leftover_val - inputs.penalty * lost
    in_camp_j = np.array([s.in_campaign for s in js], dtype=bool)
    units = res.sold[in_camp_j].sum(axis=0) + res.combo_sold.sum(axis=0)
    stock_units = res.sold.sum(axis=0) + np.einsum("cj,cas->as", setup.combo_qty, res.combo_sold)

    runs = []
    for ai, act in enumerate(acts):
        viol = check_action(act, masks[ai], inputs.must_run, labels)
        sold_by = {s.sku_id: res.sold[j, ai] for j, s in enumerate(js)}
        sold_by.update({s.sku_id: res.combo_sold[c, ai] for c, s in enumerate(cs)})
        lost_by = {s.sku_id: res.lost[j, ai] for j, s in enumerate(js)}
        lost_by.update({s.sku_id: res.combo_lost[c, ai] for c, s in enumerate(cs)})
        first = {s.sku_id: res.first_lost_t[j, ai] for j, s in enumerate(js)}
        first.update({s.sku_id: res.combo_first_lost_t[c, ai] for c, s in enumerate(cs)})
        runs.append(
            ActionRun(
                action=act,
                feasible=not viol,
                violations=viol,
                spend_hourly=spends[ai],
                j=j_val[ai],
                contribution=res.contribution[ai],
                spend=np.full(samples, spend_tot[ai]),
                units_sold=units[ai],
                stock_units_sold=stock_units[ai],
                leftover_units=leftover_units[ai],
                leftover_value=leftover_val[ai],
                leftover_by_sku={s.sku_id: res.stock_end[j, ai] for j, s in enumerate(js)},
                lost_units=lost[ai],
                sold_by_sku=sold_by,
                lost_by_sku=lost_by,
                demand_by_sku={k: d[ai].sum(axis=1) for k, d in demand.items()},
                first_lost_t=first,
            )
        )
    return SimRun(inputs=inputs, runs=runs, samples=samples, seed=seed, draws=dr, perturb=pt)


def stockout_hour(inputs: SimInputs, first_t: IntArr) -> datetime | None:
    """Median first hour with unserved demand; ``None`` when the median sample never runs
    out inside the horizon."""
    med = int(np.median(first_t))
    return inputs.hours[med] if med < inputs.T else None


# ---------------------------------------------------------------------- batch helper
def simulate_many(
    jobs: Sequence[tuple[SimInputs, Sequence[ActionSpec]]],
    samples: int = 1000,
    seed: int = DEFAULT_SEED,
    workers: int = 4,  # measured best on a 16-thread dev box (small arrays, GIL-bound beyond)
) -> list[SimRun]:
    """Many campaigns in a thread pool (numpy releases the GIL), 05 §5.5."""
    if len(jobs) < 2 or workers <= 1:
        return [simulate(i, a, samples, seed) for i, a in jobs]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda j: simulate(j[0], j[1], samples, seed), jobs))


# ---------------------------------------------------------------------- cache
_CACHE: OrderedDict[str, object] = OrderedDict()
CACHE_SIZE = 256


def cache_key(parts: Sequence[object]) -> str:
    return hashlib.sha256(repr(tuple(parts)).encode()).hexdigest()[:32]


def cache_get(key: str) -> object | None:
    v = _CACHE.get(key)
    if v is not None:
        _CACHE.move_to_end(key)
    return v


def cache_put(key: str, value: object) -> None:
    _CACHE[key] = value
    _CACHE.move_to_end(key)
    while len(_CACHE) > CACHE_SIZE:
        _CACHE.popitem(last=False)


def cache_clear() -> None:
    _CACHE.clear()
