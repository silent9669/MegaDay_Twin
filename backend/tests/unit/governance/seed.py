"""Minimal rows for governance tests: one stored scenario with 3 results + its recommendation
(no mock data needed)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import duckdb

from app.core.ids import new_id

AS_OF = datetime(2026, 11, 11, 7, 0, tzinfo=UTC)  # 14:00 VN
CONTINUE = {"kind": "continue", "pct": None, "windows_local": None}
REDUCE = {"kind": "reduce_budget", "pct": 0.3, "windows_local": None}
PAUSE = {"kind": "pause", "pct": None, "windows_local": None}


def _dist(m: float) -> str:
    return json.dumps({"mean": m, "p10": m * 0.8, "p50": m, "p90": m * 1.2})


def add_batch(
    conn: duckdb.DuckDBPyConnection, source: str = "orders", as_of: datetime = AS_OF
) -> str:
    bid = new_id("bat")
    conn.execute(
        "INSERT INTO ingest_batches (batch_id, source_type, file_name, file_sha256, received_at,"
        " as_of, mode, status) VALUES (?, ?, ?, ?, ?, ?, 'live', 'ok')",
        [bid, source, f"{bid}.csv", bid, as_of, as_of],
    )
    return bid


def seed_scenario(
    conn: duckdb.DuckDBPyConnection,
    *,
    campaign_id: str = "CD002",
    overrides: dict[str, Any] | None = None,
    data_as_of: datetime = AS_OF,
    best: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Returns ``(scenario_id, recommendation_id)``. Results: continue + reduce 30 % feasible,
    pause infeasible; best = reduce 30 % unless given."""
    sid = new_id("scn")
    rid = new_id("rec")
    ref = {
        "forecast_ids": ["fc-1"],
        "estimate_id": None,
        "param_version": {},
        "goal_id": "goal-1",
        "data_as_of": data_as_of.isoformat(),
        "panel_version": "p1",
        "iroas_source": "prior",
    }
    conn.execute(
        "INSERT INTO scenarios (scenario_id, campaign_id, goal_id, start_at, horizon_hours,"
        " param_version, overrides, inputs_ref, samples, seed, requested_by, created_at,"
        " input_hash) VALUES (?, ?, 'goal-1', ?, 10, '{}', ?, ?, 300, 20261111, 'user', ?, ?)",
        [
            sid,
            campaign_id,
            data_as_of,
            json.dumps(overrides or {}),
            json.dumps(ref),
            data_as_of + timedelta(minutes=1),
            "hash-" + sid,
        ],
    )
    for action, feasible, viol, obj in (
        (CONTINUE, True, [], 1_000_000.0),
        (REDUCE, True, [], 1_200_000.0),
        (PAUSE, False, ["flash_sale_commitment"], 900_000.0),
    ):
        conn.execute(
            "INSERT INTO scenario_results (scenario_id, action, feasible, violations, objective,"
            " contribution, spend, units_sold, leftover_units, leftover_value,"
            " stockout_hour_p50, per_sku) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '[]')",
            [
                sid,
                json.dumps(action),
                feasible,
                viol,
                _dist(obj),
                _dist(obj),
                _dist(200_000),
                _dist(50),
                _dist(10),
                _dist(100_000),
            ],
        )
    conn.execute(
        "INSERT INTO recommendations (recommendation_id, scenario_id, campaign_id, status,"
        " best_action, runner_up, p_best, expected_gain_vnd, confidence, reasons, created_at,"
        " input_hash) VALUES (?, ?, ?, 'recommend', ?, ?, 0.8, 200000, 'verified', [], ?, ?)",
        [
            rid,
            sid,
            campaign_id,
            json.dumps(best or REDUCE),
            json.dumps(CONTINUE),
            data_as_of + timedelta(minutes=1),
            "hash-" + sid,
        ],
    )
    return sid, rid


def add_blocking_issue(conn: duckdb.DuckDBPyConnection, entity_id: str = "CD002") -> str:
    bid = add_batch(conn, "cost")
    iid = new_id("iss")
    conn.execute(
        "INSERT INTO data_quality_issues (issue_id, batch_id, code, severity, entity_type,"
        " entity_id, status, created_at) VALUES (?, ?, 'MISSING_COST', 'error', 'campaign', ?,"
        " 'open', ?)",
        [iid, bid, entity_id, AS_OF],
    )
    return iid
