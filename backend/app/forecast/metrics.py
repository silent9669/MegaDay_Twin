"""Forecast metrics (FC-09, plan 03 §7.2).

With ``y`` actual and ``ŷ`` = p50, summed over the (SKU, day) cells of a backtest:

* ``WAPE = Σ|y − ŷ| / Σ y`` at SKU × day (hours summed first; ŷ = median of the day total)
* ``Bias = (Σŷ − Σy) / Σy``
* ``Pinball_q = mean(max(q·(y − ŷ_q), (q − 1)·(y − ŷ_q)))`` at hour level, q ∈ {0.1, 0.5, 0.9}
* ``Coverage`` (80 %) = share of (SKU, day) whose **randomised PIT** of the day total lies in
  ``[0.1, 0.9]``: ``u = F(y−1) + V·(F(y) − F(y−1))``, ``V ~ U(0, 1)`` seeded. For a correct
  predictive distribution ``u`` is exactly uniform, so the measure is 80 % even for small
  counts; the old ``y ∈ [p10, p90]`` (``coverage_interval``) reads ≈ 90 % there because the
  mass of the end points is counted in full.
* ``WAPE_floor`` = expected WAPE of the p50 if demand were ``NegBin(mean, k)`` around the
  model's own hourly mean: ``Σ E|Y − p50| / Σ E[Y]`` over the same (SKU, day) cells, by
  Monte Carlo (seeded). It is the error no forecast can avoid given the count noise; the
  model's ``level_sigma`` is left out on purpose — it is the model's own ignorance, and
  with it the floor grew with vagueness (a ×1.5 forecast passed on 10.10, M2 §D). The
  label rule compares ``WAPE ≤ (1 + tol) · WAPE_floor`` instead of a fixed 30 %.

WAPE only uses SKUs selling ≥ 1 unit/day (``wape_eligible``); sparse SKUs are judged on
pinball.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

FloatArr = npt.NDArray[np.float64]
QUANTILES = (0.1, 0.5, 0.9)
MIN_WAPE_RATE = 1.0  # units/day


def wape(y: FloatArr, yhat: FloatArr) -> float | None:
    s = float(np.sum(y))
    return float(np.sum(np.abs(y - yhat)) / s) if s > 0 else None


def bias(y: FloatArr, yhat: FloatArr) -> float | None:
    s = float(np.sum(y))
    return float((np.sum(yhat) - s) / s) if s > 0 else None


def pinball(y: FloatArr, q_pred: FloatArr, q: float) -> float:
    d = y - q_pred
    return float(np.mean(np.maximum(q * d, (q - 1) * d))) if d.size else 0.0


def pinball_sum(y: FloatArr, q_pred: FloatArr, q: float) -> float:
    d = y - q_pred
    return float(np.sum(np.maximum(q * d, (q - 1) * d)))


def coverage(y: FloatArr, lo: FloatArr, hi: FloatArr) -> float | None:
    return float(np.mean((y >= lo) & (y <= hi))) if y.size else None


def pit_bounds(samples: FloatArr, y: FloatArr) -> tuple[FloatArr, FloatArr]:
    """``F(y−1)`` and ``F(y)`` of each column of ``samples [n, D]`` at ``y [D]``."""
    lo = np.mean(samples < y[None, :], axis=0)
    hi = np.mean(samples <= y[None, :], axis=0)
    return np.asarray(lo, dtype=np.float64), np.asarray(hi, dtype=np.float64)


def randomized_pit(samples: FloatArr, y: FloatArr, rng: np.random.Generator) -> FloatArr:
    """Randomised PIT (Czado, Gneiting & Held 2009) for count data."""
    lo, hi = pit_bounds(samples, y)
    v = rng.uniform(size=lo.shape)
    return np.asarray(lo + v * (hi - lo), dtype=np.float64)


def pit_coverage(u: FloatArr, level: float = 0.8) -> float | None:
    """Share of PIT values inside the central ``level`` interval."""
    if u.size == 0:
        return None
    a = (1 - level) / 2
    return float(np.mean((u >= a) & (u <= 1 - a)))


def floor_stats(samples: FloatArr, p50: FloatArr) -> tuple[FloatArr, FloatArr]:
    """Per column: ``E|Y − p50|`` and ``E[Y]`` under the predictive samples ``[n, D]``."""
    mae = np.mean(np.abs(samples - p50[None, :]), axis=0)
    return np.asarray(mae, dtype=np.float64), np.asarray(samples.mean(axis=0), dtype=np.float64)


def wape_floor(floor_mae: FloatArr, floor_mean: FloatArr) -> float | None:
    s = float(np.sum(floor_mean))
    return float(np.sum(floor_mae) / s) if s > 0 else None


@dataclass
class ScoreRow:
    model_id: str
    backtest: str
    demand_group: str
    context: str
    n_skus: int
    n_sku_days: int
    wape: float | None
    bias: float | None
    pinball_10: float | None
    pinball_50: float | None
    pinball_90: float | None
    coverage: float | None  # randomised-PIT coverage of the 80 % interval
    is_champion: bool = False
    wape_floor: float | None = None
    coverage_interval: float | None = None  # old y ∈ [p10, p90] (reports only)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def key_metric(self) -> float:
        """WAPE when defined, else pinball_50 (sparse groups)."""
        if self.wape is not None:
            return self.wape
        return self.pinball_50 if self.pinball_50 is not None else float("inf")


DAY_COLUMNS = (
    "model_id",
    "backtest",
    "sku_id",
    "demand_group",
    "context",
    "day",
    "y",
    "p10",
    "p50",
    "p90",
    "mean",
    "wape_eligible",
    "pb10",
    "pb50",
    "pb90",
    "n_hours",
    "pit_u",
    "floor_mae",
    "floor_mean",
)


def aggregate(
    days: pd.DataFrame, backtest: str, group: str, context: str, model_id: str
) -> ScoreRow:
    """Aggregate per-(SKU, day) records (columns ``DAY_COLUMNS``) into one score row.
    SKU-days without any in-stock hour carry no information and are dropped."""
    days = days[days["n_hours"] > 0]
    el = days[days["wape_eligible"]]
    y, p50 = el["y"].to_numpy(float), el["p50"].to_numpy(float)
    nh = float(days["n_hours"].sum())
    pb = {q: (float(days[f"pb{int(q * 100)}"].sum()) / nh if nh > 0 else None) for q in QUANTILES}
    return ScoreRow(
        model_id=model_id,
        backtest=backtest,
        demand_group=group,
        context=context,
        n_skus=int(days["sku_id"].nunique()) if len(days) else 0,
        n_sku_days=len(days),
        wape=wape(y, p50) if len(el) else None,
        bias=bias(y, p50) if len(el) else None,
        pinball_10=pb[0.1],
        pinball_50=pb[0.5],
        pinball_90=pb[0.9],
        coverage=pit_coverage(days["pit_u"].to_numpy(float)) if len(days) else None,
        wape_floor=wape_floor(el["floor_mae"].to_numpy(float), el["floor_mean"].to_numpy(float))
        if len(el)
        else None,
        coverage_interval=coverage(
            days["y"].to_numpy(float), days["p10"].to_numpy(float), days["p90"].to_numpy(float)
        ),
    )


def score_table(days: pd.DataFrame, *, by_group: bool = True) -> list[ScoreRow]:
    """Rows per (model, backtest, group, context); ``by_group=False`` pools the shop."""
    rows: list[ScoreRow] = []
    keys = ["model_id", "backtest", "context"] + (["demand_group"] if by_group else [])
    for key, part in days.groupby(keys, sort=True):
        k = dict(zip(keys, key if isinstance(key, tuple) else (key,), strict=True))
        rows.append(
            aggregate(
                part,
                str(k["backtest"]),
                str(k.get("demand_group", "all")),
                str(k["context"]),
                str(k["model_id"]),
            )
        )
    return rows
