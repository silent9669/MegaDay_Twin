"""Ingest pipeline (plan 02 §1, §7): seller files → canonical tables → quality →
``hourly_panel`` → ``DataUpdated``.

``ingest_files(conn, paths_or_dir)`` runs, in this order:

1. **Batches.** One ``ingest_batches`` row per file (``file_sha256`` UNIQUE). A
   file whose hash is already known is skipped (idempotent re-run), unless
   ``force=True`` (re-processed under its old ``batch_id`` and ``received_at``).
2. **Detect + normalise** each file (``detect_source`` / adapter). Files are
   processed by source so that dependencies are ready: orders (catalogue, hour
   profile for the ads timezone check, ``orders_as_of`` for ``ADS_LAG``) →
   campaigns → sale program → ads → stock sheet → cost.
3. **SKU matching** (ING-08): catalogue from ``skus`` + sales per day (with combo
   consumption, ``bundle_components``) → ``resolve`` → ``write_aliases``. Aliases
   already decided (auto or by a user) are reused without re-scoring.
4. **Map ``sku_id``** for stock snapshots, restock plans/receipts and cost rows.
   Rows whose alias is still ``pending`` are **held** (not written, counted in the
   report; the ``UNMATCHED_SKU`` issue asks the user).
5. **Upsert** parent tables first (``products`` → ``skus`` → ``orders`` →
   ``order_lines``; ``campaigns`` → ``campaign_skus`` / ``ad_hourly``; …) with
   ``repo.upsert_df`` on natural keys. Adapter ``src_*`` columns are dropped and
   ads filler rows (``src_fill``: hours the report does not have) are **not**
   stored: the panel derives ``unknown`` hours and their ``ADS_*`` flags itself.
6. **Quality** (ING-07): adapter issues per batch + derived rules
   (``app.ingest.quality``) → ``data_quality_issues``.
7. **Panel** (PNL-01..04): ``build_hourly_panel`` (whole history on a history
   load; on a live delta only the touched SKUs from the earliest new hour).
8. **Event** ``DataUpdated`` (and ``LiveTick`` for live deltas) on the optional bus.

PII never reaches this module: adapters drop the columns while reading.

``ingest_live_delta(conn, live_dir)`` (PNL-04) reads ``trang_thai.json`` written
by ``mock-data/replay.js`` (or works without it on a full-day folder such as
``live_11_11``), keeps a cursor per file in ``live_cursor`` and loads only the
rows after the cursor. A file that shrank means the replay restarted: every live
batch is removed and the day is loaded again from row 0.
"""

from __future__ import annotations

import csv
import json
import tempfile
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import duckdb
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.core.events import DataUpdated, Event, EventBus, LiveTick
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.timeutil import VN_TZ, floor_hour, utcnow
from app.ingest.adapters import get_adapter
from app.ingest.adapters.base import (
    DataIssue,
    IngestContext,
    NormFrame,
    SourceType,
    file_sha256,
)
from app.ingest.detect import Detection, detect_source
from app.ingest.quality import (
    issue_counts,
    refresh_all,
    write_batch_issues,
)
from app.ingest.sku_matching import (
    ROW_CODE_PREFIX,
    AliasMatch,
    AliasRecord,
    CatalogSku,
    add_bundle_consumption,
    load_known_aliases,
    resolve,
    stock_flow,
    write_aliases,
)
from app.ingest.timezone_check import orders_hour_profile
from app.panel.build import PanelBuildResult, build_hourly_panel
from app.warehouse import repo
from app.warehouse.db import migrate

log = get_logger(__name__)

Mode = Literal["history", "live"]

SOURCE_PRIORITY: dict[SourceType, int] = {
    SourceType.ORDERS: 0,
    SourceType.CAMPAIGNS: 1,
    SourceType.SALE_PROGRAM: 2,
    SourceType.ADS: 3,
    SourceType.INVENTORY: 4,
    SourceType.COST: 5,
}
# Natural key of every table the pipeline writes (schema.sql PRIMARY KEY).
TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "products": ("product_id",),
    "skus": ("sku_id",),
    "orders": ("order_id",),
    "order_lines": ("order_id", "line_no"),
    "campaigns": ("campaign_id",),
    "campaign_skus": ("campaign_id", "sku_id"),
    "sale_commitments": ("commitment_id",),
    "ad_hourly": ("campaign_id", "hour_utc"),
    "inventory_snapshots": ("sku_id", "snapshot_at"),
    "restock_plans": ("sku_id", "seen_at"),
    "restock_receipts": ("sku_id", "received_on"),
    "cost_items": ("sku_id", "valid_from"),
}
# Parent tables before children (foreign keys).
UPSERT_ORDER: dict[SourceType, tuple[str, ...]] = {
    SourceType.ORDERS: ("products", "skus", "orders", "order_lines"),
    SourceType.CAMPAIGNS: ("campaigns", "campaign_skus"),
    SourceType.SALE_PROGRAM: ("sale_commitments",),
    SourceType.ADS: ("ad_hourly",),
    SourceType.INVENTORY: ("inventory_snapshots", "restock_plans", "restock_receipts"),
    SourceType.COST: ("cost_items",),
}
PANEL_SOURCES = frozenset(
    {
        SourceType.ORDERS,
        SourceType.ADS,
        SourceType.INVENTORY,
        SourceType.CAMPAIGNS,
        SourceType.SALE_PROGRAM,
    }
)
DATE_COLUMNS = frozenset({"planned_date", "received_on", "day_local"})
FILE_SUFFIXES = frozenset({".csv", ".json"})
LIVE_STATUS_FILE = "trang_thai.json"
REPORTED_TABLES = (
    "ingest_batches",
    "products",
    "skus",
    "orders",
    "order_lines",
    "campaigns",
    "campaign_skus",
    "ad_hourly",
    "inventory_snapshots",
    "restock_plans",
    "restock_receipts",
    "cost_items",
    "sale_commitments",
    "sku_aliases",
    "data_quality_issues",
    "price_events",
    "hourly_panel",
)


# ====================================================================== reports
class FileReport(BaseModel):
    path: str
    file_name: str
    source_type: str | None = None
    variant: str | None = None
    batch_id: str | None = None
    status: Literal["loaded", "skipped", "failed"] = "loaded"
    batch_status: str | None = None
    rows_in: int = 0
    rows_written: dict[str, int] = Field(default_factory=dict)
    rows_held: int = 0  # rows waiting for a SKU confirmation (not written)
    issues: dict[str, int] = Field(default_factory=dict)
    as_of: datetime | None = None
    tz_offset_hours: int | None = None
    duration_ms: int = 0


class IngestReport(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    mode: Mode
    files: list[FileReport] = Field(default_factory=list)
    table_rows: dict[str, int] = Field(default_factory=dict)
    aliases: dict[str, dict[str, int]] = Field(default_factory=dict)
    pending_aliases: int = 0
    issues_by_code: dict[str, int] = Field(default_factory=dict)
    run_issues: dict[str, int] = Field(default_factory=dict)  # this run only
    panel: PanelBuildResult | None = None
    skus_changed: list[str] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    timings_ms: dict[str, int] = Field(default_factory=dict)
    duration_ms: int = 0
    skipped_reason: str | None = None

    @property
    def loaded(self) -> list[FileReport]:
        return [f for f in self.files if f.status == "loaded"]

    @property
    def batch_ids(self) -> list[str]:
        return [f.batch_id for f in self.files if f.batch_id and f.status != "skipped"]


# ====================================================================== internals
@dataclass
class _Item:
    path: Path
    display_name: str
    sha: str
    det: Detection
    batch_id: str
    received_at: datetime
    report: FileReport
    norm: NormFrame | None = None
    issues: list[DataIssue] = field(default_factory=list)
    t0: float = 0.0

    @property
    def source(self) -> SourceType | None:
        return self.det.source_type


def _list_files(paths: Path | str | Sequence[Path | str]) -> list[Path]:
    """A directory means its files (not sub-directories: ``live_*/`` and the
    answer key stay out); hidden files, ``_*`` and ``trang_thai.json`` are skipped."""
    items = [paths] if isinstance(paths, Path | str) else list(paths)
    out: list[Path] = []
    for p in map(Path, items):
        if p.is_dir():
            out.extend(
                f
                for f in sorted(p.iterdir())
                if f.is_file()
                and f.suffix.lower() in FILE_SUFFIXES
                and not f.name.startswith((".", "_"))
                and f.name != LIVE_STATUS_FILE
            )
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(str(p))
    return out


def _for_db(df: pd.DataFrame) -> pd.DataFrame:
    """Make dtypes unambiguous for DuckDB's pandas scan (it guesses an object
    column's type from a sample; leading NULLs make it guess wrong)."""
    out = df.copy()
    for c in out.columns:
        s = out[c]
        if str(c) in DATE_COLUMNS:
            out[c] = pd.to_datetime(s, errors="coerce")
            continue
        if s.dtype == object:
            sample = s.dropna()
            if sample.empty or all(isinstance(v, str) for v in sample.head(1000)):
                out[c] = s.astype("string")
    return out


def _upsert(conn: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    if table == "skus":
        return _upsert_skus(conn, _for_db(df))
    return repo.upsert_df(conn, table, _for_db(df), list(TABLE_KEYS[table]))


def _upsert_skus(conn: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """``skus`` is referenced by many tables and DuckDB rejects an upsert that
    rewrites a foreign-key column (``product_id``) of a referenced row, even to the
    same value. New SKUs are inserted; existing ones only get their descriptive
    columns updated (``product_id`` and ``is_bundle`` are never touched)."""
    frame = df.drop_duplicates(subset=["sku_id"], keep="last")
    cols = [str(c) for c in frame.columns]
    updates = [c for c in cols if c not in ("sku_id", "product_id", "is_bundle")]
    conn.register("_skus_src", frame)
    try:
        col_sql = ", ".join(f'"{c}"' for c in cols)
        conn.execute(
            f"INSERT INTO skus ({col_sql}) SELECT {col_sql} FROM _skus_src "
            "ON CONFLICT (sku_id) DO NOTHING"
        )
        if updates:
            sets = ", ".join(f'"{c}" = s."{c}"' for c in updates)
            conn.execute(f"UPDATE skus SET {sets} FROM _skus_src s WHERE skus.sku_id = s.sku_id")
    finally:
        conn.unregister("_skus_src")
    return len(frame)


def _scalar(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    rows = conn.execute(sql, params or []).fetchall()
    return rows[0][0] if rows else None


def _utc(value: Any) -> datetime | None:
    if value is None or (not isinstance(value, datetime) and pd.isna(value)):
        return None
    ts = pd.Timestamp(value)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.to_pydatetime()


def _batch_row(item: _Item, mode: Mode) -> dict[str, Any]:
    norm = item.norm
    rep = item.report
    return {
        "batch_id": item.batch_id,
        "source_type": item.source.value if item.source else "unknown",
        "file_name": item.display_name,
        "file_sha256": item.sha,
        "received_at": item.received_at,
        "as_of": rep.as_of,
        "row_count": norm.row_count if norm is not None else 0,
        "dropped_columns": list(norm.dropped_columns) if norm is not None else [],
        "tz_offset_hours": rep.tz_offset_hours or 0,
        "mode": mode,
        "status": rep.batch_status or "ok",
        "duration_ms": rep.duration_ms,
    }


def _save_batch(conn: duckdb.DuckDBPyConnection, row: dict[str, Any]) -> None:
    """Insert the batch row (once), then update its scalar columns. DuckDB turns an
    update of a LIST or UNIQUE column into delete + insert, which a batch already
    referenced by issues refuses, so ``dropped_columns`` / ``file_sha256`` are only
    written by the insert."""
    cols = list(row)
    conn.execute(
        f"INSERT INTO ingest_batches ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
        "ON CONFLICT (batch_id) DO NOTHING",
        [row[c] for c in cols],
    )
    conn.execute(
        "UPDATE ingest_batches SET as_of = ?, row_count = ?, tz_offset_hours = ?, status = ?, "
        "duration_ms = ? WHERE batch_id = ?",
        [row["as_of"], row["row_count"], row["tz_offset_hours"], row["status"],
         row["duration_ms"], row["batch_id"]],
    )  # fmt: skip


def _batch_status(issues: Sequence[DataIssue], failed: bool) -> str:
    if failed:
        return "failed"
    return "ok_with_issues" if any(i.severity in ("warn", "error") for i in issues) else "ok"


# ------------------------------------------------------------------ SKU matching
def _catalog(conn: duckdb.DuckDBPyConnection) -> list[CatalogSku]:
    bundles = {
        b: [(c.sku_id, c.qty) for c in comps]
        for b, comps in repo.get_bundle_components(conn).items()
    }
    sales: dict[str, dict[str, float]] = {}
    rows = conn.execute(
        "SELECT ol.sku_id, CAST(o.ordered_at + INTERVAL 7 HOUR AS DATE) AS d, SUM(ol.qty) "
        "FROM order_lines ol JOIN orders o USING (order_id) WHERE o.status <> 'cancelled' "
        "GROUP BY 1, 2"
    ).fetchall()
    for sku, d, q in rows:
        sales.setdefault(str(sku), {})[d.isoformat()] = float(q)
    flow = add_bundle_consumption(sales, bundles)
    cat = repo.fetch_all(
        conn,
        "SELECT s.sku_id, s.product_id, COALESCE(p.name, s.product_id) AS product_name, "
        "COALESCE(s.variant_name, '') AS variant_name, s.list_price, s.is_bundle "
        "FROM skus s LEFT JOIN products p USING (product_id) ORDER BY s.sku_id",
    )
    return [
        CatalogSku(
            sku_id=str(r["sku_id"]),
            product_id=str(r["product_id"]),
            product_name=str(r["product_name"]),
            variant_name=str(r["variant_name"]),
            list_price=None if r["list_price"] is None else int(r["list_price"]),
            is_bundle=bool(r["is_bundle"]) or str(r["sku_id"]) in bundles,
            daily_out=flow.get(str(r["sku_id"]), {}),
        )
        for r in cat
    ]


def _inventory_flow(snaps: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Daily units out per internal code from end-of-day snapshots."""
    day = snaps[snaps["granularity"] == "day"]
    if day.empty:
        return {}
    closes = (pd.DatetimeIndex(day["snapshot_at"]) - pd.Timedelta(hours=1)).tz_convert(VN_TZ)
    keyed = pd.DataFrame(
        {
            "code": day["src_raw_code"].to_numpy(),
            "day": closes.strftime("%Y-%m-%d"),
            "on_hand": day["on_hand"].to_numpy(),
        }
    )
    out: dict[str, dict[str, float]] = {}
    for code, g in keyed.groupby("code", sort=False):
        series: dict[str, int | None] = {
            str(d): (None if pd.isna(v) else int(v))
            for d, v in zip(g["day"], g["on_hand"], strict=True)
        }
        out[str(code)] = stock_flow(series)
    return out


def _db_inventory_context(conn: duckdb.DuckDBPyConnection) -> list[AliasRecord]:
    """Stock-sheet aliases already in the DB, as context when only a cost file is
    imported (``resolve`` learns ambiguous short names from the stock sheet)."""
    rows = repo.fetch_all(
        conn,
        "SELECT raw_code, raw_name, sku_id FROM sku_aliases WHERE source_type = 'inventory' "
        "AND status <> 'rejected'",
    )
    flow: dict[str, dict[str, float]] = {}
    snaps = conn.execute(
        "SELECT sku_id, CAST(snapshot_at - INTERVAL 1 HOUR + INTERVAL 7 HOUR AS DATE), on_hand "
        "FROM inventory_snapshots WHERE granularity = 'day' ORDER BY 1, 2"
    ).fetchall()
    per: dict[str, dict[str, int | None]] = {}
    for sku, d, v in snaps:
        per.setdefault(str(sku), {})[d.isoformat()] = None if v is None else int(v)
    for sku, series in per.items():
        flow[sku] = stock_flow(series)
    return [
        AliasRecord(
            source_type="inventory",
            raw_code=str(r["raw_code"]),
            raw_name=str(r["raw_name"]),
            daily_out=flow.get(str(r["sku_id"])) if r["sku_id"] else None,
        )
        for r in rows
    ]


def _cost_codes(names: Iterable[Any], codes: Iterable[Any]) -> list[str]:
    """Alias code of each cost line: the adapter's ``row:<n>`` when the same name is
    typed on several lines of the file (two SKUs typed alike, different prices), else
    ``''``. Unique names keep the key ``(cost, '', name)``, stable when lines move in
    the next version of the file (a user's confirmation is kept)."""
    names = [str(n) for n in names]
    counts: dict[str, int] = {}
    for n in names:
        counts[n] = counts.get(n, 0) + 1
    return [
        str(c) if counts[n] > 1 and str(c).startswith(ROW_CODE_PREFIX) else ""
        for n, c in zip(names, codes, strict=True)
    ]


def _aliases_from(items: Sequence[_Item]) -> tuple[list[AliasRecord], list[AliasRecord]]:
    orders: list[AliasRecord] = []
    hard: list[AliasRecord] = []
    for it in items:
        norm = it.norm
        if norm is None or norm.source_items.empty:
            continue
        src = norm.source_type
        si = norm.source_items
        if src is SourceType.ORDERS:
            for code, name in zip(si["raw_code"], si["raw_name"], strict=True):
                orders.append(
                    AliasRecord(
                        source_type="orders", raw_code=str(code), raw_name=str(name or code)
                    )
                )
        elif src is SourceType.INVENTORY:
            snaps = norm.tables.get("inventory_snapshots", pd.DataFrame())
            flow = _inventory_flow(snaps) if len(snaps) else {}
            for code, name in zip(si["raw_code"], si["raw_name"], strict=True):
                hard.append(
                    AliasRecord(
                        source_type="inventory",
                        raw_code=str(code),
                        raw_name=str(name),
                        daily_out=flow.get(str(code)) or None,
                    )
                )
        elif src is SourceType.COST:
            # The cost file has no code. Lines typed with the same name get the
            # adapter's ``row:<n>`` so each is its own alias; sku_matching scores
            # ``row:`` codes as "no code" (M1-pipeline "Hoàn thiện M1").
            codes = _cost_codes(si["raw_name"], si["raw_code"])
            for name, code, price in zip(si["raw_name"], codes, si["price"], strict=True):
                hard.append(
                    AliasRecord(
                        source_type="cost",
                        raw_code=code,
                        raw_name=str(name),
                        price=None if pd.isna(price) else int(price),
                    )
                )
    return orders, hard


def _match_skus(conn: duckdb.DuckDBPyConnection, items: Sequence[_Item]) -> dict[str, int]:
    """Resolve the aliases of this run and store them. Returns timing info."""
    orders, hard = _aliases_from(items)
    if not orders and not hard:
        return {"resolved": 0}
    existing: dict[tuple[str, str, str], str] = {
        (str(r["source_type"]), str(r["raw_code"]), str(r["raw_name"])): str(r["status"])
        for r in repo.fetch_all(
            conn, "SELECT source_type, raw_code, raw_name, status FROM sku_aliases"
        )
    }
    catalog: list[CatalogSku] | None = None
    new_orders = [a for a in orders if a.key not in existing]
    if new_orders:
        catalog = _catalog(conn)
        write_aliases(conn, resolve(new_orders, catalog), catalog)
    need = [a for a in hard if existing.get(a.key) in (None, "pending")]
    if not need:
        return {"resolved": len(new_orders)}
    catalog = catalog or _catalog(conn)
    run_sources = {a.source_type for a in hard}
    context = (
        _db_inventory_context(conn)
        if "cost" in run_sources and "inventory" not in run_sources
        else []
    )
    run_keys = {a.key for a in hard}
    context = [a for a in context if a.key not in run_keys]
    res = resolve([*hard, *context], catalog, known=load_known_aliases(conn))
    to_write: list[AliasMatch] = []
    for m in res.matches:
        key = (m.source_type, m.raw_code, m.raw_name)
        if key not in run_keys:
            continue
        # Never demote an alias the machine already accepted for lack of evidence in
        # this batch (e.g. an hourly live sheet has no daily outflow to compare).
        if existing.get(key) == "auto_accepted" and m.status != "auto_accepted":
            continue
        to_write.append(m)
    write_aliases(conn, to_write, catalog)
    _drop_merged_cost_aliases(conn, hard)
    return {"resolved": len(new_orders) + len(to_write)}


def _drop_merged_cost_aliases(conn: duckdb.DuckDBPyConnection, hard: Sequence[AliasRecord]) -> None:
    """A name now split into ``row:<n>`` aliases leaves its old merged alias
    ``(cost, '', name)`` behind (databases loaded before the split). Drop it while
    still pending; a user's decision is never removed."""
    split = sorted({a.raw_name for a in hard if a.source_type == "cost" and a.raw_code})
    if split:
        conn.execute(
            "DELETE FROM sku_aliases WHERE source_type = 'cost' AND raw_code = '' "
            "AND status = 'pending' AND raw_name IN (SELECT unnest(?))",
            [split],
        )


def _alias_maps(
    conn: duckdb.DuckDBPyConnection,
) -> tuple[dict[tuple[str, str], str], dict[str, str], dict[tuple[str, str], str]]:
    """(code, name) → sku and code → sku for the stock sheet; (code, name) → sku for
    cost (code ``''`` or ``row:<n>``, see ``_cost_codes``)."""
    rows = conn.execute(
        "SELECT source_type, raw_code, raw_name, sku_id FROM sku_aliases "
        "WHERE status IN ('auto_accepted', 'confirmed') AND sku_id IS NOT NULL"
    ).fetchall()
    inv: dict[tuple[str, str], str] = {}
    inv_code: dict[str, str] = {}
    cost: dict[tuple[str, str], str] = {}
    for src, code, name, sku in rows:
        if src == "inventory":
            inv[(str(code), str(name))] = str(sku)
            inv_code.setdefault(str(code), str(sku))
        elif src == "cost":
            cost[(str(code), str(name))] = str(sku)
    return inv, inv_code, cost


def _map_inventory(
    norm: NormFrame, inv: dict[tuple[str, str], str], inv_code: dict[str, str]
) -> int:
    held = 0
    snaps = norm.tables.get("inventory_snapshots")
    if snaps is not None and len(snaps):
        keys = list(
            zip(snaps["src_raw_code"].astype(str), snaps["src_raw_name"].astype(str), strict=True)
        )
        sku = pd.Series([inv.get(k) or inv_code.get(k[0]) for k in keys], index=snaps.index)
        snaps = snaps.assign(sku_id=sku)
        held += int(snaps["sku_id"].isna().sum())
        norm.tables["inventory_snapshots"] = snaps[snaps["sku_id"].notna()]
    for t in ("restock_plans", "restock_receipts"):
        df = norm.tables.get(t)
        if df is not None and len(df):
            df = df.assign(sku_id=df["src_raw_code"].astype(str).map(inv_code))
            held += int(df["sku_id"].isna().sum())
            norm.tables[t] = df[df["sku_id"].notna()]
    return held


def _map_cost(norm: NormFrame, cost: dict[tuple[str, str], str]) -> int:
    df = norm.tables.get("cost_items")
    if df is None or not len(df):
        return 0
    si = norm.source_items
    code_of = dict(
        zip(
            si["raw_code"].astype(str),
            _cost_codes(si["raw_name"], si["raw_code"]),
            strict=True,
        )
    )
    names = df["src_raw_name"].astype(str)
    codes = df["src_raw_code"].astype(str).map(code_of).fillna("")
    sku = pd.Series([cost.get(k) for k in zip(codes, names, strict=True)], index=df.index)
    df = df.assign(sku_id=sku)
    held = int(df["sku_id"].isna().sum())
    norm.tables["cost_items"] = df[df["sku_id"].notna()]
    return held


def _ensure_campaigns(conn: duckdb.DuckDBPyConnection, norm: NormFrame) -> None:
    """``ad_hourly`` references ``campaigns``: a campaign seen only in the ads report
    gets a minimal row (the campaign file, when imported, overwrites it)."""
    seen = norm.extras.get("campaigns_seen")
    if seen is None or seen.empty:
        return
    known = {str(r[0]) for r in conn.execute("SELECT campaign_id FROM campaigns").fetchall()}
    rows = []
    for r in seen.to_dict("records"):
        if str(r["campaign_id"]) in known:
            continue
        b = r["daily_budget"]
        rows.append(
            {
                "campaign_id": str(r["campaign_id"]),
                "name": str(r["name"] or r["campaign_id"]),
                "type": None,
                "daily_budget": 0 if b is None or pd.isna(b) else int(b),
                "started_at": _utc(r["first_hour_utc"]),
                "sku_level_control": False,
            }
        )
    if rows:
        repo.upsert_rows(conn, "campaigns", rows, key=["campaign_id"])


def _ensure_skus(conn: duckdb.DuckDBPyConnection, sku_ids: Iterable[str]) -> int:
    known = {str(r[0]) for r in conn.execute("SELECT sku_id FROM skus").fetchall()}
    missing = sorted({s for s in sku_ids if s and s not in known})
    for s in missing:
        repo.ensure_sku(conn, s)
    return len(missing)


def _update_first_sale(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        "UPDATE skus SET first_sale_at = m.first_at FROM ("
        "  SELECT ol.sku_id, min(o.ordered_at) AS first_at FROM order_lines ol "
        "  JOIN orders o USING (order_id) GROUP BY ol.sku_id) m "
        "WHERE skus.sku_id = m.sku_id AND (skus.first_sale_at IS NULL "
        "OR skus.first_sale_at > m.first_at)"
    )


def _touched_skus(conn: duckdb.DuckDBPyConnection, items: Sequence[_Item]) -> set[str]:
    out: set[str] = set()
    campaigns: set[str] = set()
    for it in items:
        if it.norm is None:
            continue
        for t, df in it.norm.tables.items():
            if df is None or df.empty:
                continue
            if "sku_id" in df.columns and t != "sale_commitments":
                out.update(str(s) for s in df["sku_id"].dropna().unique())
            if t == "ad_hourly":
                campaigns.update(str(c) for c in df["campaign_id"].unique())
    if campaigns:
        rows = conn.execute(
            "SELECT DISTINCT sku_id FROM campaign_skus WHERE campaign_id IN (SELECT unnest(?))",
            [sorted(campaigns)],
        ).fetchall()
        out.update(str(r[0]) for r in rows)
    return out


def _earliest_hour(items: Sequence[_Item]) -> datetime | None:
    """First hour whose panel row may change because of this run's rows."""
    cands: list[pd.Timestamp] = []
    for it in items:
        if it.norm is None:
            continue
        t = it.norm.tables
        for table, col, back in (
            ("orders", "ordered_at", 0),
            ("ad_hourly", "hour_utc", 0),
            ("inventory_snapshots", "snapshot_at", 1),
        ):
            df = t.get(table)
            if df is not None and len(df):
                cands.append(pd.Timestamp(df[col].min()) - pd.Timedelta(hours=back))
        for table in ("restock_receipts",):
            df = t.get(table)
            if df is not None and len(df):
                d = min(df["received_on"])
                cands.append(
                    pd.Timestamp(datetime(d.year, d.month, d.day, tzinfo=VN_TZ)).tz_convert("UTC")
                )
    if not cands:
        return None
    first = min(cands)
    first = first.tz_localize("UTC") if first.tzinfo is None else first.tz_convert("UTC")
    return floor_hour(first.to_pydatetime())


def _panel_start(
    conn: duckdb.DuckDBPyConnection, items: Sequence[_Item], touched: set[str]
) -> tuple[bool, datetime | None]:
    """First hour whose panel row may change, for the touched SKUs (live delta).

    Beyond the hours of the new rows (``_earliest_hour``) a new row also changes:

    * the whole VN day of a new order (price = median of the day, 02 §6.3);
    * the stock segment it falls in, back to the last valid snapshot at or before it
      (the mismatch at the next snapshot is spread over the segment), and any receipt
      of that day (its hour is re-placed using the day's snapshots) — covered by the
      VN midnight unless the snapshot before is older;
    * the ads hole right before a campaign's new rows (``ADS_LAG`` becomes
      ``ADS_MISSING_HOUR``/``ADS_GAP_DAY``).

    Returns ``(found, start)``; ``start`` None with ``found`` = rebuild the whole
    history of the touched SKUs."""
    base = _earliest_hour(items)
    if base is None:
        return False, None
    cands = [pd.Timestamp(base).tz_convert(VN_TZ).normalize().tz_convert("UTC").to_pydatetime()]
    skus = sorted(touched)
    if skus:
        rows = conn.execute(
            "SELECT sku_id, max(snapshot_at) FILTER (WHERE snapshot_at <= ?) "
            "FROM inventory_snapshots "
            "WHERE on_hand IS NOT NULL AND sku_id IN (SELECT unnest(?)) GROUP BY 1",
            [base, skus],
        ).fetchall()
        for _, before in rows:
            if before is None:  # only snapshots after the change: the walk runs backwards
                return True, None
            cands.append(_utc(before) or base)
    firsts: dict[str, datetime] = {}
    for it in items:
        ads = it.norm.tables.get("ad_hourly") if it.norm is not None else None
        if ads is None or ads.empty:
            continue
        for cid, first in ads.groupby("campaign_id")["hour_utc"].min().items():
            t = _utc(first)
            if t is not None and (str(cid) not in firsts or t < firsts[str(cid)]):
                firsts[str(cid)] = t
    if firsts:
        cids = sorted(firsts)
        rows = conn.execute(
            "SELECT n.cid, (SELECT max(a.hour_utc) FROM ad_hourly a "
            "  WHERE a.campaign_id = n.cid AND a.hour_utc < n.first), "
            "(SELECT c.started_at FROM campaigns c WHERE c.campaign_id = n.cid) "
            "FROM (SELECT unnest(?) AS cid, unnest(?) AS first) n",
            [cids, [firsts[c] for c in cids]],
        ).fetchall()
        for _, prev, started in rows:
            if prev is not None:
                cands.append((_utc(prev) or base) + timedelta(hours=1))
            elif started is not None:
                cands.append(floor_hour(_utc(started) or base))
            else:
                return True, None
    return True, min(cands)


def _publish(bus: EventBus | None, events: Sequence[Event]) -> None:
    if bus is None:
        return
    for e in events:
        bus.publish_nowait(e)


# ====================================================================== core run
def _run(
    conn: duckdb.DuckDBPyConnection,
    files: Sequence[Path],
    *,
    mode: Mode,
    force: bool,
    build_panel: bool,
    bus: EventBus | None,
    display_names: dict[Path, str] | None = None,
    as_of_overrides: dict[SourceType, datetime] | None = None,
    extra_events: Sequence[Event] = (),
) -> IngestReport:
    t_start = time.perf_counter()
    timings: dict[str, int] = {}
    migrate(conn)
    report = IngestReport(mode=mode)
    received_at = utcnow()
    overrides = as_of_overrides or {}
    names = display_names or {}

    # ---- 1. batches (skip known hashes)
    items: list[_Item] = []
    for path in files:
        sha = file_sha256(path)
        name = names.get(path, path.name)
        old = repo.fetch_one(
            conn,
            "SELECT batch_id, source_type, received_at, status FROM ingest_batches "
            "WHERE file_sha256 = ?",
            [sha],
        )
        if old is not None and not force:
            report.files.append(
                FileReport(
                    path=str(path),
                    file_name=name,
                    source_type=str(old["source_type"]),
                    batch_id=str(old["batch_id"]),
                    status="skipped",
                    batch_status=str(old["status"]),
                )
            )
            continue
        det = detect_source(path)
        items.append(
            _Item(
                path=path,
                display_name=name,
                sha=sha,
                det=det,
                batch_id=str(old["batch_id"]) if old else new_id("ib"),
                received_at=_utc(old["received_at"]) or received_at if old else received_at,
                report=FileReport(
                    path=str(path),
                    file_name=name,
                    source_type=det.source_type.value if det.source_type else None,
                    variant=det.variant,
                ),
            )
        )
    if not items:
        report.skipped_reason = "no_new_files"
        _finish_report(conn, report, t_start, timings)
        return report
    items.sort(key=lambda it: SOURCE_PRIORITY.get(it.source, 99) if it.source else 99)

    # ---- 2. detect + normalise
    t = time.perf_counter()
    run_orders: list[pd.Series] = []
    run_orders_as_of: datetime | None = None
    for it in items:
        it.t0 = time.perf_counter()
        it.report.batch_id = it.batch_id
        if it.source is None:
            it.issues = [it.det.issue] if it.det.issue else []
            it.report.status = "failed"
            continue
        ctx = IngestContext(
            batch_id=it.batch_id,
            received_at=it.received_at,
            as_of=overrides.get(it.source),
            mode=mode,
        )
        if it.source is SourceType.ADS:
            _ads_context(
                conn, ctx, mode, run_orders, run_orders_as_of, overrides,
                exclude=[i.batch_id for i in items],
            )  # fmt: skip
        adapter = get_adapter(it.source)
        norm = adapter.normalize(adapter.read(it.path), ctx)
        it.norm = norm
        it.issues = list(norm.issues)
        it.report.rows_in = norm.row_count
        it.report.as_of = overrides.get(it.source) or norm.as_of
        it.report.tz_offset_hours = norm.tz_offset_hours
        if it.source is SourceType.ORDERS and "orders" in norm.tables:
            run_orders.append(norm.tables["orders"]["ordered_at"])
            if it.report.as_of is not None:
                run_orders_as_of = max(filter(None, [run_orders_as_of, it.report.as_of]))
        if any(i.code == "MISSING_COLUMNS" for i in norm.issues):
            it.report.status = "failed"
        it.report.duration_ms = int((time.perf_counter() - it.t0) * 1000)
    timings["normalize"] = int((time.perf_counter() - t) * 1000)

    ok = [it for it in items if it.report.status != "failed" and it.norm is not None]
    by_source = {s: [it for it in ok if it.source is s] for s in SourceType}

    # ---- 3..6. write everything in one transaction
    conn.execute("BEGIN TRANSACTION")
    try:
        for it in items:
            _save_batch(conn, _batch_row(it, mode))

        t = time.perf_counter()
        for src in (SourceType.ORDERS, SourceType.CAMPAIGNS, SourceType.SALE_PROGRAM):
            for it in by_source[src]:
                assert it.norm is not None
                if src is SourceType.CAMPAIGNS:
                    links = it.norm.tables.get("campaign_skus")
                    if links is not None and len(links):
                        _ensure_skus(conn, links["sku_id"].astype(str))
                _write_tables(conn, it)
        timings["upsert_orders_campaigns"] = int((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        _match_skus(conn, [*by_source[SourceType.ORDERS], *by_source[SourceType.INVENTORY],
                           *by_source[SourceType.COST]])  # fmt: skip
        inv_map, inv_code, cost_map = _alias_maps(conn)
        timings["sku_matching"] = int((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        for it in by_source[SourceType.ADS]:
            assert it.norm is not None
            _ensure_campaigns(conn, it.norm)
            ads = it.norm.tables.get("ad_hourly")
            if ads is not None and "src_fill" in ads.columns:
                it.norm.tables["ad_hourly"] = ads[ads["src_fill"].isna()]
        for it in by_source[SourceType.INVENTORY]:
            assert it.norm is not None
            it.report.rows_held = _map_inventory(it.norm, inv_map, inv_code)
        for it in by_source[SourceType.COST]:
            assert it.norm is not None
            it.report.rows_held = _map_cost(it.norm, cost_map)
        for src in (SourceType.ADS, SourceType.INVENTORY, SourceType.COST):
            for it in by_source[src]:
                assert it.norm is not None
                _write_tables(conn, it)
        if by_source[SourceType.ORDERS]:
            _update_first_sale(conn)
        timings["upsert_mapped"] = int((time.perf_counter() - t) * 1000)

        t = time.perf_counter()
        for it in items:
            it.report.batch_status = _batch_status(it.issues, it.report.status == "failed")
            it.report.issues = _codes(it.issues)
            _save_batch(conn, _batch_row(it, mode))
            write_batch_issues(conn, it.batch_id, it.issues, received_at)
        derived, _ = refresh_all(
            conn,
            received_at,
            fallback_batch=items[-1].batch_id,
            since=_earliest_hour(ok) if mode == "live" else None,
        )
        report.run_issues = _codes([i for it in items for i in it.issues] + derived)
        timings["quality"] = int((time.perf_counter() - t) * 1000)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    report.files.extend(it.report for it in items)

    # ---- 7. panel
    touched = _touched_skus(conn, ok)
    report.skus_changed = sorted(touched)
    loaded_sources = {it.source for it in ok if it.source is not None}
    if build_panel and loaded_sources & PANEL_SOURCES:
        t = time.perf_counter()
        report.panel = _rebuild_panel(conn, mode, ok, touched)
        timings["panel"] = int((time.perf_counter() - t) * 1000)

    # ---- 8. events
    as_ofs = [
        it.report.as_of
        for it in ok
        if it.source in (SourceType.ORDERS, SourceType.INVENTORY) and it.report.as_of
    ]
    if ok:
        report.events.append(
            DataUpdated(
                batch_id=max(it.batch_id for it in ok),
                sources=sorted({s.value for s in loaded_sources}),
                as_of=max(as_ofs) if as_ofs else None,
                skus_changed=report.skus_changed,
            )
        )
    report.events.extend(extra_events)
    _publish(bus, report.events)
    _finish_report(conn, report, t_start, timings)
    log.info(
        "ingest_done",
        mode=mode,
        files=len(report.files),
        loaded=len(report.loaded),
        duration_ms=report.duration_ms,
    )
    return report


def _write_tables(conn: duckdb.DuckDBPyConnection, it: _Item) -> None:
    """Upsert the tables of one file, parents first; the time is added to the
    file's ``duration_ms`` (normalise + write; shared steps are in ``timings_ms``)."""
    assert it.norm is not None and it.source is not None
    t = time.perf_counter()
    for table in UPSERT_ORDER[it.source]:
        if table in it.norm.tables:
            it.report.rows_written[table] = _upsert(conn, table, it.norm.schema_frame(table))
    it.report.duration_ms += int((time.perf_counter() - t) * 1000)


def _codes(issues: Iterable[DataIssue]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in issues:
        out[i.code] = out.get(i.code, 0) + 1
    return dict(sorted(out.items()))


def _ads_context(
    conn: duckdb.DuckDBPyConnection,
    ctx: IngestContext,
    mode: Mode,
    run_orders: list[pd.Series],
    run_orders_as_of: datetime | None,
    overrides: dict[SourceType, datetime],
    *,
    exclude: Sequence[str] = (),
) -> None:
    """Timezone offset (a previous ads batch's offset is reused: in live mode always,
    in history mode when it was non-zero), hour profile and ``orders_as_of``."""
    prev = conn.execute(
        "SELECT tz_offset_hours FROM ingest_batches WHERE source_type = 'ads' "
        "AND status <> 'failed' AND as_of IS NOT NULL AND batch_id NOT IN (SELECT unnest(?)) "
        "ORDER BY batch_id DESC LIMIT 1",
        [list(exclude)],
    ).fetchall()
    if prev and (mode == "live" or int(prev[0][0] or 0) != 0):
        ctx.tz_offsets = {"ads": int(prev[0][0] or 0)}
    else:
        if run_orders:
            ordered = pd.concat(run_orders, ignore_index=True)
        else:
            ordered = conn.execute("SELECT ordered_at FROM orders").df()["ordered_at"]
        if len(ordered):
            ctx.orders_hour_profile = orders_hour_profile(ordered)
    orders_as_of = overrides.get(SourceType.ORDERS) or run_orders_as_of
    if orders_as_of is None:
        orders_as_of = _utc(
            _scalar(
                conn,
                "SELECT max(as_of) FROM ingest_batches WHERE source_type = 'orders' "
                "AND status <> 'failed'",
            )
        )
    ctx.orders_as_of = orders_as_of


def _rebuild_panel(
    conn: duckdb.DuckDBPyConnection, mode: Mode, items: Sequence[_Item], touched: set[str]
) -> PanelBuildResult:
    if mode == "history":
        return build_hourly_panel(conn)
    # Live: touched SKUs from the first affected hour (``_panel_start``); if the data
    # horizon moved, the other SKUs only need the new hours. The panel computes only
    # the hours that influence what it writes (window mode, PNL-04).
    prev_end = _utc(_scalar(conn, "SELECT max(hour_utc) + INTERVAL 1 HOUR FROM hourly_panel"))
    found, start = _panel_start(conn, items, touched)
    if not found:
        start = None
    if prev_end is not None and start is not None:
        start = min(start, prev_end)
    res = build_hourly_panel(conn, sorted(touched) or None, start=start)
    if touched and prev_end is not None:
        others = sorted(
            {str(r[0]) for r in conn.execute("SELECT sku_id FROM skus").fetchall()}
            - set(touched)
            - _closure_written(conn, res, prev_end)
        )
        if others:
            extra = build_hourly_panel(conn, others, start=prev_end)
            res = res.model_copy(
                update={
                    "rows_written": res.rows_written + extra.rows_written,
                    "n_skus": res.n_skus + extra.n_skus,
                    "duration_ms": res.duration_ms + extra.duration_ms,
                }
            )
    return res


def _closure_written(
    conn: duckdb.DuckDBPyConnection, res: PanelBuildResult, since: datetime
) -> set[str]:
    """SKUs that already have rows at/after ``since`` (written by the first call,
    which closes the requested set over combos and campaigns)."""
    rows = conn.execute(
        "SELECT DISTINCT sku_id FROM hourly_panel WHERE hour_utc >= ? AND panel_version = ?",
        [since, res.panel_version],
    ).fetchall()
    return {str(r[0]) for r in rows}


def _finish_report(
    conn: duckdb.DuckDBPyConnection,
    report: IngestReport,
    t_start: float,
    timings: dict[str, int],
) -> None:
    report.table_rows = {t: repo.count_rows(conn, t) for t in REPORTED_TABLES}
    stats: dict[str, dict[str, int]] = {}
    for src, status, n in conn.execute(
        "SELECT source_type, status, count(*) FROM sku_aliases GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall():
        stats.setdefault(str(src), {})[str(status)] = int(n)
    report.aliases = stats
    report.pending_aliases = sum(s.get("pending", 0) for s in stats.values())
    report.issues_by_code = issue_counts(conn)
    report.timings_ms = timings
    report.duration_ms = int((time.perf_counter() - t_start) * 1000)


# ====================================================================== public API
def ingest_files(
    conn: duckdb.DuckDBPyConnection,
    paths: Path | str | Sequence[Path | str],
    *,
    mode: Mode = "history",
    force: bool = False,
    build_panel: bool = True,
    bus: EventBus | None = None,
) -> IngestReport:
    """Load seller files (a directory or a list of files) into the warehouse.

    Idempotent: a file already loaded (same SHA-256) is skipped; with ``force`` it
    is re-processed and upserted over its old rows. Events are returned in the
    report and, when ``bus`` is given, published on it."""
    return _run(
        conn,
        _list_files(paths),
        mode=mode,
        force=force,
        build_panel=build_panel,
        bus=bus,
    )


# ---------------------------------------------------------------------- live
class LiveStatus(BaseModel):
    """``trang_thai.json`` of ``replay.js`` (02 §7)."""

    day: date
    orders_inventory_until: datetime | None  # UTC instant of "HH:59"
    ads_until: datetime | None
    raw: dict[str, Any]

    @property
    def key(self) -> str:
        return json.dumps(
            {k: self.raw.get(k) for k in ("ngay", "don_hang_va_ton_den_het", "ads_den_het")},
            ensure_ascii=False,
            sort_keys=True,
        )


def _clock(day: date, text: Any) -> datetime | None:
    if not isinstance(text, str) or ":" not in text:
        return None  # "chưa có"
    try:
        hh, mm = (int(x) for x in text.strip().split(":", 1))
    except ValueError:
        return None
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=VN_TZ).astimezone(VN_TZ)


def read_live_status(live_dir: Path) -> LiveStatus | None:
    path = Path(live_dir) / LIVE_STATUS_FILE
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    day = date.fromisoformat(str(raw["ngay"]))
    oi = _clock(day, raw.get("don_hang_va_ton_den_het"))
    ads = _clock(day, raw.get("ads_den_het"))
    return LiveStatus(
        day=day,
        orders_inventory_until=_utc(oi),
        ads_until=_utc(ads),
        raw=raw,
    )


class LiveDeltaReport(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    live_dir: str
    status: Literal["loaded", "unchanged", "empty"]
    restarted: bool = False
    new_rows: dict[str, int] = Field(default_factory=dict)
    tick: LiveTick | None = None
    ingest: IngestReport | None = None
    duration_ms: int = 0


def _cursor_key(live_dir: Path, name: str) -> str:
    return f"{Path(live_dir).resolve().as_posix()}/{name}"


def _read_records(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return [], []
    return rows[0], [r for r in rows[1:] if r]


def _write_chunk(path: Path, header: list[str], records: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(records)


def reset_live_data(conn: duckdb.DuckDBPyConnection) -> int:
    """Remove every live batch and its rows (replay restarted from hour 0)."""
    batches = [
        str(r[0])
        for r in conn.execute("SELECT batch_id FROM ingest_batches WHERE mode = 'live'").fetchall()
    ]
    if not batches:
        return 0
    first = _scalar(
        conn,
        "SELECT min(o.ordered_at) FROM orders o WHERE o.batch_id IN (SELECT unnest(?))",
        [batches],
    )
    snap_first = _scalar(
        conn,
        "SELECT min(snapshot_at) FROM inventory_snapshots WHERE batch_id IN (SELECT unnest(?))",
        [batches],
    )
    starts = [t for t in (_utc(first), _utc(snap_first)) if t is not None]
    # No surrounding transaction: DuckDB checks foreign keys against the state at
    # the start of a transaction, so children and parents could not be deleted in
    # one. Each statement commits on its own, children first.
    conn.execute(
        "DELETE FROM order_lines WHERE order_id IN (SELECT order_id FROM orders "
        "WHERE batch_id IN (SELECT unnest(?)))",
        [batches],
    )
    for table in ("orders", "ad_hourly", "inventory_snapshots"):
        conn.execute(f"DELETE FROM {table} WHERE batch_id IN (SELECT unnest(?))", [batches])
    if starts:
        day_start = pd.Timestamp(min(starts)).tz_convert(VN_TZ).normalize()
        utc_start = day_start.tz_convert("UTC").to_pydatetime()
        conn.execute("DELETE FROM restock_receipts WHERE received_on >= ?", [day_start.date()])
        conn.execute("DELETE FROM restock_plans WHERE seen_at > ?", [utc_start])
        conn.execute("DELETE FROM hourly_panel WHERE hour_utc >= ?", [utc_start])
    conn.execute("DELETE FROM data_quality_issues WHERE batch_id IN (SELECT unnest(?))", [batches])
    conn.execute("DELETE FROM ingest_batches WHERE batch_id IN (SELECT unnest(?))", [batches])
    conn.execute("DELETE FROM live_cursor")
    return len(batches)


def ingest_live_delta(
    conn: duckdb.DuckDBPyConnection,
    live_dir: Path | str,
    *,
    bus: EventBus | None = None,
    build_panel: bool = True,
) -> LiveDeltaReport:
    """Load what is new in a live folder since the previous call (PNL-04).

    1. ``trang_thai.json`` unchanged since the last call → nothing to do.
    2. For each CSV, rows after ``live_cursor.last_row_count`` are written to a
       temporary chunk file and go through the normal pipeline (same adapters,
       ``mode='live'``). A file with fewer rows than the cursor = replay restarted
       → :func:`reset_live_data`, then reload from row 0.
    3. ``as_of`` of orders/stock = ``don_hang_va_ton_den_het``, of ads =
       ``ads_den_het`` (end of the minute ``HH:59``).
    4. ``DataUpdated`` (touched SKUs) then ``LiveTick``.
    """
    t0 = time.perf_counter()
    live_dir = Path(live_dir)
    migrate(conn)
    status = read_live_status(live_dir)
    status_key = _cursor_key(live_dir, LIVE_STATUS_FILE)
    cursors = {
        str(f): (int(n or 0), t)
        for f, n, t in conn.execute(
            "SELECT file, last_row_count, last_tick FROM live_cursor"
        ).fetchall()
    }
    if status is not None and cursors.get(status_key, (0, None))[1] == status.key:
        return LiveDeltaReport(
            live_dir=str(live_dir),
            status="unchanged",
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )

    csvs = sorted(p for p in live_dir.glob("*.csv") if p.is_file())
    loaded: dict[Path, tuple[list[str], list[list[str]]]] = {p: _read_records(p) for p in csvs}
    restarted = any(
        len(recs) < cursors.get(_cursor_key(live_dir, p.name), (0, None))[0]
        for p, (_, recs) in loaded.items()
    )
    if restarted:
        reset_live_data(conn)
        cursors = {}

    overrides: dict[SourceType, datetime] = {}
    if status is not None:
        if status.orders_inventory_until is not None:
            end = status.orders_inventory_until + timedelta(minutes=1)
            overrides[SourceType.ORDERS] = end
            overrides[SourceType.INVENTORY] = end
        if status.ads_until is not None:
            overrides[SourceType.ADS] = status.ads_until + timedelta(minutes=1)

    new_rows: dict[str, int] = {}
    report: IngestReport | None = None
    with tempfile.TemporaryDirectory(prefix="live-delta-") as tmp:
        chunks: list[Path] = []
        names: dict[Path, str] = {}
        for p, (header, recs) in loaded.items():
            start = cursors.get(_cursor_key(live_dir, p.name), (0, None))[0]
            fresh = recs[start:]
            new_rows[p.name] = len(fresh)
            if not fresh or not header:
                continue
            chunk = Path(tmp) / p.name
            _write_chunk(chunk, header, fresh)
            chunks.append(chunk)
            names[chunk] = f"{live_dir.name}/{p.name}#{start + 1}-{start + len(fresh)}"
        tick = _tick(status) if status is not None else None
        if chunks:
            report = _run(
                conn,
                chunks,
                mode="live",
                force=False,
                build_panel=build_panel,
                bus=None,
                display_names=names,
                as_of_overrides=overrides,
            )
        if tick is None and report is not None:
            tick = _tick_from_batches(conn)

    now = utcnow()
    rows = [
        {
            "file": _cursor_key(live_dir, p.name),
            "last_row_count": len(recs),
            "last_tick": status.key if status else None,
            "updated_at": now,
        }
        for p, (_, recs) in loaded.items()
    ]
    if status is not None:
        rows.append({"file": status_key, "last_row_count": 0, "last_tick": status.key,
                     "updated_at": now})  # fmt: skip
    if rows:
        repo.upsert_rows(conn, "live_cursor", rows, key=["file"])

    events: list[Event] = list(report.events) if report is not None else []
    if tick is not None:
        events.append(tick)
        if report is not None:
            report.events.append(tick)
    _publish(bus, events)
    return LiveDeltaReport(
        live_dir=str(live_dir),
        status="loaded" if report is not None else "empty",
        restarted=restarted,
        new_rows=new_rows,
        tick=tick,
        ingest=report,
        duration_ms=int((time.perf_counter() - t0) * 1000),
    )


def _tick(status: LiveStatus) -> LiveTick:
    return LiveTick(
        day=status.day,
        orders_inventory_until=status.orders_inventory_until,
        ads_until=status.ads_until,
    )


def _tick_from_batches(conn: duckdb.DuckDBPyConnection) -> LiveTick | None:
    """No ``trang_thai.json`` (full-day folder): marks from the live batches."""
    rows = dict(
        conn.execute(
            "SELECT source_type, max(as_of) FROM ingest_batches WHERE mode = 'live' "
            "AND source_type IN ('orders', 'ads') GROUP BY 1"
        ).fetchall()
    )
    oi = _utc(rows.get("orders"))
    if oi is None:
        return None
    ads = _utc(rows.get("ads"))
    minute = timedelta(minutes=1)
    return LiveTick(
        day=(oi - minute).astimezone(VN_TZ).date(),
        orders_inventory_until=oi - minute,
        ads_until=ads - minute if ads is not None else None,
    )


__all__ = [
    "FileReport",
    "IngestReport",
    "LiveDeltaReport",
    "LiveStatus",
    "ingest_files",
    "ingest_live_delta",
    "read_live_status",
    "reset_live_data",
]
