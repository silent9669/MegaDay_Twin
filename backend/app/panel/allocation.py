"""Split campaign ad spend over its SKUs, hour by hour (PNL-03, plan 02 §6.2).

The ads report only has campaign numbers. For campaign ``c`` and hour ``t``::

    w_i(t)   = (units_gross_i in the 168 hours before t + 1) · [stockout_i(t) = false]
    share_i  = w_i / Σ_j w_j          (all SKUs stocked out → equal split: the platform
                                       still spends)
    ad_spend_alloc_i = spend_c(t) · share_i
    attributed_orders_alloc_i = attributed_orders_c(t) · share_i   (display only)

So Σ_i ad_spend_alloc_i = spend_c(t) exactly for every campaign-hour.

Ad status per hour:

* row in ``ad_hourly`` → its status; ``spend`` NULL (status ``unknown``) → alloc NULL;
* no row, hour before ``campaigns.started_at`` → ``none``, alloc 0;
* no row otherwise → ``unknown``, alloc NULL, flagged ``ADS_LAG`` after the last
  hour the report covers for that campaign, ``ADS_GAP_DAY`` inside a hole of >= 20
  hours, ``ADS_MISSING_HOUR`` for a shorter hole;
* SKU in no campaign → ``campaign_id`` NULL, ``none``, alloc 0, share NULL.

A SKU listed in several campaigns gets the sum of its allocations; ``campaign_id``,
``ad_status`` and ``ad_alloc_share`` are those of its first campaign (by id).

Windowed grids (PNL-04): when the grid starts after the beginning of history,
``prior_missing[c]`` is the number of consecutive missing hours of campaign ``c``
right before ``grid[0]``; a hole that crosses the window start is classified on its
full length, so the flags match a build over the whole history.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd

FloatArr = npt.NDArray[np.float64]
ObjArr = npt.NDArray[np.object_]

WINDOW_HOURS = 168
GAP_DAY_MIN_HOURS = 20  # 02 §3.1 ADS_GAP_DAY: >= 20 consecutive hours missing

FLAG_NONE = 0
FLAG_GAP_DAY = 1
FLAG_MISSING_HOUR = 2
FLAG_LAG = 3


@dataclass
class CampaignHours:
    """One campaign aligned on the panel grid."""

    status: ObjArr
    spend: FloatArr  # NaN = unknown
    attributed: FloatArr
    flag: npt.NDArray[np.int8]


@dataclass
class Allocation:
    """Per-SKU arrays of shape (n_skus, n_hours)."""

    campaign_id: ObjArr
    status: ObjArr
    spend: FloatArr
    share: FloatArr
    attributed: FloatArr
    flag: npt.NDArray[np.int8]
    shares_by_campaign: dict[str, tuple[list[int], FloatArr]]
    campaign_hours: dict[str, CampaignHours]


def trailing_sum(units: npt.NDArray[np.int64], window: int = WINDOW_HOURS) -> FloatArr:
    """Σ units over the ``window`` hours strictly before each hour (rows = SKUs)."""
    c = np.zeros((units.shape[0], units.shape[1] + 1), dtype=np.float64)
    np.cumsum(units, axis=1, out=c[:, 1:])
    idx = np.arange(units.shape[1])
    lo = np.maximum(idx - window, 0)
    return np.asarray(c[:, idx] - c[:, lo], dtype=np.float64)


def _align_campaign(
    grid: pd.DatetimeIndex,
    rows: pd.DataFrame,
    started_at: pd.Timestamp | None,
    prior_missing: int = 0,
) -> CampaignHours:
    n = len(grid)
    status = np.full(n, None, dtype=object)
    spend = np.full(n, np.nan)
    attributed = np.full(n, np.nan)
    flag = np.zeros(n, dtype=np.int8)
    have = np.zeros(n, dtype=bool)
    if not rows.empty:
        pos = grid.get_indexer(pd.DatetimeIndex(rows["hour_utc"]))
        ok = pos >= 0
        p = pos[ok]
        r = rows[ok]
        status[p] = r["status"].to_numpy(dtype=object)
        sp = pd.to_numeric(r["spend"], errors="coerce").to_numpy(dtype=float)
        spend[p] = sp
        attributed[p] = pd.to_numeric(r["attributed_orders"], errors="coerce").to_numpy(dtype=float)
        have[p] = True
        unknown_rows = (status[p] == "unknown") | np.isnan(sp)
        spend[p[unknown_rows]] = np.nan
        attributed[p[unknown_rows]] = np.nan
        status[p[unknown_rows]] = "unknown"
    before = (
        np.asarray(grid < started_at, dtype=bool)
        if started_at is not None and not pd.isna(started_at)
        else np.zeros(n, dtype=bool)
    )
    none_mask = ~have & before
    status[none_mask] = "none"
    spend[none_mask] = 0.0
    attributed[none_mask] = 0.0
    missing = ~have & ~before
    status[missing] = "unknown"
    last = int(np.nonzero(have)[0].max()) if have.any() else -1
    if missing.any():
        idx = np.nonzero(missing)[0]
        # run-length of consecutive missing hours
        breaks = np.nonzero(np.diff(idx) != 1)[0]
        starts = np.concatenate([[0], breaks + 1])
        ends = np.concatenate([breaks + 1, [len(idx)]])
        for s, e in zip(starts, ends, strict=True):
            run = idx[s:e]
            length = len(run) + (prior_missing if run[0] == 0 else 0)
            if run[0] > last:
                flag[run] = FLAG_LAG
            elif length >= GAP_DAY_MIN_HOURS:
                flag[run] = FLAG_GAP_DAY
            else:
                flag[run] = FLAG_MISSING_HOUR
    return CampaignHours(status=status, spend=spend, attributed=attributed, flag=flag)


def allocate_ad_spend(
    grid: pd.DatetimeIndex,
    sku_ids: list[str],
    units_gross: npt.NDArray[np.int64],
    stockout: npt.NDArray[np.bool_],
    campaign_skus: dict[str, list[str]],
    ad_hourly: pd.DataFrame,
    started_at: dict[str, pd.Timestamp | None],
    prior_missing: dict[str, int] | None = None,
) -> Allocation:
    """``units_gross``/``stockout``: (n_skus, n_hours) in ``sku_ids`` order on ``grid``.
    ``ad_hourly``: columns campaign_id, hour_utc, status, spend, attributed_orders.
    ``prior_missing``: see module docstring (windowed grids only)."""
    prior = prior_missing or {}
    n_sku, n = units_gross.shape
    pos = {s: i for i, s in enumerate(sku_ids)}
    weights = (trailing_sum(units_gross) + 1.0) * (~stockout)

    camp_id = np.full((n_sku, n), None, dtype=object)
    status = np.full((n_sku, n), "none", dtype=object)
    spend = np.zeros((n_sku, n))
    share_out = np.full((n_sku, n), np.nan)
    attributed = np.zeros((n_sku, n))
    flag = np.zeros((n_sku, n), dtype=np.int8)
    primary_done = np.zeros(n_sku, dtype=bool)

    by_campaign = {str(k): g for k, g in ad_hourly.groupby("campaign_id", sort=False)}
    shares_by_campaign: dict[str, tuple[list[int], FloatArr]] = {}
    hours_by_campaign: dict[str, CampaignHours] = {}
    for cid in sorted(campaign_skus):
        members = [pos[s] for s in campaign_skus[cid] if s in pos]
        if not members:
            continue
        ch = _align_campaign(
            grid,
            by_campaign.get(cid, ad_hourly.iloc[0:0]),
            started_at.get(cid),
            prior.get(cid, 0),
        )
        hours_by_campaign[cid] = ch
        w = weights[members]
        tot = w.sum(axis=0)
        share = np.where(tot > 0, w / np.where(tot > 0, tot, 1.0), 1.0 / len(members))
        shares_by_campaign[cid] = (members, share)
        alloc = share * ch.spend  # NaN propagates for unknown hours
        attr = share * ch.attributed
        for j, i in enumerate(members):
            spend[i] = spend[i] + alloc[j]
            attributed[i] = attributed[i] + attr[j]
            if not primary_done[i]:
                primary_done[i] = True
                camp_id[i] = cid
                status[i] = ch.status
                share_out[i] = share[j]
                flag[i] = ch.flag
    return Allocation(
        campaign_id=camp_id,
        status=status,
        spend=spend,
        share=share_out,
        attributed=attributed,
        flag=flag,
        shares_by_campaign=shares_by_campaign,
        campaign_hours=hours_by_campaign,
    )
