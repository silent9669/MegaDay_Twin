"""Shared demand labels. Semantic labels never double as forecasting confidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

import duckdb

POLICY_VERSION = "demand-selection-v1"
PRIMARY_FROM_HISTORY = {"on_dinh": "Normal", "mua_vu": "Seasonal", "trend": "Trendy"}


def inventory_hash(conn: duckdb.DuckDBPyConnection) -> str | None:
    row = conn.execute(
        "SELECT file_sha256 FROM ingest_batches WHERE source_type='inventory' "
        "AND status IN ('ok','ok_with_issues') ORDER BY received_at DESC, batch_id DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else None


def inventory_mapping(conn: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """Accept only a unique resolved identity. Names are not canonical identifiers."""
    rows = conn.execute(
        "SELECT raw_code, sku_id, status FROM sku_aliases WHERE source_type='inventory'"
    ).fetchall()
    candidates: dict[str, set[str]] = {}
    blocked: set[str] = set()
    for code, sku, status in rows:
        if not code:
            continue
        if status not in ("confirmed", "auto_accepted") or sku is None:
            blocked.add(str(code))
        else:
            candidates.setdefault(str(code), set()).add(str(sku))
    return {
        code: next(iter(ids))
        for code, ids in candidates.items()
        if len(ids) == 1 and code not in blocked
    }


def active_labels(
    conn: duckdb.DuckDBPyConnection, at: datetime | None = None
) -> dict[str, dict[str, Any]]:
    fingerprint = inventory_hash(conn)
    if not fingerprint:
        return {}
    mapping = inventory_mapping(conn)
    rows = conn.execute(
        "SELECT sku_id, raw_code, payload FROM sku_demand_labels "
        "WHERE inventory_sha256=? AND (? IS NULL OR assessed_at<=?) "
        "ORDER BY CAST(payload->>'completed_at' AS TIMESTAMPTZ), synced_at, entry_id",
        [fingerprint, at, at],
    ).fetchall()
    out = {}
    for sku, code, payload in rows:
        if mapping.get(code) == sku:
            out[sku] = json.loads(payload)
    return out


def forecast_signature(conn: duckdb.DuckDBPyConnection, at: datetime) -> str:
    """Conservative reuse guard: data, labels, parameters and model scores all matter."""
    tables = (
        "hourly_panel",
        "parameters",
        "model_scores",
        "model_registry",
        "sale_commitments",
        "products",
        "sku_aliases",
    )
    values = [
        (table, conn.execute(f"SELECT count(*), bit_xor(hash(t)) FROM {table} t").fetchone())
        for table in tables
    ]
    # Auto profiles are derived at each forecast origin, not new source data.
    # Include manual overrides, but do not invalidate a forecast just because
    # a replay at another origin refreshed the derived profile/timestamp.
    values.append(
        (
            "skus",
            conn.execute(
                "SELECT count(*), bit_xor(hash(t)) FROM (SELECT sku_id, product_id, "
                "variant_name, list_price, is_bundle, first_sale_at, "
                "CASE WHEN demand_group_source='user' THEN demand_group END AS manual_group "
                "FROM skus) t"
            ).fetchone(),
        )
    )
    return hashlib.sha256(
        json.dumps(
            [POLICY_VERSION, values, active_labels(conn, at)], sort_keys=True, default=str
        ).encode()
    ).hexdigest()


def demand_summary(conn: duckdb.DuckDBPyConnection, sku: str) -> dict[str, Any]:
    label = active_labels(conn).get(sku)
    row = conn.execute("SELECT demand_group FROM skus WHERE sku_id=?", [sku]).fetchone()
    group = str(row[0]) if row and row[0] else "unclassified"
    forecast = conn.execute(
        "SELECT payload FROM forecasts WHERE sku_id=? ORDER BY created_at DESC LIMIT 1", [sku]
    ).fetchone()
    selection = json.loads(forecast[0]).get("model_selection") if forecast else None
    return {
        "primary_label": label["label"] if label else PRIMARY_FROM_HISTORY.get(group),
        "source": ("warehouse_manual" if label.get("manual") else "warehouse_ai")
        if label
        else "history"
        if group in PRIMARY_FROM_HISTORY
        else None,
        "label_evidence": label,
        "history_profile": group,
        "model_selection": selection,
    }
