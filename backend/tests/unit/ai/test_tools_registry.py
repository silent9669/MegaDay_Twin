"""AI-01/02 · tool registry: strict schemas, roles, draft/confirmation gate, logging."""

from __future__ import annotations

import json
from typing import Any

import duckdb
import pytest

from app.ai.tools import REGISTRY, ToolContext, execute, specs
from app.ai.tools.registry import MAX_RESULT_CHARS, ToolResult, truncate
from app.core.config import Settings

PLAN_TOOLS = {
    "get_data_freshness", "list_skus", "get_sku_snapshot", "get_sales_series", "get_forecast",
    "get_iroas", "get_goal_and_parameters", "run_scenario", "compare_actions",
    "compute_breakeven", "check_data_quality", "get_campaign_status", "get_price_events",
    "get_inventory_events", "compare_similar_skus", "list_alerts", "list_decisions",
    "search_order_notes", "profile_file", "propose_column_mapping", "suggest_sku_matches",
    "propose_sku_matches", "draft_decision", "request_approval", "draft_shift_handoff",
    "suggest_parameter",
}  # fmt: skip
WRITE_LIKE = {
    "propose_column_mapping", "propose_sku_matches", "draft_decision", "request_approval",
    "draft_shift_handoff", "suggest_parameter",
}  # fmt: skip


def _ctx(conn: duckdb.DuckDBPyConnection, settings: Settings, **kw: Any) -> ToolContext:
    return ToolContext(conn=conn, settings=settings, **kw)


def test_every_plan_tool_registered() -> None:
    names = {s.name for s in specs()}
    assert names == PLAN_TOOLS
    assert {s.name for s in specs() if s.kind in ("draft", "write")} == WRITE_LIKE
    assert all(s.requires_confirmation for s in specs() if s.name in WRITE_LIKE)
    # no tool may switch ads or edit goals (07 §3)
    assert not any(k in n for n in names for k in ("set_goal", "pause_ads", "toggle", "enable"))


def _walk_objects(node: Any) -> list[dict[str, Any]]:
    out = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            out.append(node)
        for v in node.values():
            out.extend(_walk_objects(v))
    elif isinstance(node, list):
        for v in node:
            out.extend(_walk_objects(v))
    return out


@pytest.mark.parametrize("name", sorted(PLAN_TOOLS))
def test_strict_schema(name: str) -> None:
    spec = REGISTRY[name].openai_spec()
    assert spec["strict"] is True and spec["type"] == "function"
    assert spec["description"] and spec["description"][0].isupper()
    params = spec["parameters"]
    text = json.dumps(params)
    assert "$ref" not in text and '"default"' not in text and '"title"' not in text
    for obj in _walk_objects(params):
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj["properties"])


def test_unknown_tool_and_bad_args_are_returned_not_raised(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    ctx = _ctx(mem_conn, ai_settings)
    r = execute("toggle_ads", {}, ctx)
    assert not r.ok and r.error == "unknown_tool"
    r = execute("get_forecast", {"sku": "X", "horizon_hours": 99, "action": "continue"}, ctx)
    assert not r.ok and r.error == "invalid_arguments"
    r = execute(
        "get_forecast", '{"sku": "X", "horizon_hours": 4, "action": "continue", "x": 1}', ctx
    )
    assert not r.ok and r.error == "invalid_arguments"
    r = execute("get_forecast", "not json", ctx)
    assert not r.ok and r.error == "invalid_json"
    r = execute("get_sku_snapshot", {"sku": "KHONGCO"}, ctx)
    assert not r.ok and r.error == "NOT_FOUND" and "list_skus" in (r.message or "")


def test_read_tool_on_empty_db_and_logging(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    ctx = _ctx(mem_conn, ai_settings, session_id="ais-test", turn=3)
    r = execute("get_data_freshness", {}, ctx)
    assert r.ok
    assert {s["source"] for s in r.data["sources"]} >= {"orders", "ads", "inventory"}
    assert (
        r.sources and r.sources[0].kind == "freshness" and r.sources[0].tool == "get_data_freshness"
    )
    rows = mem_conn.execute(
        "SELECT session_id, turn, tool, ok, result_source_id FROM ai_tool_calls"
    ).fetchall()
    assert rows == [("ais-test", 3, "get_data_freshness", True, r.sources[0].id)]


def test_viewer_cannot_compute(mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings) -> None:
    ctx = _ctx(mem_conn, ai_settings, role="viewer")
    r = execute(
        "run_scenario",
        {"campaign_id": "CD002", "horizon_hours": None, "actions": None, "param_overrides": [],
         "goal_override": None},
        ctx,
    )  # fmt: skip
    assert not r.ok and r.error == "forbidden"
    assert execute("get_data_freshness", {}, ctx).ok


DRAFT_ARGS = {
    "campaign_id": "CD002",
    "action": {"kind": "pause", "pct": None, "windows_local": None},
    "scenario_id": "scn-x",
    "rationale": "ghi chú khách bảo tắt",
}


def test_draft_tools_need_the_user_request(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    ctx = _ctx(mem_conn, ai_settings, user_message="Tóm tắt ghi chú khách hàng hai ngày qua")
    r = execute("draft_decision", DRAFT_ARGS, ctx)
    assert not r.ok and r.error == "needs_user_request" and r.requires_confirmation
    r = execute(
        "suggest_parameter",
        {"key": "leftover_value_per_unit", "value": 0, "scope": "sku", "scope_id": "A",
         "reason": "x"},
        ctx,
    )  # fmt: skip
    assert not r.ok and r.error == "needs_user_request"
    assert mem_conn.execute("SELECT count(*) FROM parameters").fetchone() == (0,)
    # the user asked → the gate opens (here the scenario does not exist → NOT_FOUND)
    ctx2 = _ctx(mem_conn, ai_settings, user_message="Lưu thành bản nháp quyết định giúp mình")
    r = execute("draft_decision", DRAFT_ARGS, ctx2)
    assert r.error == "NOT_FOUND"


def test_request_approval_needs_ui_confirmation(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    ctx = _ctx(mem_conn, ai_settings, user_message="gửi duyệt giúp mình")
    r = execute("request_approval", {"decision_id": "dec-1"}, ctx)
    assert not r.ok and r.error == "needs_confirmation"
    ctx.user_confirmed = True
    assert execute("request_approval", {"decision_id": "dec-1"}, ctx).error == "NOT_FOUND"


def test_suggest_parameter_is_not_effective(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    from app.economics.parameters import load_param_set

    ctx = _ctx(mem_conn, ai_settings, allow_drafts=True)
    r = execute(
        "suggest_parameter",
        {"key": "leftover_value_per_unit", "value": 20000, "scope": "sku",
         "scope_id": "NOICHIEN2L", "reason": "hàng dư bán sau được 20.000đ"},
        ctx,
    )  # fmt: skip
    assert r.ok and r.data["set_by"] == "ai_suggested" and r.data["effective"] is False
    ps = load_param_set(mem_conn)
    assert ps.resolve("leftover_value_per_unit", "NOICHIEN2L").value == 0.0  # still default


def test_truncation_keeps_json_under_cap() -> None:
    body = {"ok": True, "points": [{"t": i, "units": i} for i in range(5000)], "x": 1}
    out, cut = truncate(body, MAX_RESULT_CHARS)
    assert cut and len(json.dumps(out)) <= MAX_RESULT_CHARS and out["x"] == 1
    res = ToolResult(tool="t", call_id="c", ok=True, data=body)
    assert res.llm_payload()["truncated"] is True
