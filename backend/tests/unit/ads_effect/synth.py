"""Synthetic campaign panels with known ads parameters (seeded).

``make_panel`` returns the SKU × hour rows that ``dataset.build_campaign_hours`` reads, for
one campaign with a single SKU, generated from exactly the model of plan 04 §5.1:

    base_t = level_d · B[h] · base_per_day / 24,   B = HOUR_PROFILE · 24
    A_t    = spend_t + δ · A_{t−1},   g_t = 1 + a · (1 − exp(−A_t / (κ · s̄)))
    units  ~ Poisson(base_t · g_t)

Spend varies inside the day like on the platform: a daily budget that runs out in the
evening (budget_exhausted), optional night pauses (00–06h) and a few pauses of 6–48 h.
``stock_trap=True`` adds the confounder of 04 §4: every ~9 days the SKU runs low, the
seller pauses the ads and the SKU then sells (almost) nothing until restock.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.ads_effect.model import adstock

HOUR_PROFILE = np.array(
    [0.012, 0.007, 0.005, 0.004, 0.004, 0.006, 0.015, 0.03, 0.045, 0.055, 0.06, 0.065,
     0.065, 0.055, 0.05, 0.05, 0.05, 0.052, 0.055, 0.062, 0.07, 0.075, 0.068, 0.04]
)  # fmt: skip
B_TRUE = HOUR_PROFILE * 24
PRICE = 100_000


@dataclass
class Synth:
    frame: pd.DataFrame
    a: float
    kappa: float
    delta: float
    spend_ref: float
    true_iroas: float
    started_at: pd.Timestamp


def make_panel(
    seed: int,
    *,
    days: int = 60,
    base_per_day: float = 150.0,
    budget: float = 1_500_000.0,
    a: float = 0.6,
    kappa: float = 1.5,
    delta: float = 0.6,
    night_off: bool = True,
    n_episodes: int = 3,
    stock_trap: bool = False,
    sku: str = "SKU-A",
    campaign: str = "C1",
) -> Synth:
    rng = np.random.default_rng(seed)
    n = days * 24
    start = pd.Timestamp("2026-07-13 17:00", tz="UTC")  # 00:00 VN of 14/07
    hours = pd.date_range(start, periods=n, freq="h")
    local = hours.tz_convert("Asia/Ho_Chi_Minh")
    h = local.hour.to_numpy()
    d = np.repeat(np.arange(days), 24)
    level = np.exp(rng.normal(0.0, 0.25, days))

    paused = np.zeros(n, dtype=bool)
    if night_off:
        paused |= h < 6
    for _ in range(n_episodes if n > 14 * 24 else 0):
        s = int(rng.integers(7 * 24, n - 5 * 24))
        paused[s : s + int(rng.integers(6, 48))] = True

    low = np.zeros(n, dtype=bool)
    out = np.zeros(n, dtype=bool)
    if stock_trap:
        for s in range(10 * 24, n - 3 * 24, 9 * 24):
            s += int(rng.integers(0, 24))
            low[s - 3 : s + 30] = True  # running low; 3 h later the seller pauses the ads
            paused[s : s + 30] = True
            out[s + 6 : s + 30] = True  # shelf empty 6 h into the pause, until restock

    spend = np.zeros(n)
    status = np.full(n, "running", dtype=object)
    remaining = 0.0
    for t in range(n):
        if h[t] == 0:
            remaining = budget
        if paused[t]:
            status[t] = "paused"
            continue
        want = budget * HOUR_PROFILE[h[t]] * 1.3 * rng.uniform(0.8, 1.2)
        s = min(want, remaining)
        remaining -= s
        spend[t] = s
        if s <= 0:
            status[t] = "budget_exhausted"
    spend_ref = float(spend[spend > 0].mean())
    k = kappa * spend_ref
    adst = adstock(spend, delta)
    u = a * (1.0 - np.exp(-adst / k))
    base = level[d] * B_TRUE[h] * base_per_day / 24.0
    lam = base * (1.0 + u)
    units = rng.poisson(lam).astype(np.int64)
    if stock_trap:
        units[out] = 0
    inc = base * u * np.where(out, 0.0, 1.0)
    true_iroas = float((inc * PRICE).sum() / spend.sum())

    frame = pd.DataFrame(
        {
            "sku_id": sku,
            "hour_utc": hours,
            "hour_local": h.astype(np.int64),
            "dow_local": ((local.day_of_week.to_numpy() + 1) % 7).astype(np.int64),
            "day_local": local.date,
            "is_double_day": False,
            "units_ordered": units,
            "price": PRICE,
            "stock_begin": np.where(out, 0, 100),
            "stockout": out,
            "low_stock": low,
            "campaign_id": campaign,
            "ad_status": status.astype(str),
            "ad_spend_alloc": spend,
            "ad_alloc_share": 1.0,
        }
    )
    return Synth(frame, a, kappa, delta, spend_ref, true_iroas, start)
