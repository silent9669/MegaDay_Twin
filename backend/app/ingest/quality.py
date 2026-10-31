"""Data quality rules that need several sources or matched SKUs (ING-07, plan 02 §3.1),
and persistence of every issue into ``data_quality_issues``.

Two kinds of issues
-------------------
* **Adapter issues** (``DUP_ORDER``, ``NEG_STOCK``, ``ADS_TZ_SHIFT``…, see
  docs/quyet-dinh/M1-adapter.md §5) belong to the batch of the file they came
  from: :func:`write_batch_issues` replaces the issues of one batch.
* **Derived issues** (:data:`DERIVED_CODES`) are recomputed from the warehouse
  after every ingest run by :func:`run_cross_source_checks`:

  ============================ ======= ==============================================
  code                         level   rule
  ============================ ======= ==============================================
  ``UNMATCHED_SKU``            error   alias of the stock sheet / cost file still
                                       ``pending`` (queue, 02 §5.3)
  ``MISSING_COST``             error   SKU sold (not cancelled) without any
                                       ``cost_items`` row → economics blocked
  ``STOCK_JUMP``               warn    snapshot ≠ previous − units out + receipts by
                                       more than ``max(5, 10 % · expected)``
  ``SPEND_OVER_BUDGET``        warn    daily spend > daily budget × 1.05
  ``BUNDLE_NO_STOCK_ROW``      warn    SKU sold, no stock row, not a declared combo
  ============================ ======= ==============================================

  :func:`refresh_derived_issues` keeps them in sync: ids are deterministic
  (same issue → same ``issue_id``), issues that disappeared are deleted unless a
  user already ``confirmed``/``dismissed`` them, and a user decision is never
  overwritten.

Codes and severities come from one table, ``adapters.base.ISSUE_SEVERITY``
(02 §3.1 + the 4 adapter codes ``ROW_INVALID``, ``UNKNOWN_STATUS``, ``DUP_ROW``,
``MISSING_COLUMNS``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from app.core.timeutil import VN_TZ
from app.ingest.adapters.base import ISSUE_SEVERITY, DataIssue, make_issue
from app.warehouse import repo

DERIVED_CODES: tuple[str, ...] = (
    "UNMATCHED_SKU",
    "MISSING_COST",
    "STOCK_JUMP",
    "SPEND_OVER_BUDGET",
    "BUNDLE_NO_STOCK_ROW",
)
# Source whose latest batch a derived issue is attached to (FK batch_id).
DERIVED_SOURCE: dict[str, tuple[str, ...]] = {
    "UNMATCHED_SKU": ("inventory", "cost"),
    "MISSING_COST": ("cost", "orders"),
    "STOCK_JUMP": ("inventory",),
    "SPEND_OVER_BUDGET": ("ads",),
    "BUNDLE_NO_STOCK_ROW": ("inventory", "orders"),
}
STOCK_JUMP_ABS = 5  # 02 §3.1: max(5, 10 %)
STOCK_JUMP_REL = 0.10
SPEND_TOLERANCE = 1.05
LOOKBACK = timedelta(days=3)  # incremental STOCK_JUMP: walk back to a valid snapshot
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
RECEIPT_HOUR_LOCAL = 10  # default receipt hour when unknown (02 §6.1)
VN_OFFSET = "INTERVAL 7 HOUR"  # VN has no DST; SQL day bucketing on a UTC session

_ISSUE_COLUMNS = (
    "issue_id",
    "batch_id",
    "code",
    "severity",
    "entity_type",
    "entity_id",
    "row_ref",
    "detail",
    "status",
    "resolved_by",
    "created_at",
)


# ====================================================================== ids & rows
def _digest(*parts: object) -> str:
    raw = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def derived_issue_id(issue: DataIssue) -> str:
    """Deterministic id: the same derived issue keeps its id (and its user status)
    across ingest runs."""
    return "dq-" + _digest(issue.code, issue.entity_type, issue.entity_id, issue.row_ref)


def batch_issue_id(batch_id: str, seq: int, issue: DataIssue) -> str:
    return "dq-" + _digest(batch_id, seq, issue.code, issue.entity_id, issue.row_ref)


def _row(issue: DataIssue, issue_id: str, batch_id: str, created_at: datetime) -> list[Any]:
    severity = issue.severity or ISSUE_SEVERITY.get(issue.code, "warn")
    return [
        issue_id,
        batch_id,
        issue.code,
        severity,
        issue.entity_type,
        issue.entity_id,
        issue.row_ref,
        json.dumps(issue.detail, ensure_ascii=False, default=str),
        issue.status,
        None,
        created_at,
    ]


def _insert_frame(conn: duckdb.DuckDBPyConnection, rows: list[list[Any]], conflict: str) -> int:
    if not rows:
        return 0
    df = pd.DataFrame(rows, columns=list(_ISSUE_COLUMNS))
    for c in _ISSUE_COLUMNS:
        if c != "created_at":
            df[c] = df[c].astype("string")
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True)
    df = df.drop_duplicates(subset=["issue_id"], keep="last")
    cols = ", ".join(_ISSUE_COLUMNS)
    select = ", ".join("CAST(detail AS JSON)" if c == "detail" else c for c in _ISSUE_COLUMNS)
    conn.register("_dq_src", df)
    try:
        conn.execute(
            f"INSERT INTO data_quality_issues ({cols}) SELECT {select} FROM _dq_src {conflict}"
        )
    finally:
        conn.unregister("_dq_src")
    return len(df)


def write_batch_issues(
    conn: duckdb.DuckDBPyConnection,
    batch_id: str,
    issues: Sequence[DataIssue],
    created_at: datetime,
) -> int:
    """Replace the issues of one batch (re-processing a file never duplicates)."""
    conn.execute(
        "DELETE FROM data_quality_issues WHERE batch_id = ? AND code NOT IN (SELECT unnest(?))",
        [batch_id, list(DERIVED_CODES)],
    )
    rows = [
        _row(i, batch_issue_id(batch_id, n, i), batch_id, created_at) for n, i in enumerate(issues)
    ]
    return _insert_frame(conn, rows, "ON CONFLICT (issue_id) DO NOTHING")


def refresh_derived_issues(
    conn: duckdb.DuckDBPyConnection,
    issues: Sequence[DataIssue],
    batch_for_code: Mapping[str, str],
    created_at: datetime,
    *,
    codes: Iterable[str] = DERIVED_CODES,
    scope: tuple[str, list[Any]] | None = None,
) -> int:
    """Sync derived issues of ``codes`` with ``issues``. Returns rows written.

    ``batch_for_code``: batch id each code is attached to; codes without a batch
    are skipped (no batch of the related source exists yet). ``scope``: extra SQL
    condition limiting which stored issues may be deleted (incremental checks)."""
    codes = [c for c in codes if c in batch_for_code]
    if not codes:
        return 0
    rows: list[list[Any]] = []
    for i in issues:
        if i.code in codes:
            rows.append(_row(i, derived_issue_id(i), batch_for_code[i.code], created_at))
    keep = [r[0] for r in rows]
    extra_sql, extra_params = scope if scope is not None else ("TRUE", [])
    conn.execute(
        "DELETE FROM data_quality_issues WHERE code IN (SELECT unnest(?)) "
        "AND status IN ('open', 'auto_fixed') AND issue_id NOT IN (SELECT unnest(?)) "
        f"AND ({extra_sql})",
        [codes, keep, *extra_params],
    )
    # A user decision (confirmed / dismissed) is kept; the facts are refreshed.
    return _insert_frame(
        conn,
        rows,
        "ON CONFLICT (issue_id) DO UPDATE SET detail = excluded.detail, "
        "severity = excluded.severity, batch_id = excluded.batch_id",
    )


def issue_counts(
    conn: duckdb.DuckDBPyConnection, *, batch_ids: Sequence[str] | None = None
) -> dict[str, int]:
    sql = "SELECT code, count(*) FROM data_quality_issues"
    params: list[Any] = []
    if batch_ids is not None:
        sql += " WHERE batch_id IN (SELECT unnest(?))"
        params.append(list(batch_ids))
    sql += " GROUP BY code ORDER BY code"
    return {str(c): int(n) for c, n in conn.execute(sql, params).fetchall()}


# ====================================================================== helpers
def _df(conn: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
    return conn.execute(sql, params or []).df()


def _exists(conn: duckdb.DuckDBPyConnection, source: str) -> bool:
    rows = conn.execute(
        "SELECT count(*) FROM ingest_batches WHERE source_type = ? AND status <> 'failed'",
        [source],
    ).fetchall()
    return bool(rows and rows[0][0])


def _sold_skus_sql() -> str:
    return (
        "SELECT DISTINCT ol.sku_id FROM order_lines ol JOIN orders o USING (order_id) "
        "WHERE o.status <> 'cancelled'"
    )


def _units_out_events(
    conn: duckdb.DuckDBPyConnection, since: datetime | None = None
) -> pd.DataFrame:
    """(sku_id, ordered_at, qty) leaving the shelf: own non-cancelled orders plus
    ``qty ×`` the combo orders that contain the SKU (02 §2.3)."""
    return _df(
        conn,
        """
        WITH lines AS (
          SELECT ol.sku_id, o.ordered_at, ol.qty
          FROM order_lines ol JOIN orders o USING (order_id)
          WHERE o.status <> 'cancelled' AND o.ordered_at >= ?
        )
        SELECT sku_id, ordered_at, qty FROM lines
        UNION ALL
        SELECT b.component_sku_id AS sku_id, l.ordered_at, l.qty * b.qty AS qty
        FROM lines l JOIN bundle_components b ON b.bundle_sku_id = l.sku_id
        """,
        [since or EPOCH],
    )


# ====================================================================== rules
def check_unmatched_sku(conn: duckdb.DuckDBPyConnection) -> list[DataIssue]:
    """One ``UNMATCHED_SKU`` per alias still waiting in the queue."""
    rows = repo.fetch_all(
        conn,
        "SELECT alias_id, source_type, raw_code, raw_name, sku_id, score, method "
        "FROM sku_aliases WHERE status = 'pending' ORDER BY source_type, raw_code, raw_name",
    )
    out: list[DataIssue] = []
    for r in rows:
        out.append(
            make_issue(
                "UNMATCHED_SKU",
                entity_type="sku",
                entity_id=str(r["alias_id"]),
                row_ref=f"{r['source_type']}:{r['raw_code'] or r['raw_name']}",
                detail={
                    "alias_id": str(r["alias_id"]),
                    "source_type": str(r["source_type"]),
                    "raw_code": str(r["raw_code"]),
                    "raw_name": str(r["raw_name"]),
                    "suggested_sku": None if r["sku_id"] is None else str(r["sku_id"]),
                    "score": None if r["score"] is None else round(float(r["score"]), 4),
                    "trust": "untrusted",
                },
            )
        )
    return out


def check_missing_cost(conn: duckdb.DuckDBPyConnection) -> list[DataIssue]:
    """``MISSING_COST`` (blocking) for every sold SKU without a cost row.
    ``detail.reason``: ``no_cost_file`` (no cost file imported yet),
    ``cost_alias_pending`` (a cost line waits in the SKU queue with this SKU as
    suggestion) or ``no_cost_row`` (nothing in the cost file points to it)."""
    has_file = _exists(conn, "cost")
    rows = _df(
        conn,
        f"""
        WITH sold AS ({_sold_skus_sql()}),
        pend AS (
          SELECT sku_id, list(alias_id ORDER BY alias_id) AS aliases
          FROM sku_aliases WHERE source_type = 'cost' AND status = 'pending'
            AND sku_id IS NOT NULL GROUP BY sku_id
        )
        SELECT s.sku_id, p.aliases FROM sold s
        LEFT JOIN pend p USING (sku_id)
        WHERE s.sku_id NOT IN (SELECT DISTINCT sku_id FROM cost_items)
        ORDER BY s.sku_id
        """,
    )
    out: list[DataIssue] = []
    for sku, aliases in zip(rows["sku_id"], rows["aliases"], strict=True):
        pending = [str(a) for a in aliases] if isinstance(aliases, list | np.ndarray) else []
        if not has_file:
            reason = "no_cost_file"
        elif pending:
            reason = "cost_alias_pending"
        else:
            reason = "no_cost_row"
        out.append(
            make_issue(
                "MISSING_COST",
                entity_type="sku",
                entity_id=str(sku),
                detail={"reason": reason, "pending_aliases": pending, "blocks": "economics"},
            )
        )
    return out


def check_bundle_no_stock_row(conn: duckdb.DuckDBPyConnection) -> list[DataIssue]:
    """Sold SKU with no stock snapshot that is not a declared combo (and not the
    suggestion of a stock-sheet alias still in the queue, which is
    ``UNMATCHED_SKU``'s job)."""
    if not _exists(conn, "inventory"):
        return []
    rows = _df(
        conn,
        f"""
        WITH sold AS ({_sold_skus_sql()})
        SELECT s.sku_id, k.is_bundle FROM sold s JOIN skus k USING (sku_id)
        WHERE s.sku_id NOT IN (SELECT DISTINCT sku_id FROM inventory_snapshots)
          AND s.sku_id NOT IN (SELECT DISTINCT bundle_sku_id FROM bundle_components)
          AND s.sku_id NOT IN (SELECT sku_id FROM sku_aliases WHERE source_type = 'inventory'
                               AND status = 'pending' AND sku_id IS NOT NULL)
        ORDER BY s.sku_id
        """,
    )
    return [
        make_issue(
            "BUNDLE_NO_STOCK_ROW",
            entity_type="sku",
            entity_id=str(sku),
            detail={"action": "declare_bundle_components", "flagged_bundle": bool(b)},
        )
        for sku, b in zip(rows["sku_id"], rows["is_bundle"], strict=True)
    ]


def check_spend_over_budget(conn: duckdb.DuckDBPyConnection) -> list[DataIssue]:
    """Campaign-day (VN) whose spend exceeds the day's budget by more than 5 %."""
    rows = repo.fetch_all(
        conn,
        f"""
        SELECT a.campaign_id, CAST(a.hour_utc + {VN_OFFSET} AS DATE) AS day_local,
               SUM(a.spend) AS spend,
               COALESCE(MAX(a.daily_budget), ANY_VALUE(c.daily_budget)) AS budget
        FROM ad_hourly a JOIN campaigns c USING (campaign_id)
        WHERE a.spend IS NOT NULL
        GROUP BY 1, 2
        HAVING SUM(a.spend) > {SPEND_TOLERANCE} * COALESCE(MAX(a.daily_budget),
                                                           ANY_VALUE(c.daily_budget))
        ORDER BY 1, 2
        """,
    )
    out: list[DataIssue] = []
    for r in rows:
        day = r["day_local"].isoformat()
        out.append(
            make_issue(
                "SPEND_OVER_BUDGET",
                entity_type="campaign",
                entity_id=str(r["campaign_id"]),
                row_ref=f"{r['campaign_id']}@{day}",
                detail={
                    "date": day,
                    "spend": int(r["spend"]),
                    "budget": int(r["budget"]),
                    "ratio": round(float(r["spend"]) / float(r["budget"]), 4)
                    if r["budget"]
                    else None,
                },
            )
        )
    return out


def _local_day(ts: pd.Timestamp) -> date:
    return ts.tz_convert(VN_TZ).date()


def check_stock_jump(
    conn: duckdb.DuckDBPyConnection,
    sku_ids: Sequence[str] | None = None,
    *,
    since: datetime | None = None,
) -> list[DataIssue]:
    """``STOCK_JUMP``: walk consecutive valid snapshots of each SKU.

    ``expected = previous − units out in between + receipts``; a jump is
    ``|on_hand − expected| > max(5, 10 % · expected)``. Negative/unreadable
    snapshots (``on_hand`` NULL) are skipped, so the next valid one is compared
    with the last valid one. Receipts (``restock_receipts``, a day each) count at
    10:00 VN between daily snapshots; between hourly snapshots of a day they are
    assigned to the hour with the largest unexplained rise (the hour is unknown).

    ``since`` (live deltas): only snapshots at/after ``since`` are judged; the
    walk starts :data:`LOOKBACK` earlier so the previous valid snapshot is known."""
    where = "WHERE on_hand IS NOT NULL"
    params: list[Any] = []
    lo = since - LOOKBACK if since is not None else None
    if lo is not None:
        where += " AND snapshot_at >= ?"
        params.append(lo)
    if sku_ids is not None:
        where += " AND sku_id IN (SELECT unnest(?))"
        params.append(list(sku_ids))
    snaps = _df(
        conn,
        f"SELECT sku_id, snapshot_at, on_hand, granularity, note FROM inventory_snapshots "
        f"{where} ORDER BY sku_id, snapshot_at",
        params,
    )
    if snaps.empty:
        return []
    events = _units_out_events(conn, lo)
    receipts = _df(conn, "SELECT sku_id, received_on, qty FROM restock_receipts")
    ev_by = {str(k): g for k, g in events.groupby("sku_id", sort=False)}
    rec_by: dict[str, dict[date, int]] = {}
    for s, d, q in zip(receipts["sku_id"], receipts["received_on"], receipts["qty"], strict=True):
        day = pd.Timestamp(d).date()
        rec_by.setdefault(str(s), {})[day] = rec_by.get(str(s), {}).get(day, 0) + int(q)

    out: list[DataIssue] = []
    for sku, g in snaps.groupby("sku_id", sort=True):
        sku = str(sku)
        if len(g) < 2:
            continue
        t = pd.DatetimeIndex(g["snapshot_at"]).tz_convert("UTC")
        stock = g["on_hand"].to_numpy(dtype=float)
        gran = g["granularity"].to_numpy(dtype=object)
        notes = g["note"].to_numpy(dtype=object)
        ev = ev_by.get(sku)
        if ev is not None and len(ev):
            et = pd.DatetimeIndex(ev["ordered_at"]).tz_convert("UTC")
            order = np.argsort(et.asi8, kind="stable")
            e_ns = et.asi8[order]
            cum = np.concatenate([[0.0], np.cumsum(ev["qty"].to_numpy(dtype=float)[order])])
            out_at = cum[np.searchsorted(e_ns, t.asi8, side="left")]
        else:
            out_at = np.zeros(len(t))
        sold = np.diff(out_at)  # units out in [t_{k-1}, t_k)
        prev = stock[:-1]
        cur = stock[1:]
        rec = np.zeros(len(cur))
        days = rec_by.get(sku, {})
        hourly_days: dict[date, list[int]] = {}
        # numpy views: per-element pandas Timestamp work dominated live deltas.
        t_ns = t.as_unit("ns").asi8
        hour_day = (t - pd.Timedelta(hours=1)).tz_convert(VN_TZ).date
        rec_at = [
            (
                pd.Timestamp(
                    datetime(day.year, day.month, day.day, RECEIPT_HOUR_LOCAL, tzinfo=VN_TZ)
                ).value,
                q,
            )
            for day, q in days.items()
        ]
        for k in range(len(cur)):
            if gran[k + 1] == "hour":
                hourly_days.setdefault(hour_day[k + 1], []).append(k)
                continue
            for at_ns, q in rec_at:
                if t_ns[k] <= at_ns < t_ns[k + 1]:
                    rec[k] += q
        resid = cur - (prev - sold + rec)
        for day, ks in hourly_days.items():
            q = days.get(day, 0)
            if q:
                k_best = max(ks, key=lambda k: resid[k])
                rec[k_best] += q
                resid[k_best] -= q
        expected = prev - sold + rec
        limit = np.maximum(STOCK_JUMP_ABS, STOCK_JUMP_REL * np.maximum(expected, 0))
        for hit in np.nonzero(np.abs(resid) > limit)[0]:
            k = int(hit)
            at = t[k + 1]
            if since is not None and at < pd.Timestamp(since):
                continue
            label = (
                _local_day(at - pd.Timedelta(hours=1)).isoformat()
                if gran[k + 1] == "day"
                else at.tz_convert(VN_TZ).isoformat()
            )
            note = notes[k + 1]
            out.append(
                make_issue(
                    "STOCK_JUMP",
                    entity_type="sku",
                    entity_id=sku,
                    row_ref=f"{sku}@{at.isoformat()}",
                    detail={
                        "snapshot_at": at.isoformat(),
                        "snapshot_local": label,
                        "granularity": str(gran[k + 1]),
                        "value": int(cur[k]),
                        "expected": round(float(expected[k])),
                        "diff": round(float(resid[k])),
                        "units_out": round(float(sold[k])),
                        "receipts": int(rec[k]),
                        "note": note if isinstance(note, str) and note else None,
                        "action": "trust_snapshot",
                    },
                )
            )
    return out


def run_cross_source_checks(
    conn: duckdb.DuckDBPyConnection, *, since: datetime | None = None
) -> list[DataIssue]:
    """Every derived rule on the current warehouse content (``STOCK_JUMP`` only
    from ``since`` when given)."""
    return [
        *check_unmatched_sku(conn),
        *check_missing_cost(conn),
        *check_stock_jump(conn, since=since),
        *check_spend_over_budget(conn),
        *check_bundle_no_stock_row(conn),
    ]


def latest_batches(conn: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """source_type → latest non-failed batch id."""
    rows = conn.execute(
        "SELECT source_type, max(batch_id) FROM ingest_batches WHERE status <> 'failed' "
        "GROUP BY source_type"
    ).fetchall()
    return {str(s): str(b) for s, b in rows}


def batch_for_codes(latest: Mapping[str, str], fallback: str | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for code, sources in DERIVED_SOURCE.items():
        bid = next((latest[s] for s in sources if s in latest), fallback)
        if bid is not None:
            out[code] = bid
    return out


def refresh_all(
    conn: duckdb.DuckDBPyConnection,
    created_at: datetime,
    *,
    fallback_batch: str | None = None,
    since: datetime | None = None,
) -> tuple[list[DataIssue], int]:
    """Run every derived rule and sync ``data_quality_issues``. With ``since``
    (live delta) stock jumps are re-checked, and replaced, only from that instant."""
    issues = run_cross_source_checks(conn, since=since)
    batches = batch_for_codes(latest_batches(conn), fallback_batch)
    if since is None:
        return issues, refresh_derived_issues(conn, issues, batches, created_at)
    others = [c for c in DERIVED_CODES if c != "STOCK_JUMP"]
    written = refresh_derived_issues(conn, issues, batches, created_at, codes=others)
    written += refresh_derived_issues(
        conn,
        issues,
        batches,
        created_at,
        codes=["STOCK_JUMP"],
        scope=(
            "CAST(json_extract_string(detail, '$.snapshot_at') AS TIMESTAMPTZ) >= ?",
            [since],
        ),
    )
    return issues, written


__all__ = [
    "DERIVED_CODES",
    "batch_for_codes",
    "check_bundle_no_stock_row",
    "check_missing_cost",
    "check_spend_over_budget",
    "check_stock_jump",
    "check_unmatched_sku",
    "derived_issue_id",
    "issue_counts",
    "latest_batches",
    "refresh_all",
    "refresh_derived_issues",
    "run_cross_source_checks",
    "write_batch_issues",
]
