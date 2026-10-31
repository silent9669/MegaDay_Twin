"""AI-06 · what-if intent (structured output) → run_scenario args / SimRequest."""

from __future__ import annotations

import json

import duckdb
import pytest

from app.ai.client import FakeClient, LlmRequest, LlmResponse
from app.ai.flows.whatif import (
    AskBack,
    WhatIfIntent,
    heuristic_intent,
    parse_intent,
    resolve_intent,
)
from app.ai.tools import ToolContext
from app.ai.tools.compute import to_sim_request
from app.ai.tools.schemas import RunScenarioArgs
from app.core.config import Settings


@pytest.mark.parametrize(
    ("question", "check"),
    [
        (
            "Nếu sếp giao chỉ tiêu 97 cái hôm nay thì sao?",
            lambda i: i.goal_override.kind == "sell_target" and i.goal_override.target_units == 97,
        ),
        (
            "Nếu hàng nồi chiên về sớm 1 ngày thì sao?",
            lambda i: [(o.key, o.value) for o in i.param_overrides] == [("restock_delay_days", -1)],
        ),
        (
            "Hàng về trễ 2 ngày thì CD002 thế nào?",
            lambda i: i.param_overrides[0].value == 2 and i.campaign_id == "CD002",
        ),
        (
            "Nếu hàng dư còn 20.000đ/sp thì sao?",
            lambda i: (
                [(o.key, o.value) for o in i.param_overrides]
                == [("leftover_value_per_unit", 20000)]
            ),
        ),
        (
            "Giảm ngân sách 30% thay vì chỉ chạy khung flash sale thì sao?",
            lambda i: (
                [(a.kind, a.pct) for a in i.actions] == [("continue", None), ("reduce_budget", 0.3)]
            ),
        ),
    ],
)
def test_heuristic_intent(question: str, check: object) -> None:
    intent = heuristic_intent(question)
    assert callable(check) and check(intent), intent


def test_structured_output_request_and_parse(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    seen: list[LlmRequest] = []

    def step(req: LlmRequest) -> LlmResponse:
        seen.append(req)
        return FakeClient.say(
            json.dumps(
                {
                    "campaign_id": "CD002",
                    "sku": None,
                    "horizon_hours": None,
                    "actions": None,
                    "param_overrides": [],
                    "goal_override": {
                        "kind": "sell_target",
                        "target_units": 97,
                        "deadline": None,
                        "sell_target_prob": None,
                        "max_loss_vnd": None,
                    },
                    "needs_clarification": None,
                    "assumptions": ["Mục tiêu giả định: bán đủ 97 sp"],
                }
            )
        )

    ctx = ToolContext(conn=mem_conn, settings=ai_settings)
    intent = parse_intent(FakeClient([step]), ctx, "Nếu sếp giao chỉ tiêu 97 cái?")
    fmt = seen[0].text_format
    assert fmt is not None and fmt["type"] == "json_schema" and fmt["strict"] is True
    assert fmt["schema"]["additionalProperties"] is False
    assert intent.goal_override is not None and intent.goal_override.target_units == 97


def test_resolve_asks_back_when_info_missing(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    ctx = ToolContext(conn=mem_conn, settings=ai_settings)
    r = resolve_intent(ctx, WhatIfIntent())
    assert isinstance(r, AskBack) and r.missing == "campaign_id"
    r = resolve_intent(ctx, heuristic_intent("CD002 nếu xả hàng thì sao"))
    assert isinstance(r, AskBack) and r.missing == "leftover_value_per_unit"
    r = resolve_intent(ctx, heuristic_intent("CD002 giảm ngân sách 30% thì sao"))
    assert isinstance(r, AskBack) and r.missing == "goal"  # no active goal in an empty DB
    r = resolve_intent(ctx, heuristic_intent("CD002 nếu sếp giao chỉ tiêu 97 cái"))
    assert isinstance(r, RunScenarioArgs) and r.goal_override is not None


def test_to_sim_request(mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings) -> None:
    ctx = ToolContext(conn=mem_conn, settings=ai_settings)
    args = RunScenarioArgs.model_validate(
        {
            "campaign_id": "CD002",
            "horizon_hours": 23,
            "actions": [{"kind": "reduce_budget", "pct": 0.3, "windows_local": None}],
            "param_overrides": [
                {"key": "restock_delay_days", "value": -1},
                {"key": "khong_co", "value": 1},
            ],
            "goal_override": None,
        }
    )
    req = to_sim_request(ctx, args)
    assert req.campaign_id == "CD002" and req.horizon_hours == 23
    assert req.param_overrides == {"restock_delay_days": -1.0}  # unknown key dropped
    assert req.actions is not None and req.actions[0].key == "reduce_budget:0.3"
