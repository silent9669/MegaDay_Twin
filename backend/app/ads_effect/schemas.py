"""Contract of the ads-effect layer: :class:`IroasEstimate` (plan 04 §7, 05 §5.2–5.3).

``IroasEstimate`` is what the simulator, the API and the AI tools read. One estimate per
campaign and history period; it mirrors a row of ``iroas_estimates``.

Model behind it (04 §5.1), per campaign ``c`` and hour ``t``::

    A_t = spend_t + delta · A_{t-1}       # adstock (VND)
    K   = kappa · spend_ref               # spend_ref = mean spend per hour with spend
    g_t = 1 + a · (1 − exp(−A_t / K))     # demand multiplier of ads
    μ_t = base_t · g_t                    # base_t = demand without ads

``(kappa, a)`` is uncertain: ``posterior`` holds the grid cells with their weights (sum 1).
``delta`` and ``spend_ref`` are fixed numbers.

Simulator use (05 §5.2–5.3), for SKU ``i`` of the campaign with spend share ``share_i``::

    A_{i,t} = share_i · spend_t + delta · A_{i,t-1},   A_{i,t0-1} = share_i · adstock_last
    u_{i,t} = est.uplift(A_i, share=share_i)                       # expected g − 1, shape [T]
    # or, with uncertainty and common random numbers across actions:
    kappa_s, a_s = est.sample_params(S, rng)                       # draw ONCE, reuse per action
    u = est.uplift_at(A_i[None, :], kappa_s[:, None], a_s[:, None], share=share_i)  # [S, T]

``K_i = K · share_i`` (04 §5.4): every SKU of a campaign shares ``a``; the incremental
part of a SKU is proportional to its own base demand.

Strict typing: the simulator package is ``mypy --strict`` and imports this module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field

Label = Literal["verified", "reference", "scenario"]
FloatArray = npt.NDArray[np.float64]

MODEL_ID = "adstock_grid@1.0.0"
DEFAULT_SEED = 20261111

# Reasons an hour is left out of the fit (04 §4); keys of ``excluded_hours``.
EXCLUSION_REASONS: tuple[str, ...] = ("unknown", "launch", "stock_pause", "low_stock")

# Check flag (04 §5.3).
FLAG_MODEL_CHECK_DISAGREE = "MODEL_CHECK_DISAGREE"


class PosteriorCell(BaseModel):
    """One cell of the ``(kappa, a)`` grid with its posterior weight ``w``."""

    kappa: float
    a: float
    w: float = Field(ge=0.0)


class IroasEstimate(BaseModel):
    """Incremental ROAS of one campaign over a history period (plan 04 §7).

    Money fields are VND; ratios are revenue per VND of spend.

    * ``iroas_mean`` / ``iroas_p10`` / ``iroas_p90``: posterior mean and 10/90 % quantiles
      of ``Σ incremental revenue / Σ spend`` over the period.
    * ``marginal_iroas``: extra revenue per extra VND at the spend schedule of the last
      7 days (+1 % spend, finite difference). Below ``iroas_mean`` because of saturation.
    * ``platform_roas``: platform-reported ROAS over the same period (display only, 04 §2).
    * ``breakeven_iroas``: ``eff_price / contribution`` weighted over the campaign's SKUs
      (05 §2); ``None`` when unit costs are missing. Economics may recompute it.
    * ``excluded_hours``: hours left out of the fit by reason (``EXCLUSION_REASONS``).
    * ``n_clean_off_hours``: hours without spend (paused / budget exhausted) still in the fit.
    * ``check_ratio``: crude lift from budget-exhausted hours (04 §5.3), ``None`` if too few.
    * ``sku_shares``: share of the campaign's spend/base demand per SKU (sum 1, 04 §5.4).
    * ``adstock_last``: campaign adstock (VND) at the last hour of the period, to seed
      the simulator's adstock recursion.
    """

    estimate_id: str
    campaign_id: str
    period_start: datetime
    period_end: datetime
    model_id: str = MODEL_ID
    delta: float
    spend_ref: float = Field(gt=0.0)
    kappa_mean: float
    a_mean: float
    posterior: list[PosteriorCell]
    spend_total: int
    inc_revenue_mean: int
    iroas_mean: float
    iroas_p10: float
    iroas_p90: float
    marginal_iroas: float | None = None
    platform_roas: float | None = None
    breakeven_iroas: float | None = None
    n_hours_used: int
    n_clean_off_hours: int
    excluded_hours: dict[str, int]
    check_ratio: float | None = None
    flags: list[str] = Field(default_factory=list)
    confidence: Label
    reasons: list[str] = Field(default_factory=list)
    sku_shares: dict[str, float] = Field(default_factory=dict)
    adstock_last: float = 0.0
    panel_version: str
    created_at: datetime

    # ------------------------------------------------------------------ grid helpers
    def grid_arrays(self) -> tuple[FloatArray, FloatArray, FloatArray]:
        """``(kappa, a, w)`` of the posterior cells; ``w`` renormalised to sum 1."""
        kappa = np.array([c.kappa for c in self.posterior], dtype=np.float64)
        a = np.array([c.a for c in self.posterior], dtype=np.float64)
        w = np.array([c.w for c in self.posterior], dtype=np.float64)
        total = float(w.sum())
        if total <= 0.0 or not np.isfinite(total):
            raise ValueError("posterior has no weight")
        return kappa, a, w / total

    def sample_params(
        self, n: int, rng: np.random.Generator | None = None
    ) -> tuple[FloatArray, FloatArray]:
        """Draw ``n`` pairs ``(kappa, a)`` from the posterior grid (05 §5.3).

        Draw once per simulation and reuse the same arrays for every action (common
        random numbers). Default ``rng`` = ``Generator(PCG64(20261111))``.
        """
        kappa, a, w = self.grid_arrays()
        gen = rng if rng is not None else np.random.default_rng(DEFAULT_SEED)
        idx = gen.choice(len(w), size=n, p=w)
        return kappa[idx], a[idx]

    def uplift_at(
        self,
        adstock: npt.ArrayLike,
        kappa: npt.ArrayLike,
        a: npt.ArrayLike,
        share: float = 1.0,
    ) -> FloatArray:
        """``a · (1 − exp(−adstock / (kappa · spend_ref · share)))`` with numpy broadcasting.

        ``adstock`` is the SKU's own adstock (VND, built from ``share · spend``); for the
        whole campaign use ``share=1``.
        """
        if share <= 0.0:
            raise ValueError("share must be > 0")
        adst = np.maximum(np.asarray(adstock, dtype=np.float64), 0.0)
        k = np.asarray(kappa, dtype=np.float64) * self.spend_ref * share
        out: FloatArray = np.asarray(a, dtype=np.float64) * (1.0 - np.exp(-adst / k))
        return out

    def uplift(
        self,
        adstock: npt.ArrayLike,
        share: float = 1.0,
        draws: int | None = None,
        rng: np.random.Generator | None = None,
    ) -> FloatArray:
        """``g − 1`` for the simulator (04 §7).

        * ``draws=None``: posterior expectation, same shape as ``adstock``.
        * ``draws=n``: ``n`` draws of ``(kappa, a)`` from the posterior, result shape
          ``(n, *adstock.shape)``. Pass the same ``rng`` seed for every action, or use
          :meth:`sample_params` + :meth:`uplift_at` to share the draws explicitly.
        """
        adst = np.asarray(adstock, dtype=np.float64)
        if draws is None:
            kappa, a, w = self.grid_arrays()
            shape = (len(w),) + (1,) * adst.ndim
            u = self.uplift_at(adst[None, ...], kappa.reshape(shape), a.reshape(shape), share)
            out: FloatArray = np.tensordot(w, u, axes=(0, 0))
            return out
        kappa_s, a_s = self.sample_params(draws, rng)
        shape = (draws,) + (1,) * adst.ndim
        return self.uplift_at(adst[None, ...], kappa_s.reshape(shape), a_s.reshape(shape), share)

    def adstock_path(
        self, spend: npt.ArrayLike, share: float = 1.0, start: float | None = None
    ) -> FloatArray:
        """Adstock recursion ``A_t = share·spend_t + delta·A_{t−1}``; ``A_{−1}`` defaults to
        ``share · adstock_last`` (state at the end of the period). Last axis = time."""
        s = np.asarray(spend, dtype=np.float64) * share
        prev = np.full(s.shape[:-1], share * self.adstock_last if start is None else start)
        out = np.empty_like(s)
        for t in range(s.shape[-1]):
            prev = s[..., t] + self.delta * prev
            out[..., t] = prev
        return out
