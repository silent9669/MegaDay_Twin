"""Campaign × hour dataset for the iROAS model and the exclusion rules (ADS-01, plan 04 §4).

Input: ``hourly_panel`` (SKU × hour; spend already split per SKU, stock flags, ads status),
``campaigns`` / ``campaign_skus``, and the platform-attributed numbers of ``ad_hourly``
(display only). The panel is read with a narrow SQL select instead of ``load_panel``: only
13 columns are needed and the full panel of ``demo`` is 864k rows (01 §7 allows the panel
DataFrame to cross into ``ads_effect``).

Per campaign, on the contiguous hour grid from the campaign start to the end of the panel:

* ``y`` = Σ units ordered (``units_gross + units_cancelled``: what the buyers ordered, as
  the platform counts it for its own ROAS) of the campaign's SKUs (a combo counts its combo
  orders) [DEV: 04 §5.1 says ``units_gross``; decision log M3-ads B1],
* ``revenue`` = Σ ``units · eff_price`` (``list_price`` when no price yet),
* ``spend`` = Σ ``ad_spend_alloc`` (NaN when ``ad_status='unknown'``),
* ``ctx`` = context of the hour-of-day rhythm: weekday 0..6 or 7 for double days.

Exclusion rules (04 §4), each hour gets the first reason that applies:

1. ``unknown``: ``ad_status='unknown'`` (ads report missing / late) and the
   ``CARRY_HOURS`` after such a run (the adstock of those hours is unknown) [DEV: the
   6-hour tail is not in the plan; decision log M3-ads A2].
2. ``launch``: the first 3 days after the campaign starts or one of its main SKUs launches.
3. ``stock_pause``: a run of ``paused`` hours with a main SKU ``low_stock`` (or out of
   stock) in the 6 hours before it starts, plus the 6 hours after the run.
4. ``low_stock``: a main SKU is ``stockout`` or ``low_stock`` in that hour.

"Main SKU" = a SKU with ≥ 30 % of the campaign's units over the period (04 §4 rule 2).
The rules can be switched off (``apply_stock_rules=False``) for the "no de-confounding"
run of the evaluation (04 §9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import duckdb
import numpy as np
import numpy.typing as npt
import pandas as pd

from app.ads_effect.schemas import EXCLUSION_REASONS

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]
BoolArray = npt.NDArray[np.bool_]

MAIN_SKU_SHARE = 0.30  # 04 §4 rule 2
STOCK_PAUSE_LOOKBACK = 6  # hours before a pause run (rule 3)
STOCK_PAUSE_TAIL = 6  # hours after a stock pause (rule 3)
CARRY_HOURS = 6  # hours after an unknown run (adstock unknown)
LAUNCH_DAYS = 3  # rule 4
CTX_DOUBLE_DAY = 7  # context index of double days (weekdays are 0..6)
N_CTX = 8

_PANEL_SQL = """
SELECT p.sku_id, p.hour_utc, p.hour_local, p.dow_local, p.day_local, p.is_double_day,
       p.units_gross + p.units_cancelled AS units_ordered,
       coalesce(p.eff_price, p.list_price, 0) AS price,
       p.stock_begin, p.stockout, p.low_stock, p.campaign_id, p.ad_status,
       p.ad_spend_alloc, p.ad_alloc_share
FROM hourly_panel p
WHERE p.campaign_id IN (SELECT unnest(?))
ORDER BY p.campaign_id, p.sku_id, p.hour_utc
"""


@dataclass
class CampaignHours:
    """Hourly series of one campaign (arrays share the time axis)."""

    campaign_id: str
    hours: pd.DatetimeIndex  # UTC, contiguous
    day: IntArray  # 0-based day index (local days)
    hour_local: IntArray
    ctx: IntArray  # 0..6 weekday, 7 double day
    y: FloatArray  # units
    revenue: FloatArray  # VND
    spend: FloatArray  # VND, NaN when unknown
    status: npt.NDArray[np.str_]
    keep: BoolArray  # usable for the fit
    reason: npt.NDArray[np.str_]  # '' when kept, else one of EXCLUSION_REASONS
    sku_ids: list[str]
    sku_units: FloatArray  # [n_sku] units over the period
    sku_revenue: FloatArray  # [n_sku] revenue (VND) over the period
    sku_shares: dict[str, float]  # latest spend share per SKU (sum 1)
    attributed_revenue: float = 0.0
    panel_version: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def spend0(self) -> FloatArray:
        """Spend with unknown hours set to 0 (for the adstock recursion)."""
        return np.nan_to_num(self.spend, nan=0.0)

    @property
    def off(self) -> BoolArray:
        """Hours without spend (paused / budget exhausted)."""
        return np.isin(self.status, ["paused", "budget_exhausted"]) & (self.spend0 <= 0.0)

    def excluded_hours(self) -> dict[str, int]:
        return {r: int((self.reason == r).sum()) for r in EXCLUSION_REASONS}

    @property
    def n_clean_off_hours(self) -> int:
        return int((self.keep & self.off).sum())


# ---------------------------------------------------------------------- helpers
def _runs(mask: BoolArray) -> list[tuple[int, int]]:
    """``[(start, end)]`` (end exclusive) of the True runs of ``mask``."""
    if mask.size == 0:
        return []
    m = np.concatenate(([False], mask, [False]))
    d = np.diff(m.astype(np.int8))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def _dilate_after(mask: BoolArray, n: int) -> BoolArray:
    """``mask`` plus the ``n`` hours following each True run."""
    out = mask.copy()
    for _, e in _runs(mask):
        out[e : e + n] = True
    return out


def exclusion_reasons(
    *,
    status: npt.NDArray[np.str_],
    main_low: BoolArray,
    launch: BoolArray,
    apply_stock_rules: bool = True,
) -> npt.NDArray[np.str_]:
    """Pure rule engine (04 §4). Inputs share the time axis:

    * ``status``: campaign ads status per hour,
    * ``main_low``: a main SKU is ``stockout`` or ``low_stock``,
    * ``launch``: inside the first 3 days of the campaign / a main SKU.

    Returns ``''`` for kept hours, else the first reason in ``EXCLUSION_REASONS`` order.
    """
    n = status.size
    unknown = _dilate_after(status == "unknown", CARRY_HOURS)
    stock_pause = np.zeros(n, dtype=bool)
    if apply_stock_rules:
        paused = status == "paused"
        for s, e in _runs(paused):
            lo = max(0, s - STOCK_PAUSE_LOOKBACK)
            if bool(main_low[lo : s + 1].any()):
                stock_pause[s : min(n, e + STOCK_PAUSE_TAIL)] = True
    low = main_low if apply_stock_rules else np.zeros(n, dtype=bool)
    reason = np.full(n, "", dtype="<U12")
    for name, m in (
        ("low_stock", low),
        ("stock_pause", stock_pause),
        ("launch", launch),
        ("unknown", unknown),
    ):  # reverse priority: the last assignment wins
        reason[m] = name
    return reason


def _list_campaigns(conn: duckdb.DuckDBPyConnection) -> dict[str, pd.Timestamp | None]:
    rows = conn.execute("SELECT campaign_id, started_at FROM campaigns ORDER BY 1").fetchall()
    return {str(r[0]): (None if r[1] is None else pd.Timestamp(r[1])) for r in rows}


def _platform_attributed(conn: duckdb.DuckDBPyConnection) -> dict[str, tuple[float, float]]:
    rows = conn.execute(
        "SELECT campaign_id, coalesce(sum(attributed_revenue), 0), coalesce(sum(spend), 0) "
        "FROM ad_hourly GROUP BY campaign_id"
    ).fetchall()
    return {str(r[0]): (float(r[1]), float(r[2])) for r in rows}


def _panel_version(conn: duckdb.DuckDBPyConnection) -> str:
    rows = conn.execute("SELECT max(panel_version) FROM hourly_panel").fetchall()
    return str(rows[0][0]) if rows and rows[0][0] is not None else ""


def build_campaign_hours(
    sku_frame: pd.DataFrame,
    campaign_id: str,
    started_at: datetime | None,
    *,
    apply_stock_rules: bool = True,
) -> CampaignHours | None:
    """Aggregate the panel rows of one campaign (all its SKUs) and apply the rules."""
    df = sku_frame
    if started_at is not None:
        df = df[df["hour_utc"] >= pd.Timestamp(started_at)]
    if df.empty:
        return None
    hours = pd.DatetimeIndex(np.sort(df["hour_utc"].unique())).tz_convert("UTC")
    sku_ids = sorted(df["sku_id"].unique().tolist())
    n_t, n_s = len(hours), len(sku_ids)
    t_idx = hours.get_indexer(pd.DatetimeIndex(df["hour_utc"]))
    s_idx = pd.Index(sku_ids).get_indexer(df["sku_id"])

    def mat(col: str, fill: float = 0.0) -> FloatArray:
        out = np.full((n_s, n_t), fill, dtype=np.float64)
        out[s_idx, t_idx] = df[col].to_numpy(dtype=np.float64, na_value=np.nan)
        return out

    units = mat("units_ordered")
    price = mat("price")
    stockout = mat("stockout").astype(bool)
    low_stock = mat("low_stock").astype(bool)
    stock_begin = mat("stock_begin", np.nan)
    spend_sku = mat("ad_spend_alloc", np.nan)

    first = df.drop_duplicates("hour_utc").set_index("hour_utc").reindex(hours)
    status = first["ad_status"].fillna("unknown").to_numpy(dtype=str)
    hour_local = first["hour_local"].to_numpy(dtype=np.int64)
    dow = first["dow_local"].to_numpy(dtype=np.int64)
    dd = first["is_double_day"].to_numpy(dtype=bool)
    ctx = np.where(dd, CTX_DOUBLE_DAY, dow).astype(np.int64)
    day_codes, _ = pd.factorize(first["day_local"], sort=True)
    day = np.asarray(day_codes, dtype=np.int64)

    spend = np.where(status == "unknown", np.nan, np.nansum(spend_sku, axis=0))
    y = units.sum(axis=0)
    revenue = (units * np.nan_to_num(price)).sum(axis=0)

    sku_units = units.sum(axis=1)
    total = float(sku_units.sum())
    shares = sku_units / total if total > 0 else np.full(n_s, 1.0 / n_s)
    main = shares >= MAIN_SKU_SHARE
    if not main.any():
        main = shares == shares.max()
    main_low = (stockout[main] | low_stock[main]).any(axis=0)

    # Launch: the campaign start (the series starts there), and the first hour a main SKU
    # has stock or sales when that is later than the first day of the series.
    launch = np.zeros(n_t, dtype=bool)
    launch[: LAUNCH_DAYS * 24] = True
    for i in np.flatnonzero(main):
        alive = (np.nan_to_num(stock_begin[i]) > 0) | (units[i] > 0)
        if alive.any():
            f = int(np.argmax(alive))
            if f > 24:  # present from the start → not a launch
                launch[f : f + LAUNCH_DAYS * 24] = True

    reason = exclusion_reasons(
        status=status, main_low=main_low, launch=launch, apply_stock_rules=apply_stock_rules
    )
    keep = (reason == "") & (status != "none")

    # Latest spend share per SKU (the panel's allocation share of the last hour that has one).
    alloc = mat("ad_alloc_share", np.nan)
    last = np.full(n_s, np.nan)
    for j in range(n_s):
        ok = np.flatnonzero(np.isfinite(alloc[j]))
        if ok.size:
            last[j] = alloc[j, ok[-1]]
    if np.isfinite(last).any() and np.nansum(last) > 0:
        last = np.nan_to_num(last) / np.nansum(last)
    else:
        last = shares
    sku_shares = {s: float(v) for s, v in zip(sku_ids, last, strict=True)}

    return CampaignHours(
        campaign_id=campaign_id,
        hours=hours,
        day=day,
        hour_local=hour_local,
        ctx=ctx,
        y=y,
        revenue=revenue,
        spend=spend.astype(np.float64),
        status=status,
        keep=keep,
        reason=reason,
        sku_ids=sku_ids,
        sku_units=sku_units,
        sku_revenue=(units * np.nan_to_num(price)).sum(axis=1),
        sku_shares=sku_shares,
    )


def load_campaign_hours(
    conn: duckdb.DuckDBPyConnection,
    campaign_ids: list[str] | None = None,
    *,
    apply_stock_rules: bool = True,
) -> list[CampaignHours]:
    """Every campaign (or ``campaign_ids``) as :class:`CampaignHours`, rules applied."""
    started = _list_campaigns(conn)
    if campaign_ids is not None:
        started = {k: v for k, v in started.items() if k in set(campaign_ids)}
    if not started:
        return []
    panel = conn.execute(_PANEL_SQL, [list(started)]).df()
    if panel.empty:
        return []
    panel["hour_utc"] = pd.DatetimeIndex(panel["hour_utc"]).tz_convert("UTC")
    attributed = _platform_attributed(conn)
    version = _panel_version(conn)
    out: list[CampaignHours] = []
    for cid, frame in panel.groupby("campaign_id", sort=True):
        ch = build_campaign_hours(
            frame, str(cid), started.get(str(cid)), apply_stock_rules=apply_stock_rules
        )
        if ch is None:
            continue
        ch.attributed_revenue = attributed.get(str(cid), (0.0, 0.0))[0]
        ch.panel_version = version
        out.append(ch)
    return out


# ---------------------------------------------------------------------- hour rhythm (ADS-02)
_RHYTHM_SQL = """
WITH d AS (
  SELECT sku_id, day_local, bool_or(stockout) AS any_out
  FROM hourly_panel WHERE campaign_id IS NULL GROUP BY 1, 2
)
SELECT CASE WHEN p.is_double_day THEN 7 ELSE p.dow_local END AS ctx, p.hour_local AS h,
       sum(p.units_gross + p.units_cancelled)::DOUBLE AS units
FROM hourly_panel p JOIN d USING (sku_id, day_local)
WHERE p.campaign_id IS NULL AND NOT d.any_out
GROUP BY 1, 2
"""

_RHYTHM_ALL_SQL = """
SELECT CASE WHEN is_double_day THEN 7 ELSE dow_local END AS ctx, hour_local AS h,
       sum(units_gross + units_cancelled)::DOUBLE AS units
FROM hourly_panel WHERE NOT stockout GROUP BY 1, 2
"""

RHYTHM_MIN_UNITS = 300.0  # fewer units per context → fall back / borrow from the pooled profile
RHYTHM_SHRINK_UNITS = 5000.0  # pseudo-units of the weekday shape mixed into the double-day shape
WEEKDAY_TEST_P = 0.01  # weekday shapes are kept apart only if homogeneity is rejected


@dataclass
class HourRhythm:
    """``B[ctx, h]`` (mean 1 over the 24 hours of each context) and where it came from."""

    table: FloatArray  # [N_CTX, 24]
    source: str  # "no_ads_skus" | "all_skus"
    units: float
    weekday_p: float | None = None  # χ² homogeneity p-value of the 7 weekday shapes
    pooled_weekdays: bool = True

    def at(self, ctx: IntArray, hour_local: IntArray) -> FloatArray:
        out: FloatArray = self.table[ctx, hour_local]
        return out


def _profile(rows: list[tuple[Any, ...]]) -> tuple[FloatArray, FloatArray]:
    counts = np.zeros((N_CTX, 24))
    for ctx, h, u in rows:
        counts[int(ctx), int(h)] += float(u or 0.0)
    return counts, counts.sum(axis=1)


def weekday_homogeneity_p(counts: FloatArray) -> float | None:
    """χ² test that the 7 weekday rows of ``counts`` share one hour shape."""
    from scipy.special import chdtrc  # light import (scipy.stats takes ~1.5 s)

    wk = counts[:7]
    wk = wk[wk.sum(axis=1) > 0]
    wk = wk[:, wk.sum(axis=0) > 0]
    if wk.shape[0] < 2 or wk.shape[1] < 2:
        return None
    expected = np.outer(wk.sum(axis=1), wk.sum(axis=0)) / wk.sum()
    stat = float(((wk - expected) ** 2 / expected).sum())
    dof = (wk.shape[0] - 1) * (wk.shape[1] - 1)
    return float(chdtrc(dof, stat))


def rhythm_from_counts(counts: FloatArray) -> tuple[FloatArray, float | None, bool]:
    """``B[ctx, h]`` from unit counts, mean 1 per context.

    * Weekdays (ctx 0..6) share one shape unless the χ² homogeneity test rejects it at
      ``WEEKDAY_TEST_P``: a weekday's level is absorbed by the day effect anyway, and 7
      separate shapes multiply the noise by ~2.6 (decision log M3-ads B2).
    * Double days (ctx 7) keep their own shape (it differs: midnight and noon peaks),
      shrunk toward the weekday shape with ``RHYTHM_SHRINK_UNITS`` pseudo-units, or
      mostly borrowed when the context has fewer than ``RHYTHM_MIN_UNITS`` units.
    """
    tot = counts.sum(axis=1)
    pooled = counts[:7].sum(axis=0)
    if pooled.sum() <= 0:
        pooled = counts.sum(axis=0)
    pooled = (pooled + 0.5) / (pooled + 0.5).sum()
    p = weekday_homogeneity_p(counts)
    pool = p is None or p >= WEEKDAY_TEST_P
    table = np.empty_like(counts)
    for c in range(N_CTX):
        if c != CTX_DOUBLE_DAY and pool:
            mix = pooled.copy()
        else:
            k = RHYTHM_SHRINK_UNITS * (10.0 if tot[c] < RHYTHM_MIN_UNITS else 1.0)
            mix = counts[c] + k * pooled
        table[c] = 24.0 * mix / mix.sum()
    return table, p, pool


def estimate_rhythm(conn: duckdb.DuckDBPyConnection) -> HourRhythm:
    """Hour-of-day × weekday rhythm (double days apart) from SKUs **without** ads (04 §5.1,
    ADS-02), on SKU-days with no stockout. Falls back to every SKU (non-stockout hours)
    when the shop has too few SKUs without ads."""
    counts, tot = _profile(conn.execute(_RHYTHM_SQL).fetchall())
    source = "no_ads_skus"
    if tot[:7].sum() < 7 * RHYTHM_MIN_UNITS:
        counts, tot = _profile(conn.execute(_RHYTHM_ALL_SQL).fetchall())
        source = "all_skus"
    table, p, pooled = rhythm_from_counts(counts)
    return HourRhythm(
        table=table, source=source, units=float(tot.sum()), weekday_p=p, pooled_weekdays=pooled
    )
