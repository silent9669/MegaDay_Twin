"""iROAS per campaign: posterior summaries, label, write/read ``iroas_estimates`` (ADS-04,
ADS-05, ADS-06; plan 04 §5.2–§7).

* Incremental revenue of an hour = revenue sold × ``(1 − 1/g_t)``: the share of what was
  actually sold that the ads added. With ``μ = base·g`` this is ``base·(g − 1)`` in
  expectation (04 §5.2) and it needs no ``α_d`` on excluded days; hours where stock limited
  sales contribute what was really sold (decision log M3-ads B1).
* ``iROAS(κ, a) = Σ_t inc_t / Σ_t spend_t`` over the period (hours with known spend);
  mean and p10/p90 are posterior-weighted over the 366 grid cells.
* Marginal iROAS: +1 % spend over the last 7 days, same posterior.

Storage: the DDL of ``iroas_estimates`` (04 §7) has no columns for ``spend_ref``,
``adstock_last``, ``sku_shares``, ``breakeven_iroas``, ``flags``, ``reasons``; they are kept
next to the cells in the ``posterior`` JSON object (``{"cells": [...], ...}``) so the
schema shared with the other layers is untouched (decision log M3-ads C1).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from app.ads_effect import model as m
from app.ads_effect.dataset import CampaignHours, HourRhythm, estimate_rhythm, load_campaign_hours
from app.ads_effect.schemas import (
    FLAG_MODEL_CHECK_DISAGREE,
    MODEL_ID,
    IroasEstimate,
    Label,
    PosteriorCell,
)
from app.core import messages_vi as vi
from app.core.config import get_settings
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.timeutil import utcnow

log = get_logger(__name__)

MIN_OFF_HOURS_SCENARIO = 12  # 04 §6
MIN_OFF_HOURS_VERIFIED = 48
MAX_REL_WIDTH_VERIFIED = 0.6
MARGINAL_HOURS = 168
MARGINAL_STEP = 0.01
SHOP_KAPPA_PRIOR = True  # empirical-Bayes κ prior shared by the shop (model.shop_kappa_prior)


def iroas_label(n_clean_off_hours: int, rel_width: float, check_ok: bool) -> Label:
    """04 §6."""
    if n_clean_off_hours < MIN_OFF_HOURS_SCENARIO:
        return "scenario"
    if n_clean_off_hours >= MIN_OFF_HOURS_VERIFIED and rel_width <= MAX_REL_WIDTH_VERIFIED:
        return "verified" if check_ok else "reference"
    return "reference"


def _fmt(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


# ---------------------------------------------------------------------- costs
def _latest_costs(conn: duckdb.DuckDBPyConnection) -> dict[str, tuple[float, float]]:
    rows = conn.execute(
        "SELECT sku_id, arg_max(unit_cost, valid_from), arg_max(packaging_cost, valid_from) "
        "FROM cost_items GROUP BY sku_id"
    ).fetchall()
    return {str(r[0]): (float(r[1]), float(r[2] or 0)) for r in rows}


def breakeven_of(
    ch: CampaignHours, costs: dict[str, tuple[float, float]], fee_rate: float
) -> float | None:
    """``Σ revenue / Σ contribution`` over the campaign's SKUs (05 §2: ``eff_price / m``,
    weighted by what each SKU sold). ``None`` when a SKU with ≥ 10 % of revenue has no
    cost or the contribution is not positive."""
    rev_total = float(ch.sku_revenue.sum())
    if rev_total <= 0:
        return None
    contrib = 0.0
    covered = 0.0
    for sku, units, rev in zip(ch.sku_ids, ch.sku_units, ch.sku_revenue, strict=True):
        if sku not in costs:
            if rev >= 0.1 * rev_total:
                return None
            continue
        unit_cost, pack = costs[sku]
        contrib += float(rev) * (1 - fee_rate) - float(units) * (unit_cost + pack)
        covered += float(rev)
    if contrib <= 0 or covered <= 0:
        return None
    return covered / contrib


def _cells(post: np.ndarray[Any, np.dtype[np.float64]]) -> list[PosteriorCell]:
    """Grid cells with weight > ``MIN_POST_WEIGHT`` (04 §7), weights renormalised to 1."""
    idx = list(zip(*np.nonzero(post > m.MIN_POST_WEIGHT), strict=True))
    total = float(sum(post[i, j] for i, j in idx))
    return [
        PosteriorCell(kappa=m.KAPPA_GRID[i], a=float(m.A_GRID[j]), w=float(post[i, j]) / total)
        for i, j in idx
    ]


# ---------------------------------------------------------------------- one campaign
@dataclass
class CampaignFit:
    ch: CampaignHours
    post: np.ndarray[Any, np.dtype[np.float64]]
    iroas_cells: np.ndarray[Any, np.dtype[np.float64]]
    phi: float


def estimate_campaign(
    ch: CampaignHours,
    rhythm: HourRhythm,
    delta: float,
    *,
    costs: dict[str, tuple[float, float]] | None = None,
    fee_rate: float = 0.12,
    rhythm_reason: bool = False,
    kappa_prior: np.ndarray[Any, np.dtype[np.float64]] | None = None,
) -> IroasEstimate:
    fi = m.fit_input(ch, rhythm, delta)
    fit = m.grid_loglik(fi)
    post = m.posterior(fit, m.prior_grid_with(kappa_prior))
    a_full = m.adstock(ch.spend0, delta)
    spend_ref = fi.spend_ref
    known = np.isfinite(ch.spend)
    spend_total = float(ch.spend0[known].sum())
    rev = np.where(known, ch.revenue, 0.0)
    inc_cells = m.cell_incremental(a_full, spend_ref, rev)  # [κ, a]
    iroas_cells = inc_cells / spend_total if spend_total > 0 else np.zeros_like(inc_cells)

    # Marginal at the last 7 days' schedule: base_t = R_t / g_t, g at 1.01·A.
    tail = np.zeros(ch.y.size, dtype=bool)
    tail[-MARGINAL_HOURS:] = True
    tail &= known
    spend_tail = float(ch.spend0[tail].sum())
    marginal: float | None = None
    if spend_tail > 0:
        marg_cells = np.empty_like(inc_cells)
        sel = tail & (ch.revenue > 0)
        at = a_full[sel]
        rt = ch.revenue[sel]
        for i, kappa in enumerate(m.KAPPA_GRID):
            k = kappa * spend_ref
            e0 = 1.0 - np.exp(-at / k)
            e1 = 1.0 - np.exp(-at * (1 + MARGINAL_STEP) / k)
            g0 = 1.0 + m.A_GRID[:, None] * e0[None, :]
            g1 = 1.0 + m.A_GRID[:, None] * e1[None, :]
            marg_cells[i] = ((rt[None, :] / g0) * (g1 - g0)).sum(axis=1)
        marginal = float((post * marg_cells).sum() / (MARGINAL_STEP * spend_tail))

    mean = float((post * iroas_cells).sum())
    p10 = m.weighted_quantile(iroas_cells, post, 0.10)
    p90 = m.weighted_quantile(iroas_cells, post, 0.90)
    kap = np.array(m.KAPPA_GRID)[:, None] * np.ones_like(post)
    agr = np.ones((len(m.KAPPA_GRID), 1)) * m.A_GRID[None, :]
    kappa_mean = float((post * kap).sum())
    a_mean = float((post * agr).sum())

    # Paired check at the best cell (04 §5.3).
    check: m.PairedCheck | None = None
    if fi.n:
        bi, bj = fit.best
        check = m.paired_check(ch, fi, fit, float(m.A_GRID[bj]), m.KAPPA_GRID[bi])
    check_ok = check.ok if check is not None else True

    n_off = ch.n_clean_off_hours
    rel_width = (p90 - p10) / mean if mean > 1e-9 else float("inf")
    label = iroas_label(n_off, rel_width, check_ok)
    flags: list[str] = []
    excluded = ch.excluded_hours()
    reasons = [vi.ADS_REASON_DELTA.format(delta=_fmt(delta))]
    if n_off < MIN_OFF_HOURS_SCENARIO:
        reasons.append(vi.ADS_REASON_FEW_OFF.format(n=n_off))
    else:
        reasons.append(vi.ADS_REASON_OFF_HOURS.format(n=n_off))
    for key, n in excluded.items():
        if n:
            reasons.append(vi.ADS_REASON_EXCLUDED.format(n=n, what=vi.ADS_EXCLUDED_WHAT[key]))
    if label != "scenario" and rel_width > MAX_REL_WIDTH_VERIFIED:
        reasons.append(vi.ADS_REASON_WIDE.format(width=min(rel_width, 9.99)))
    if check is not None and not check_ok:
        flags.append(FLAG_MODEL_CHECK_DISAGREE)
        reasons.append(
            vi.ADS_REASON_CHECK_DISAGREE.format(
                crude=check.crude_lift or 0.0, model=check.model_lift or 0.0
            )
        )
    if rhythm_reason:
        reasons.append(vi.ADS_REASON_RHYTHM_ALL)
    if len(ch.sku_ids) > 1:
        reasons.append(vi.ADS_REASON_SKU_SPLIT)
    reasons.append(vi.ADS_REASON_SHORT_TERM)

    cells = _cells(post)
    roas = ch.attributed_revenue / spend_total if spend_total > 0 else None
    breakeven = breakeven_of(ch, costs, fee_rate) if costs is not None else None
    return IroasEstimate(
        estimate_id=new_id("iroas"),
        campaign_id=ch.campaign_id,
        period_start=ch.hours[0].to_pydatetime(),
        period_end=(ch.hours[-1] + pd.Timedelta(hours=1)).to_pydatetime(),
        model_id=MODEL_ID,
        delta=delta,
        spend_ref=spend_ref,
        kappa_mean=kappa_mean,
        a_mean=a_mean,
        posterior=cells,
        spend_total=round(spend_total),
        inc_revenue_mean=round(float((post * inc_cells).sum())),
        iroas_mean=mean,
        iroas_p10=p10,
        iroas_p90=p90,
        marginal_iroas=marginal,
        platform_roas=roas,
        breakeven_iroas=breakeven,
        n_hours_used=fi.n,
        n_clean_off_hours=n_off,
        excluded_hours=excluded,
        check_ratio=check.ratio if check is not None else None,
        flags=flags,
        confidence=label,
        reasons=reasons,
        sku_shares=ch.sku_shares,
        adstock_last=float(a_full[-1]) if a_full.size else 0.0,
        panel_version=ch.panel_version or "",
        created_at=utcnow(),
    )


def prior_estimate(
    campaign_id: str,
    spend_ref: float,
    *,
    delta: float = 0.6,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    sku_shares: dict[str, float] | None = None,
) -> IroasEstimate:
    """Prior-only estimate (label ``scenario``) for a campaign without usable history; the
    simulator can use it so it is never blocked (12 · SIM-02)."""
    post = m.prior_grid()
    cells = _cells(post)
    now = utcnow()
    kap = float((post.sum(axis=1) * np.array(m.KAPPA_GRID)).sum())
    a_mean = float((post.sum(axis=0) * m.A_GRID).sum())
    return IroasEstimate(
        estimate_id=new_id("iroas"),
        campaign_id=campaign_id,
        period_start=period_start or now,
        period_end=period_end or now,
        delta=delta,
        spend_ref=max(spend_ref, 1.0),
        kappa_mean=kap,
        a_mean=a_mean,
        posterior=cells,
        spend_total=0,
        inc_revenue_mean=0,
        iroas_mean=0.0,
        iroas_p10=0.0,
        iroas_p90=0.0,
        n_hours_used=0,
        n_clean_off_hours=0,
        excluded_hours={},
        confidence="scenario",
        reasons=[vi.ADS_REASON_PRIOR],
        sku_shares=sku_shares or {},
        panel_version="",
        created_at=now,
    )


# ---------------------------------------------------------------------- all campaigns
@dataclass
class AdsRun:
    estimates: list[IroasEstimate]
    delta: float
    delta_scores: dict[float, float]
    rhythm_source: str
    seconds: float
    labels: dict[str, int] = field(default_factory=dict)


def estimate_all(
    conn: duckdb.DuckDBPyConnection,
    campaign_ids: list[str] | None = None,
    *,
    apply_stock_rules: bool = True,
    delta: float | None = None,
) -> AdsRun:
    """Estimate every campaign of the warehouse (does not write). ``δ`` and the κ prior are
    shared by all campaigns of the run (``delta`` fixes δ)."""
    t0 = time.perf_counter()
    rhythm = estimate_rhythm(conn)
    camps = load_campaign_hours(conn, campaign_ids, apply_stock_rules=apply_stock_rules)
    delta, scores, fits = m.fit_shop(camps, rhythm, delta=delta)
    kappa_prior = m.shop_kappa_prior(fits) if SHOP_KAPPA_PRIOR else None
    costs = _latest_costs(conn)
    fee = get_settings().platform_fee_rate
    ests = [
        estimate_campaign(
            ch,
            rhythm,
            delta,
            costs=costs,
            fee_rate=fee,
            rhythm_reason=rhythm.source != "no_ads_skus",
            kappa_prior=kappa_prior,
        )
        for ch in camps
    ]
    secs = time.perf_counter() - t0
    labels: dict[str, int] = dict(Counter(str(e.confidence) for e in ests))
    log.info("iroas_estimated", n=len(ests), delta=delta, seconds=round(secs, 2), **labels)
    return AdsRun(ests, delta, scores, rhythm.source, secs, labels)


# ---------------------------------------------------------------------- storage (ADS-06)
_COLUMNS = (
    "estimate_id", "campaign_id", "period_start", "period_end", "model_id", "delta",
    "kappa_mean", "a_mean", "posterior", "spend_total", "inc_revenue_mean", "iroas_mean",
    "iroas_p10", "iroas_p90", "marginal_iroas", "platform_roas", "n_hours_used",
    "n_clean_off_hours", "excluded_hours", "check_ratio", "confidence", "panel_version",
    "created_at",
)  # fmt: skip
_EXTRA = ("spend_ref", "adstock_last", "breakeven_iroas", "sku_shares", "flags", "reasons")


def _row(e: IroasEstimate) -> list[Any]:
    d = e.model_dump(mode="python")
    post = {"cells": [c.model_dump() for c in e.posterior], **{k: d[k] for k in _EXTRA}}
    d["posterior"] = json.dumps(post, ensure_ascii=False)
    d["excluded_hours"] = json.dumps(e.excluded_hours)
    return [d[c] for c in _COLUMNS]


def write_estimates(conn: duckdb.DuckDBPyConnection, estimates: list[IroasEstimate]) -> int:
    """Insert estimates; an older estimate of the same campaign, model and period is
    replaced (re-running ``estimate-ads`` is idempotent)."""
    if not estimates:
        return 0
    cols = ", ".join(_COLUMNS)
    marks = ", ".join("?" for _ in _COLUMNS)
    conn.execute("BEGIN TRANSACTION")
    try:
        for e in estimates:
            conn.execute(
                "DELETE FROM iroas_estimates WHERE campaign_id = ? AND model_id = ? "
                "AND period_start = ? AND period_end = ?",
                [e.campaign_id, e.model_id, e.period_start, e.period_end],
            )
        conn.executemany(
            f"INSERT INTO iroas_estimates ({cols}) VALUES ({marks})",
            [_row(e) for e in estimates],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(estimates)


def _from_row(row: tuple[Any, ...]) -> IroasEstimate:
    d = dict(zip(_COLUMNS, row, strict=True))
    post = json.loads(d["posterior"]) if isinstance(d["posterior"], str) else d["posterior"]
    if isinstance(post, list):  # plain DDL form [{kappa, a, w}]
        post = {"cells": post}
    d["posterior"] = [PosteriorCell(**c) for c in post["cells"]]
    for k in _EXTRA:
        if post.get(k) is not None:
            d[k] = post[k]
    d.setdefault("spend_ref", 1.0)
    ex = d["excluded_hours"]
    d["excluded_hours"] = json.loads(ex) if isinstance(ex, str) else ex
    return IroasEstimate(**d)


def load_estimates(
    conn: duckdb.DuckDBPyConnection, campaign_ids: list[str] | None = None
) -> dict[str, IroasEstimate]:
    """Latest estimate per campaign (by ``created_at``)."""
    where = "WHERE campaign_id IN (SELECT unnest(?))" if campaign_ids is not None else ""
    params: list[Any] = [campaign_ids] if campaign_ids is not None else []
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM iroas_estimates {where} "
        "QUALIFY row_number() OVER (PARTITION BY campaign_id ORDER BY created_at DESC) = 1 "
        "ORDER BY campaign_id",
        params,
    ).fetchall()
    return {str(r[1]): _from_row(r) for r in rows}


def load_estimate(conn: duckdb.DuckDBPyConnection, campaign_id: str) -> IroasEstimate | None:
    return load_estimates(conn, [campaign_id]).get(campaign_id)
