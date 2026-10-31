"""Build ``hourly_panel`` (SKU × hour) from the canonical tables (PNL-01, plan 02 §6).

Reads (names as in ``app/warehouse/schema.sql``):

* ``skus`` (catalogue, ``is_bundle``, ``list_price``), ``bundle_components``
* ``orders`` + ``order_lines`` → units per SKU × hour (cancelled split out) and the
  effective price per day (written to ``price_events``)
* ``inventory_snapshots`` (+ ``restock_receipts``) → hourly stock, stockout flags
  (``stock.py``)
* ``campaigns``, ``campaign_skus``, ``ad_hourly`` → spend split per SKU (``allocation.py``)
* ``sale_commitments`` (``flash_window``, ``committed_price``)
* ``ingest_batches`` → default end of the panel (``as_of``) and ``panel_version``

Writes ``hourly_panel`` (delete + insert of the rebuilt range, ordered by
``(sku_id, hour_utc)`` for DuckDB zone maps) and ``price_events``.

The hour grid logically starts at the VN midnight of the first order so stock walks
and the 7-day windows see the whole history; ``start``/``end`` only limit what is
written. ``sku_ids`` is closed over combos/components and campaign co-members
(their numbers depend on each other) and the closure is what gets written.

Window mode (PNL-04, default when ``start`` is given): only the hours that can
influence ``[start, end)`` are computed. The computed grid starts at the VN midnight
on or before the earliest of

* ``start − 168 h`` (trailing 7-day sums: ad shares, ``low_stock``);
* for each stocked SKU, its last valid snapshot at or before ``start`` that does not
  fall strictly inside the day of one of its receipts (the stock walk from that
  anchor on does not depend on anything earlier, and no receipt crosses it).

State that crosses the window start is carried in explicitly: the length of an ads
hole that started before the window (``ADS_GAP_DAY`` vs ``ADS_MISSING_HOUR``) and the
last known prices of each SKU (``carried_forward``). The rows written are therefore
identical to a build over the whole history (tested cell by cell). A SKU with valid
snapshots only after ``start`` falls back to the whole history.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import duckdb
import numpy as np
import numpy.typing as npt
import pandas as pd
from pydantic import BaseModel

from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.timeutil import VN_TZ, floor_hour, is_double_day, is_payday, utcnow
from app.panel import schema as ps
from app.panel.allocation import (
    FLAG_GAP_DAY,
    FLAG_LAG,
    FLAG_MISSING_HOUR,
    WINDOW_HOURS,
    Allocation,
    allocate_ad_spend,
    trailing_sum,
)
from app.panel.prices import build_price_events
from app.panel.stock import (
    CONF,
    DAY_HOURS,
    SkuStockInput,
    StockSeries,
    combo_stock,
    reconstruct_sku,
    stockout_flags,
)
from app.warehouse.repo import get_bundle_components, upsert_df

log = get_logger(__name__)

HOUR = pd.Timedelta(hours=1)
TET = date(2027, 2, 6)  # 02 §6: days_to_tet
GIFT_PEAKS = ((10, 20), (11, 20))  # 20/10 and 20/11
LOW_STOCK_DAYS = 2.0  # low_stock: stock < 2 × recent daily demand
FLAG_ORDER = (
    ps.FLAG_PROVISIONAL,
    ps.FLAG_ADS_GAP_DAY,
    ps.FLAG_ADS_MISSING_HOUR,
    ps.FLAG_ADS_LAG,
    ps.FLAG_STOCK_INTERPOLATED,
    ps.FLAG_STOCK_JUMP,
    ps.FLAG_NO_STOCK_DATA,
    ps.FLAG_BUNDLE_NO_STOCK_ROW,
)


class PanelBuildResult(BaseModel):
    rows_written: int
    n_skus: int
    start: datetime
    end: datetime
    panel_version: str
    price_rows: int
    duration_ms: int


@dataclass
class PanelFrames:
    panel: pd.DataFrame
    price_events: pd.DataFrame
    grid: pd.DatetimeIndex
    sku_ids: list[str]
    allocation: Allocation


# ----------------------------------------------------------------- helpers
def _scalar(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    rows = conn.execute(sql, params or []).fetchall()
    return rows[0][0] if rows else None


def _df(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
    return conn.execute(sql, params or []).df()


def _utc(ts: Any) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _vn_midnight(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.tz_convert(VN_TZ).normalize().tz_convert("UTC")


def _ceil_hour(ts: pd.Timestamp) -> pd.Timestamp:
    return ts.ceil("h")


def default_range(conn: duckdb.DuckDBPyConnection) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """Grid start = VN midnight of the first order; end = ceil-hour of the latest
    ``as_of`` of order/inventory batches (else one hour after the last order)."""
    first = _scalar(conn, "SELECT min(ordered_at) FROM orders")
    if first is None:
        return None
    start = _vn_midnight(_utc(first))
    as_of = _scalar(
        conn,
        "SELECT max(as_of) FROM ingest_batches "
        "WHERE source_type IN ('orders', 'inventory') AND status <> 'failed'",
    )
    if as_of is not None:
        end = _ceil_hour(_utc(as_of))
    else:
        last = _scalar(conn, "SELECT max(ordered_at) FROM orders")
        end = _utc(floor_hour(_utc(last).to_pydatetime())) + HOUR
    return start, end


def _days_to_gift_peak(d: date) -> int:
    cands = [date(y, m, dd) for y in (d.year - 1, d.year, d.year + 1) for m, dd in GIFT_PEAKS]
    best = min(cands, key=lambda p: (abs((p - d).days), (p - d).days < 0))
    return (best - d).days


def _closure(
    requested: list[str],
    all_skus: list[str],
    bundles: dict[str, list[tuple[str, int]]],
    campaign_skus: dict[str, list[str]],
) -> list[str]:
    known = set(all_skus)
    out = {s for s in requested if s in known}
    changed = True
    while changed:
        changed = False
        for b, comps in bundles.items():
            group = {b, *(c for c, _ in comps)}
            if out & group and not group <= out:
                out |= group & known
                changed = True
        for members in campaign_skus.values():
            group = set(members)
            if out & group and not group <= out:
                out |= group & known
                changed = True
    return sorted(out)


# ----------------------------------------------------------------- loading
def _load_units(
    conn: duckdb.DuckDBPyConnection, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    return _df(
        conn,
        """
        SELECT ol.sku_id,
               date_trunc('hour', o.ordered_at) AS hour_utc,
               COALESCE(SUM(ol.qty) FILTER (WHERE o.status <> 'cancelled'), 0) AS units_gross,
               COALESCE(SUM(ol.qty) FILTER (WHERE o.status <> 'cancelled'
                                             AND NOT o.status_final), 0) AS units_provisional,
               COALESCE(SUM(ol.qty) FILTER (WHERE o.status = 'cancelled'), 0) AS units_cancelled,
               COUNT(DISTINCT o.order_id) FILTER (WHERE o.status <> 'cancelled') AS orders_count,
               COALESCE(SUM(CAST(ol.unit_price AS BIGINT) * ol.qty)
                        FILTER (WHERE o.status NOT IN ('cancelled', 'returned')), 0)
                   AS revenue_net
        FROM orders o JOIN order_lines ol USING (order_id)
        WHERE o.ordered_at >= ? AND o.ordered_at < ?
        GROUP BY 1, 2
        """,
        [start.to_pydatetime(), end.to_pydatetime()],
    )


def _load_daily_prices(
    conn: duckdb.DuckDBPyConnection, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    df = _df(
        conn,
        """
        SELECT ol.sku_id,
               CAST(timezone('Asia/Ho_Chi_Minh', o.ordered_at) AS DATE) AS day_local,
               median(ol.list_price) AS list_price,
               median(ol.unit_price) AS eff_price
        FROM orders o JOIN order_lines ol USING (order_id)
        WHERE o.status <> 'cancelled' AND o.ordered_at >= ? AND o.ordered_at < ?
        GROUP BY 1, 2
        """,
        [start.to_pydatetime(), end.to_pydatetime()],
    )
    df["day_local"] = pd.to_datetime(df["day_local"]).dt.date
    return df


def _load_commitments(conn: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, pd.DataFrame]:
    prices = _df(
        conn,
        "SELECT sku_id, day_local, value_int FROM sale_commitments "
        "WHERE kind = 'committed_price' AND sku_id IS NOT NULL AND value_int IS NOT NULL",
    )
    prices["day_local"] = pd.to_datetime(prices["day_local"]).dt.date
    windows = _df(
        conn,
        "SELECT sku_id, window_start, window_end FROM sale_commitments "
        "WHERE kind = 'flash_window' AND window_start IS NOT NULL AND window_end IS NOT NULL",
    )
    return prices.drop_duplicates(["sku_id", "day_local"], keep="last"), windows


# ----------------------------------------------------------------- window (PNL-04)
def _window_start(
    full_start: pd.Timestamp,
    write_from: pd.Timestamp,
    g_end: pd.Timestamp,
    snaps: pd.DataFrame,
    rec: pd.DataFrame,
    stocked: list[str],
) -> pd.Timestamp:
    """First hour that must be computed so rows from ``write_from`` on equal a build over
    the whole history (see module docstring). ``full_start`` = no window possible."""
    s_idx = int((write_from - full_start) // HOUR)
    need = s_idx - WINDOW_HOURS
    if need <= 0:
        return full_start
    n_full = int((g_end - full_start) // HOUR)
    valid = snaps[snaps["on_hand"].notna() & snaps["sku_id"].isin(set(stocked))]
    if valid.empty:
        return max(full_start, _vn_midnight(full_start + need * HOUR))
    k = ((pd.DatetimeIndex(valid["snapshot_at"]).tz_convert("UTC") - full_start) / HOUR).to_numpy(
        dtype=float
    )
    on_grid = (np.floor(k) == k) & (k >= 0) & (k <= n_full)
    anc = pd.DataFrame({"sku_id": valid["sku_id"].to_numpy()[on_grid], "k": k[on_grid]})
    anc = anc.drop_duplicates()
    has_any = set(anc["sku_id"])
    cand = anc[anc["k"] <= s_idx]
    if not rec.empty and not cand.empty:
        mid = pd.DatetimeIndex(pd.to_datetime(rec["received_on"]).dt.tz_localize(VN_TZ)).tz_convert(
            "UTC"
        )
        ws = pd.DataFrame(
            {"sku_id": rec["sku_id"].to_numpy(), "ws": ((mid - full_start) // HOUR).to_numpy()}
        )
        both = cand.merge(ws, on="sku_id")
        inside = both[(both["ws"] < both["k"]) & (both["k"] < both["ws"] + DAY_HOURS)]
        if not inside.empty:  # a snapshot inside a receipt day cannot start the walk
            bad = pd.MultiIndex.from_frame(inside[["sku_id", "k"]].drop_duplicates())
            cand = cand[~pd.MultiIndex.from_frame(cand[["sku_id", "k"]]).isin(bad)]
    picked = cand.groupby("sku_id")["k"].max()
    if has_any - set(picked.index):  # snapshots only after start: the walk runs backwards
        return full_start
    if not picked.empty:
        need = min(need, int(picked.min()))
    if need <= 0:
        return full_start
    return max(full_start, _vn_midnight(full_start + need * HOUR))


def _ads_prior_missing(
    conn: duckdb.DuckDBPyConnection,
    full_start: pd.Timestamp,
    g_start: pd.Timestamp,
    campaigns: list[str],
    started: dict[str, pd.Timestamp | None],
) -> dict[str, int]:
    """Consecutive missing ads hours of each campaign right before ``g_start``."""
    if g_start <= full_start or not campaigns:
        return {}
    last = {
        str(c): _utc(h)
        for c, h in conn.execute(
            "SELECT campaign_id, max(hour_utc) FROM ad_hourly "
            "WHERE hour_utc >= ? AND hour_utc < ? GROUP BY 1",
            [full_start.to_pydatetime(), g_start.to_pydatetime()],
        ).fetchall()
    }
    out: dict[str, int] = {}
    for cid in campaigns:
        run_start = full_start
        if cid in last:
            run_start = max(run_start, last[cid] + HOUR)
        st = started.get(cid)
        if st is not None:
            run_start = max(run_start, _ceil_hour(st))
        out[cid] = max(0, int((g_start - run_start) // HOUR))
    return out


def _price_seed(
    conn: duckdb.DuckDBPyConnection,
    full_start: pd.Timestamp,
    g_start: pd.Timestamp,
    commit_prices: pd.DataFrame,
    pos: dict[str, int],
) -> pd.DataFrame:
    """One row per SKU on the day before the window: the prices carried into it
    (last effective price, last observed list price, NaN when none yet)."""
    cols = ["sku_id", "day_local", "list_price", "eff_price"]
    if g_start <= full_start:
        return pd.DataFrame(columns=cols)
    first_day = full_start.tz_convert(VN_TZ).date()
    d0 = g_start.tz_convert(VN_TZ).date()
    com = commit_prices[
        commit_prices["sku_id"].isin(pos)
        & (commit_prices["day_local"] >= first_day)
        & (commit_prices["day_local"] < d0)
    ]
    if com.empty:
        # Usual case: the carried prices are the medians of the last selling day
        # (order_lines prices are NOT NULL), no need to aggregate every day.
        seed = _df(
            conn,
            """
            WITH l AS (
                SELECT ol.sku_id,
                       CAST(timezone('Asia/Ho_Chi_Minh', o.ordered_at) AS DATE) AS d,
                       ol.list_price, ol.unit_price
                FROM orders o JOIN order_lines ol USING (order_id)
                WHERE o.status <> 'cancelled' AND o.ordered_at >= ? AND o.ordered_at < ?
            ), last AS (SELECT sku_id, max(d) AS d FROM l GROUP BY 1)
            SELECT l.sku_id, median(l.list_price) AS list_price,
                   median(l.unit_price) AS eff_price
            FROM l JOIN last USING (sku_id, d) GROUP BY 1 ORDER BY 1
            """,
            [full_start.to_pydatetime(), g_start.to_pydatetime()],
        )
        seed = seed[seed["sku_id"].isin(pos)].reset_index(drop=True)
        seed["day_local"] = d0 - timedelta(days=1)
        seed["list_price"] = seed["list_price"].astype(float)
        seed["eff_price"] = seed["eff_price"].astype(float)
        return seed[cols]
    obs = _load_daily_prices(conn, full_start, g_start)
    obs = obs[obs["sku_id"].isin(pos)]
    com_keys = set(zip(com["sku_id"], com["day_local"], strict=True))
    kept = obs[[k not in com_keys for k in zip(obs["sku_id"], obs["day_local"], strict=True)]]
    eff = pd.concat(
        [
            kept[["sku_id", "day_local", "eff_price"]],
            com.rename(columns={"value_int": "eff_price"})[["sku_id", "day_local", "eff_price"]],
        ],
        ignore_index=True,
    )
    eff["eff_price"] = eff["eff_price"].astype(float)
    eff = eff.dropna(subset=["eff_price"]).sort_values(["sku_id", "day_local"])
    if eff.empty:
        return pd.DataFrame(columns=cols)
    lst = kept.dropna(subset=["list_price"]).sort_values(["sku_id", "day_local"])
    seed = eff.groupby("sku_id", sort=True)["eff_price"].last().to_frame()
    seed["list_price"] = lst.groupby("sku_id")["list_price"].last().astype(float)
    seed["day_local"] = d0 - timedelta(days=1)
    return seed.reset_index()[cols]


# ----------------------------------------------------------------- compute
def compute_hourly_panel(
    conn: duckdb.DuckDBPyConnection,
    sku_ids: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    panel_version: str | None = None,
    window: bool = True,
) -> PanelFrames | None:
    """Compute the panel without writing. ``None`` when there are no orders yet.

    ``window`` (only with ``start``): compute just the hours that influence the rows
    written (module docstring); ``False`` walks the whole history (same result)."""
    rng = default_range(conn)
    if rng is None:
        return None
    g_start, g_end = rng
    if end is not None:
        g_end = _ceil_hour(_utc(end))
    if start is not None:
        g_start = min(g_start, _vn_midnight(_utc(start)))
    full_start = g_start
    write_from = _utc(floor_hour(_utc(start).to_pydatetime())) if start is not None else g_start

    sku_rows = _df(conn, "SELECT sku_id, is_bundle, list_price FROM skus ORDER BY sku_id")
    all_skus = [str(s) for s in sku_rows["sku_id"]]
    bundle_flag = dict(zip(all_skus, sku_rows["is_bundle"].astype(bool), strict=True))
    fallback_lp = {
        str(s): int(p)
        for s, p in zip(sku_rows["sku_id"], sku_rows["list_price"], strict=True)
        if pd.notna(p)
    }
    bundles = {
        b: [(c.sku_id, c.qty) for c in comps] for b, comps in get_bundle_components(conn).items()
    }
    cs = _df(conn, "SELECT campaign_id, sku_id FROM campaign_skus ORDER BY campaign_id, sku_id")
    campaign_skus: dict[str, list[str]] = {}
    for cid, sid in zip(cs["campaign_id"], cs["sku_id"], strict=True):
        campaign_skus.setdefault(str(cid), []).append(str(sid))
    skus = _closure(sku_ids, all_skus, bundles, campaign_skus) if sku_ids is not None else all_skus
    pos = {s: i for i, s in enumerate(skus)}
    m = len(skus)

    snaps = _df(conn, "SELECT sku_id, snapshot_at, on_hand FROM inventory_snapshots")
    rec = _df(
        conn,
        "SELECT sku_id, received_on, SUM(qty) AS qty FROM restock_receipts GROUP BY 1, 2",
    )
    if window and start is not None and write_from > g_start:
        stocked = [s for s in skus if not (s in bundles or bundle_flag.get(s, False))]
        g_start = _window_start(full_start, write_from, g_end, snaps, rec, stocked)
    grid = pd.date_range(g_start, g_end, freq="h", inclusive="left", tz="UTC")
    n = len(grid)

    # ---- units per SKU × hour
    def mat(dtype: Any = np.int64) -> npt.NDArray[Any]:
        return np.zeros((m, n), dtype=dtype)

    u_gross, u_prov, u_canc, n_orders, rev = mat(), mat(), mat(), mat(), mat()
    units = _load_units(conn, g_start, g_end)
    if not units.empty:
        units = units[units["sku_id"].isin(pos)]
        r = units["sku_id"].map(pos).to_numpy(dtype=np.int64)
        c = ((pd.DatetimeIndex(units["hour_utc"]).tz_convert("UTC") - g_start) // HOUR).to_numpy(
            dtype=np.int64
        )
        for arr, col in (
            (u_gross, "units_gross"),
            (u_prov, "units_provisional"),
            (u_canc, "units_cancelled"),
            (n_orders, "orders_count"),
            (rev, "revenue_net"),
        ):
            np.add.at(arr, (r, c), units[col].to_numpy(dtype=np.int64))
    taken = u_gross + u_canc

    # ---- stock
    anchors: dict[str, dict[int, int | None]] = {}
    if g_start > full_start and not snaps.empty:
        snaps = snaps[pd.DatetimeIndex(snaps["snapshot_at"]).tz_convert("UTC") >= g_start]
    if not snaps.empty:
        k = (pd.DatetimeIndex(snaps["snapshot_at"]).tz_convert("UTC") - g_start) / HOUR
        for sid, kk, v in zip(snaps["sku_id"], k, snaps["on_hand"], strict=True):
            if float(kk).is_integer() and 0 <= kk <= n:
                anchors.setdefault(str(sid), {})[int(kk)] = None if pd.isna(v) else int(v)
    receipts: dict[str, list[tuple[int, int]]] = {}
    if not rec.empty:
        mid = pd.DatetimeIndex(pd.to_datetime(rec["received_on"]).dt.tz_localize(VN_TZ)).tz_convert(
            "UTC"
        )
        ws = ((mid - g_start) // HOUR).to_numpy(dtype=np.int64)
        for sid, w, q in zip(rec["sku_id"], ws, rec["qty"], strict=True):
            receipts.setdefault(str(sid), []).append((int(w), int(q)))

    stock_cons = u_gross.copy()  # what leaves the shelf (own + combos)
    stock_need = taken.copy()
    for b, comps in bundles.items():
        if b not in pos:
            continue
        for comp, q in comps:
            if comp in pos:
                stock_cons[pos[comp]] += u_gross[pos[b]] * q
                stock_need[pos[comp]] += taken[pos[b]] * q

    begin = np.full((m, n), np.nan)
    end_ = np.full((m, n), np.nan)
    source = np.full((m, n), None, dtype=object)
    restock = mat()
    stockout = np.zeros((m, n), dtype=bool)
    bits = np.zeros((m, n), dtype=np.int64)
    bit = {f: 1 << i for i, f in enumerate(FLAG_ORDER)}
    series: dict[str, StockSeries] = {}
    for s in skus:
        i = pos[s]
        if s in bundles or bundle_flag.get(s, False):
            continue
        st = reconstruct_sku(
            SkuStockInput(
                consumed=stock_cons[i],
                need=stock_need[i],
                anchors=anchors.get(s, {}),
                receipts=receipts.get(s, []),
            )
        )
        series[s] = st
        if not st.known:
            bits[i] |= bit[ps.FLAG_NO_STOCK_DATA]
            continue
        begin[i], end_[i], source[i], restock[i] = st.begin, st.end, st.source, st.restock
        stockout[i] = stockout_flags(st.begin, taken[i])
        bits[i] |= np.where(st.interpolated, bit[ps.FLAG_STOCK_INTERPOLATED], 0)
        bits[i] |= np.where(st.jump, bit[ps.FLAG_STOCK_JUMP], 0)
    for s in skus:
        if not (s in bundles or bundle_flag.get(s, False)):
            continue
        i = pos[s]
        comps = bundles.get(s, [])
        parts = [(series[c], u_gross[pos[c]], q) for c, q in comps if c in series]
        if not comps or len(parts) != len(comps) or not all(p[0].known for p in parts):
            bits[i] |= bit[ps.FLAG_BUNDLE_NO_STOCK_ROW]
            continue
        cb = combo_stock(parts)
        begin[i], end_[i], source[i] = cb.begin, cb.end, cb.source
        stockout[i] = stockout_flags(cb.available, taken[i])
        for p in parts:
            bits[i] |= np.where(p[0].interpolated, bit[ps.FLAG_STOCK_INTERPOLATED], 0)
            bits[i] |= np.where(p[0].jump, bit[ps.FLAG_STOCK_JUMP], 0)

    conf = np.full((m, n), np.nan)
    for src, val in CONF.items():
        conf[source == src] = val
    daily_rate = trailing_sum(u_gross) / 7.0
    low_stock = np.isfinite(begin) & (
        np.nan_to_num(begin, nan=np.inf) < LOW_STOCK_DAYS * daily_rate
    )

    # ---- ads
    ads = _df(
        conn,
        "SELECT campaign_id, hour_utc, status, spend, attributed_orders FROM ad_hourly "
        "WHERE hour_utc >= ? AND hour_utc < ?",
        [g_start.to_pydatetime(), g_end.to_pydatetime()],
    )
    camp = _df(conn, "SELECT campaign_id, started_at FROM campaigns")
    started = {
        str(c): (None if pd.isna(t) else _utc(t))
        for c, t in zip(camp["campaign_id"], camp["started_at"], strict=True)
    }
    sel_campaigns = {c: [s for s in members if s in pos] for c, members in campaign_skus.items()}
    prior = _ads_prior_missing(
        conn, full_start, g_start, sorted(c for c, v in sel_campaigns.items() if v), started
    )
    alloc = allocate_ad_spend(grid, skus, u_gross, stockout, sel_campaigns, ads, started, prior)
    bits |= np.where(alloc.flag == FLAG_GAP_DAY, bit[ps.FLAG_ADS_GAP_DAY], 0)
    bits |= np.where(alloc.flag == FLAG_MISSING_HOUR, bit[ps.FLAG_ADS_MISSING_HOUR], 0)
    bits |= np.where(alloc.flag == FLAG_LAG, bit[ps.FLAG_ADS_LAG], 0)
    bits |= np.where(u_prov > 0, bit[ps.FLAG_PROVISIONAL], 0)

    # ---- calendar + prices (per VN day)
    local = grid.tz_convert(VN_TZ)
    hour_local = local.hour.to_numpy(dtype=np.int64)
    dow_local = ((local.day_of_week.to_numpy() + 1) % 7).astype(np.int64)
    day_of_hour = np.array([t.date() for t in local], dtype=object)
    days = sorted(set(day_of_hour))
    day_idx = {d: j for j, d in enumerate(days)}
    hour_day = np.array([day_idx[d] for d in day_of_hour], dtype=np.int64)
    cal = {
        "is_double_day": np.array([is_double_day(d) for d in days]),
        "is_pre_double_day": np.array([is_double_day(d + timedelta(days=1)) for d in days]),
        "is_post_double_day": np.array([is_double_day(d - timedelta(days=1)) for d in days]),
        "is_payday": np.array([is_payday(d) for d in days]),
        "days_to_tet": np.array([(TET - d).days for d in days], dtype=np.int64),
        "days_to_gift_peak": np.array([_days_to_gift_peak(d) for d in days], dtype=np.int64),
    }
    commit_prices, windows = _load_commitments(conn)
    flash = np.zeros((m, n), dtype=bool)
    for sid, ws_, we_ in zip(
        windows["sku_id"], windows["window_start"], windows["window_end"], strict=True
    ):
        hit = np.asarray((grid >= _utc(ws_)) & (grid < _utc(we_)), dtype=bool)
        if sid is None or pd.isna(sid):
            flash |= hit[None, :]
        elif str(sid) in pos:
            flash[pos[str(sid)]] |= hit

    observed = _load_daily_prices(conn, g_start, g_end)
    observed = observed[observed["sku_id"].isin(pos)]
    commit_sel = commit_prices[commit_prices["sku_id"].isin(pos)]
    seed = _price_seed(conn, full_start, g_start, commit_prices, pos)
    if seed.empty:
        prices = build_price_events(observed, days, commit_sel, fallback_lp)
    else:
        # Prices carried into the window ride on a pseudo-observation the day before.
        prices = build_price_events(
            pd.concat([seed, observed], ignore_index=True),
            [days[0] - timedelta(days=1), *days],
            commit_sel[commit_sel["day_local"] >= days[0]],
            fallback_lp,
        )
        prices = prices[prices["day_local"] >= days[0]].reset_index(drop=True)
    lp_d = np.full((m, len(days)), np.nan)
    ep_d = np.full((m, len(days)), np.nan)
    dc_d = np.full((m, len(days)), np.nan)
    if not prices.empty:
        pr = prices["sku_id"].map(pos).to_numpy(dtype=np.int64)
        pc = prices["day_local"].map(day_idx).to_numpy(dtype=np.int64)
        lp_d[pr, pc] = prices["list_price"].to_numpy(dtype=float)
        ep_d[pr, pc] = prices["eff_price"].to_numpy(dtype=float)
        dc_d[pr, pc] = prices["discount_pct"].to_numpy(dtype=float)
    for s, i in pos.items():
        if s in fallback_lp:
            row = lp_d[i]
            row[np.isnan(row)] = fallback_lp[s]

    # ---- assemble (sku-major, hour-minor)
    version = panel_version or _default_version(conn)
    keep = np.asarray(grid >= write_from, dtype=bool)
    cols = np.nonzero(keep)[0]
    nk = len(cols)

    def flat(a: npt.NDArray[Any]) -> npt.NDArray[Any]:
        return a[:, cols].reshape(-1)

    def tile(a: npt.NDArray[Any]) -> npt.NDArray[Any]:
        return np.tile(a[cols], m)

    def per_day(a: npt.NDArray[Any]) -> npt.NDArray[Any]:
        return np.tile(a[hour_day[cols]], m)

    def nullable_int(a: npt.NDArray[Any]) -> pd.arrays.IntegerArray:
        return pd.array(np.where(np.isfinite(a), np.round(a), np.nan), dtype="Int64")

    uniq_bits, inverse = np.unique(flat(bits), return_inverse=True)
    flag_lists = [[f for f in FLAG_ORDER if int(b) & bit[f]] for b in uniq_bits]
    data_flags = [flag_lists[j] for j in inverse]

    day_hour_cols = hour_day[cols]
    panel = pd.DataFrame(
        {
            "sku_id": np.repeat(np.array(skus, dtype=object), nk),
            "hour_utc": grid[cols].take(np.tile(np.arange(nk), m)),
            "hour_local": tile(hour_local),
            "dow_local": tile(dow_local),
            "day_local": np.tile(day_of_hour[cols], m),
            "units_gross": flat(u_gross),
            "units_provisional": flat(u_prov),
            "units_cancelled": flat(u_canc),
            "orders_count": flat(n_orders),
            "revenue_net": flat(rev),
            "list_price": nullable_int(lp_d[:, day_hour_cols].reshape(-1)),
            "eff_price": nullable_int(ep_d[:, day_hour_cols].reshape(-1)),
            "discount_pct": dc_d[:, day_hour_cols].reshape(-1),
            "stock_begin": nullable_int(flat(begin)),
            "stock_end": nullable_int(flat(end_)),
            "stock_source": flat(source),
            "stockout": flat(stockout),
            "stockout_conf": flat(conf),
            "low_stock": flat(low_stock),
            "restock_arrived": flat(restock),
            "campaign_id": flat(alloc.campaign_id),
            "ad_status": flat(alloc.status),
            "ad_spend_alloc": flat(alloc.spend),
            "ad_alloc_share": flat(alloc.share),
            "attributed_orders_alloc": flat(alloc.attributed),
            "is_double_day": per_day(cal["is_double_day"]),
            "is_pre_double_day": per_day(cal["is_pre_double_day"]),
            "is_post_double_day": per_day(cal["is_post_double_day"]),
            "is_payday": per_day(cal["is_payday"]),
            "in_flash_window": flat(flash),
            "days_to_tet": per_day(cal["days_to_tet"]),
            "days_to_gift_peak": per_day(cal["days_to_gift_peak"]),
            "data_flags": data_flags,
            "panel_version": version,
        }
    )
    # Explicit string dtype: DuckDB infers object columns from a sample and a column
    # that starts with NULLs (e.g. campaign_id of SKUs without ads) would fail.
    for col, kind, _ in ps.PANEL_COLUMNS:
        if kind == "str":
            panel[col] = panel[col].astype("string")
    ps.validate_panel(panel)
    write_days = set(day_of_hour[cols])
    prices = prices[prices["day_local"].isin(write_days)].reset_index(drop=True)
    return PanelFrames(panel=panel, price_events=prices, grid=grid, sku_ids=skus, allocation=alloc)


def _default_version(conn: duckdb.DuckDBPyConnection) -> str:
    v = _scalar(conn, "SELECT max(batch_id) FROM ingest_batches")
    return str(v) if v is not None else new_id("panel")


# ----------------------------------------------------------------- write
def write_panel(conn: duckdb.DuckDBPyConnection, frames: PanelFrames) -> int:
    """Replace the rows of ``frames`` (its SKUs × its hour range) in ``hourly_panel``."""
    panel = frames.panel
    if panel.empty:
        return 0
    lo = panel["hour_utc"].min().to_pydatetime()
    hi = panel["hour_utc"].max().to_pydatetime()
    cols = ", ".join(f'"{c}"' for c in ps.COLUMN_NAMES)
    # data_flags goes through text: DuckDB infers list child types from a sample, and a
    # long run of empty lists makes it guess NULL.
    exprs = ", ".join(
        "CASE WHEN data_flags_txt = '' THEN []::VARCHAR[] "
        "ELSE string_split(data_flags_txt, ',') END"
        if c == "data_flags"
        else f'"{c}"'
        for c in ps.COLUMN_NAMES
    )
    src = panel.drop(columns=["data_flags"])
    src["data_flags_txt"] = pd.array([",".join(f) for f in panel["data_flags"]], dtype="string")
    sku_df = pd.DataFrame({"sku_id": frames.sku_ids})
    conn.register("_panel_skus", sku_df)
    conn.register("_panel_src", src)
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            "DELETE FROM hourly_panel WHERE hour_utc >= ? AND hour_utc <= ? "
            "AND sku_id IN (SELECT sku_id FROM _panel_skus)",
            [lo, hi],
        )
        conn.execute(
            f"INSERT INTO hourly_panel ({cols}) SELECT {exprs} FROM _panel_src "
            "ORDER BY sku_id, hour_utc"
        )
        if not frames.price_events.empty:
            upsert_df(conn, "price_events", frames.price_events, ["sku_id", "day_local"])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.unregister("_panel_src")
        conn.unregister("_panel_skus")
    return len(panel)


def build_hourly_panel(
    conn: duckdb.DuckDBPyConnection,
    sku_ids: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    *,
    panel_version: str | None = None,
    window: bool = True,
) -> PanelBuildResult:
    """Rebuild ``hourly_panel`` for ``sku_ids`` (None = all, closed over combos and
    campaign co-members) and hours ``[start, end)`` (None = whole history / up to
    ``as_of``). Also refreshes ``price_events``. Idempotent. With ``start``, only the
    hours that influence ``[start, end)`` are computed (``window``, PNL-04)."""
    t0 = time.perf_counter()
    frames = compute_hourly_panel(
        conn, sku_ids, start, end, panel_version=panel_version, window=window
    )
    if frames is None:
        now = utcnow()
        return PanelBuildResult(
            rows_written=0,
            n_skus=0,
            start=now,
            end=now,
            panel_version=panel_version or "",
            price_rows=0,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )
    rows = write_panel(conn, frames)
    p = frames.panel
    res = PanelBuildResult(
        rows_written=rows,
        n_skus=len(frames.sku_ids),
        start=p["hour_utc"].min().to_pydatetime() if rows else frames.grid[0].to_pydatetime(),
        end=(p["hour_utc"].max() + HOUR).to_pydatetime()
        if rows
        else frames.grid[0].to_pydatetime(),
        panel_version=str(p["panel_version"].iat[0]) if rows else "",
        price_rows=len(frames.price_events),
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )
    log.info("panel_built", **res.model_dump(mode="json"))
    return res


def load_panel(
    conn: duckdb.DuckDBPyConnection,
    sku_ids: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Read ``hourly_panel`` as a validated DataFrame (input of forecast / ads_effect).
    Nullable integer columns come back as pandas ``Int64``; ``day_local`` as ``date``."""
    where: list[str] = ["TRUE"]
    params: list[Any] = []
    if sku_ids is not None:
        where.append("sku_id IN (SELECT unnest(?))")
        params.append(list(sku_ids))
    if start is not None:
        where.append("hour_utc >= ?")
        params.append(start)
    if end is not None:
        where.append("hour_utc < ?")
        params.append(end)
    df = _df(
        conn,
        f"SELECT * FROM hourly_panel WHERE {' AND '.join(where)} ORDER BY sku_id, hour_utc",
        params,
    )
    return normalize_panel_dtypes(df)


def normalize_panel_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a panel read back from DuckDB to the dtypes :func:`validate_panel` expects."""
    out = df.copy()
    out["hour_utc"] = pd.DatetimeIndex(out["hour_utc"]).tz_convert("UTC")
    out["day_local"] = pd.to_datetime(out["day_local"]).dt.date
    for col, kind, nullable in ps.PANEL_COLUMNS:
        if kind == "int":
            out[col] = out[col].astype("Int64") if nullable else out[col].astype("int64")
        elif kind == "bool":
            out[col] = out[col].astype(bool)
        elif kind == "float":
            out[col] = out[col].astype(float)
        elif kind == "list":
            out[col] = [list(v) if v is not None else [] for v in out[col]]
    return ps.validate_panel(out)
