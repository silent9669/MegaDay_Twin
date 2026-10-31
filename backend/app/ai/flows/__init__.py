"""The five flows of 07 §4 (+ a general chat flow): system prompt part, tool subset and
model task per flow. Flow logic that runs without the LLM lives in the sub-modules
(``whatif.py``: intent → ``SimRequest``; ``investigate.py``: 6-step checklist)."""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.client import ModelTask

READ_COMMON = ("get_data_freshness", "list_skus", "get_sku_snapshot")


@dataclass(frozen=True)
class Flow:
    name: str
    task: ModelTask
    tools: tuple[str, ...]
    max_steps: int = 8
    temperature: float | None = 0.2


FLOWS: dict[str, Flow] = {
    f.name: f
    for f in (
        Flow(
            "whatif",
            "reasoning",
            (
                *READ_COMMON,
                "get_forecast",
                "get_iroas",
                "get_goal_and_parameters",
                "run_scenario",
                "compare_actions",
                "compute_breakeven",
                "draft_decision",
                "request_approval",
                "suggest_parameter",
            ),
        ),
        Flow(
            "investigate",
            "reasoning",
            (
                *READ_COMMON,
                "check_data_quality",
                "get_sales_series",
                "get_forecast",
                "get_inventory_events",
                "get_campaign_status",
                "get_price_events",
                "compare_similar_skus",
                "search_order_notes",
                "list_alerts",
            ),
        ),
        Flow(
            "explain",
            "reasoning",
            (
                "compare_actions",
                "compute_breakeven",
                "get_iroas",
                "get_sku_snapshot",
                "get_forecast",
                "get_goal_and_parameters",
                "draft_decision",
            ),
        ),
        Flow(
            "handoff",
            "fast",
            ("list_decisions", "list_alerts", "get_data_freshness", "draft_shift_handoff"),
        ),
        Flow(
            "ingest",
            "fast",
            (
                "profile_file",
                "propose_column_mapping",
                "suggest_sku_matches",
                "propose_sku_matches",
                "check_data_quality",
            ),
        ),
        Flow(
            "chat",
            "reasoning",
            (
                *READ_COMMON,
                "get_sales_series",
                "get_forecast",
                "get_iroas",
                "get_goal_and_parameters",
                "run_scenario",
                "compare_actions",
                "compute_breakeven",
                "check_data_quality",
                "get_campaign_status",
                "get_price_events",
                "get_inventory_events",
                "compare_similar_skus",
                "list_alerts",
                "list_decisions",
                "search_order_notes",
                "draft_decision",
                "suggest_parameter",
            ),
        ),
    )
}


def get_flow(name: str) -> Flow:
    return FLOWS.get(name, FLOWS["chat"])
