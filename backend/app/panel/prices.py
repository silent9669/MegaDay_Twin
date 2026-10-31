"""Effective price per SKU × VN day → ``price_events`` (plan 02 §6.3, PNL-01).

* Day with non-cancelled orders: ``eff_price`` = median ``unit_price``, ``list_price``
  = median ``list_price`` of those orders (``source='orders'``).
* Day without orders: carry the last known prices forward (``carried_forward``).
* Day with a ``committed_price`` sale commitment (11.11): the committed price wins
  (``commitment``); list price is the carried one.
* Before the first order of a SKU there is no row (eff price unknown).

``discount_pct = 1 − eff/list`` clipped to [0, 1] (a price above list = no discount).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

PRICE_COLUMNS = ["sku_id", "day_local", "list_price", "eff_price", "discount_pct", "source"]


def build_price_events(
    observed: pd.DataFrame,
    days: list[date],
    commitments: pd.DataFrame | None = None,
    fallback_list_price: dict[str, int] | None = None,
) -> pd.DataFrame:
    """``observed``: columns sku_id, day_local (date), list_price, eff_price (medians of the
    day). ``commitments``: sku_id, day_local, value_int. Returns rows for ``days`` from each
    SKU's first known price on."""
    if not days:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    obs = observed.copy()
    obs["source"] = "orders"
    if commitments is not None and not commitments.empty:
        com = commitments.rename(columns={"value_int": "eff_price"})[
            ["sku_id", "day_local", "eff_price"]
        ].copy()
        com["list_price"] = np.nan
        com["source"] = "commitment"
        obs = pd.concat(
            [
                obs[
                    ~obs.set_index(["sku_id", "day_local"]).index.isin(
                        com.set_index(["sku_id", "day_local"]).index
                    )
                ],
                com,
            ],
            ignore_index=True,
        )
    if obs.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    day_list = sorted(set(days))
    obs = obs[obs["day_local"].isin(set(day_list))]
    if obs.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    fallback = fallback_list_price or {}
    # Vectorised over SKUs: SKU × day grid (sorted), forward fill inside each SKU, keep
    # the days from the SKU's first known price on.
    day_pos = {d: j for j, d in enumerate(day_list)}
    skus = sorted(obs["sku_id"].unique())
    sku_pos = {s: i for i, s in enumerate(skus)}
    nd = len(day_list)
    r = obs["sku_id"].map(sku_pos).to_numpy(dtype=np.int64)
    c = obs["day_local"].map(day_pos).to_numpy(dtype=np.int64)
    flat = r * nd + c
    size = len(skus) * nd
    eff = np.full(size, np.nan)
    lst = np.full(size, np.nan)
    src = np.full(size, None, dtype=object)
    eff[flat] = obs["eff_price"].astype(float).to_numpy()
    lst[flat] = obs["list_price"].astype(float).to_numpy()
    src[flat] = obs["source"].to_numpy(dtype=object)
    first = np.full(len(skus), nd, dtype=np.int64)
    np.minimum.at(first, r, c)
    sku_of = np.repeat(np.arange(len(skus)), nd)
    day_of = np.tile(np.arange(nd), len(skus))
    keep = day_of >= first[sku_of]
    f = pd.DataFrame({"g": sku_of, "eff_price": eff, "list_price": lst})
    g = f.groupby("g", sort=False)
    eff_f = g["eff_price"].ffill().to_numpy()
    lp_ff = pd.Series(np.where(keep, g["list_price"].ffill().to_numpy(), np.nan))
    lp = lp_ff.groupby(sku_of, sort=False).bfill().to_numpy()
    all_nan = np.asarray(~np.isfinite(lp).reshape(len(skus), nd).any(axis=1), dtype=bool)
    fb = np.array([float(fallback.get(str(s), np.nan)) for s in skus])
    lp = np.where(np.isnan(lp) & all_nan[sku_of], fb[sku_of], lp)
    lp = np.where(np.isnan(lp), eff_f, lp)
    src = np.where(pd.isna(src), "carried_forward", src)
    res = pd.DataFrame(
        {
            "sku_id": np.array(skus, dtype=object)[sku_of[keep]],
            "day_local": np.array(day_list, dtype=object)[day_of[keep]],
            "list_price": lp[keep],
            "eff_price": eff_f[keep],
            "source": src[keep],
        }
    )
    res["eff_price"] = res["eff_price"].round().astype("int64")
    res["list_price"] = res["list_price"].round().astype("int64")
    pos_lp = res["list_price"].where(res["list_price"] > 0)
    res["discount_pct"] = (1.0 - res["eff_price"] / pos_lp).clip(0.0, 1.0).fillna(0.0)
    return res[PRICE_COLUMNS]
