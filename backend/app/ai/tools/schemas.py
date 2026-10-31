"""Tool parameter models (07 §3 "Schema chi tiết") and the strict JSON-schema exporter.

OpenAI strict function calling / structured outputs need: every property listed in
``required``, ``additionalProperties: false`` on every object, optional fields typed as
``["…", "null"]``, no ``$ref`` surprises. Pydantic's schema is post-processed by
:func:`strict_schema`; numeric / length bounds are moved into the description (some are
not accepted in strict mode) and are enforced again by pydantic when the call is parsed.

Every optional field is declared ``X | None = None`` so the model may send ``null``.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_BOUND_KEYS = {
    "minimum": "≥",
    "maximum": "≤",
    "exclusiveMinimum": ">",
    "exclusiveMaximum": "<",
    "minItems": "ít nhất (phần tử)",
    "maxItems": "tối đa (phần tử)",
    "minLength": "dài ít nhất",
    "maxLength": "dài tối đa",
}
_DROP_KEYS = {"title", "default", "format", "pattern", "examples"}


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic model → strict JSON schema (inlined ``$defs``)."""
    raw = model.model_json_schema(by_alias=True)
    defs: dict[str, Any] = raw.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                merged = copy.deepcopy(defs[name])
                extra = {k: v for k, v in node.items() if k != "$ref"}
                merged.update(extra)
                return resolve(merged)
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    def fix(node: Any) -> Any:
        if isinstance(node, list):
            return [fix(v) for v in node]
        if not isinstance(node, dict):
            return node
        notes = []
        for k, label in _BOUND_KEYS.items():
            if k in node:
                notes.append(f"{label} {node.pop(k)}")
        fmt = node.get("format")
        if fmt:
            notes.append(f"định dạng {fmt}")
        for k in _DROP_KEYS:
            node.pop(k, None)
        if notes:
            desc = node.get("description", "")
            node["description"] = (desc + " " if desc else "") + "(" + ", ".join(notes) + ")"
        if "anyOf" in node:
            node["anyOf"] = [fix(v) for v in node["anyOf"]]
        if node.get("type") == "object" or "properties" in node:
            node["type"] = "object"
            props = node.get("properties", {})
            node["properties"] = {k: fix(v) for k, v in props.items()}
            node["required"] = list(props)
            node["additionalProperties"] = False
        if "items" in node:
            node["items"] = fix(node["items"])
        return node

    out: dict[str, Any] = fix(resolve(raw))
    return out


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------- read
class NoArgs(Args):
    pass


class ListSkusArgs(Args):
    q: str | None = Field(None, description="Tìm theo mã hoặc tên (không dấu cũng được)")
    campaign_id: str | None = Field(None, description="Chỉ SKU của chiến dịch này")
    limit: int | None = Field(None, ge=1, le=50, description="Mặc định 20")


class SkuArgs(Args):
    sku: str = Field(description="SKU sàn, ví dụ NOICHIEN2L")


class SalesSeriesArgs(Args):
    sku: str
    grain: Literal["hour", "day"]
    from_: str = Field(alias="from", description="Thời điểm bắt đầu, ISO 8601 (+07:00)")
    to: str = Field(description="Thời điểm kết thúc (không gồm), ISO 8601")


class ForecastArgs(Args):
    sku: str
    horizon_hours: int = Field(ge=1, le=48)
    action: Literal["continue", "pause"]


class IroasArgs(Args):
    campaign_id: str | None = None
    sku: str | None = None


class GoalParamsArgs(Args):
    sku: str | None = None
    campaign_id: str | None = None


class ActionArg(Args):
    kind: Literal["continue", "pause", "reduce_budget", "schedule_hours", "pause_until_restock"]
    pct: float | None = Field(None, ge=0.05, le=0.95, description="reduce_budget: 0.3 = giảm 30%")
    windows_local: list[list[int]] | None = Field(
        None, description="schedule_hours: [[0,2],[12,14]] giờ Việt Nam, [đầu, cuối)"
    )


class ParamOverrideArg(Args):
    key: str = Field(description="Khóa trong danh mục tham số, ví dụ restock_delay_days")
    value: float


# [DEV] What-if on the goal itself ("nếu sếp giao chỉ tiêu 97 cái"): the simulation runs under
# a hypothetical goal that is never stored (the AI never creates goals). No docstring on
# purpose: pydantic would send it to the LLM as the schema description.
class GoalOverrideArg(Args):
    kind: Literal["profit", "sell_target", "clear_stock", "efficiency"]
    target_units: int | None = Field(None, ge=1, description="sell_target: số sp cần bán")
    deadline: str | None = Field(None, description="Hạn, ISO 8601; null = hạn mục tiêu hiện tại")
    sell_target_prob: float | None = Field(None, ge=0.5, le=0.99)
    max_loss_vnd: float | None = Field(None, ge=0)


class RunScenarioArgs(Args):
    campaign_id: str = Field(description="Mã chiến dịch, ví dụ CD002. Chưa biết thì lấy từ"
                             " get_sku_snapshot.")  # fmt: skip
    horizon_hours: int | None = Field(None, ge=1, le=72, description="null = tới hạn mục tiêu")
    actions: list[ActionArg] | None = Field(
        None, max_length=5, description="null = chạy đủ các hành động mặc định"
    )
    param_overrides: list[ParamOverrideArg] = Field(
        default_factory=list,
        description="Giả định người dùng nêu; key phải có trong danh mục tham số",
    )
    goal_override: GoalOverrideArg | None = Field(
        None, description="Giả định về mục tiêu (không lưu); null = mục tiêu đang áp dụng"
    )


class CompareActionsArgs(Args):
    campaign_id: str
    horizon_hours: int | None = Field(None, ge=1, le=72)


class BreakevenArgs(Args):
    sku: str
    horizon_hours: int | None = Field(None, ge=1, le=72)
    leftover_value_per_unit: float | None = Field(None, ge=0)


class DataQualityArgs(Args):
    sku: str | None = None
    severity: Literal["any", "blocking"] | None = None


class CampaignArgs(Args):
    campaign_id: str


class SkuRangeArgs(Args):
    sku: str
    from_: str | None = Field(None, alias="from", description="ISO 8601; null = 2 ngày trước")
    to: str | None = Field(None, description="ISO 8601; null = giờ dữ liệu hiện tại")


class SimilarArgs(Args):
    sku: str
    hours: int | None = Field(None, ge=1, le=72, description="Khung giờ gần nhất, mặc định 3")


class AlertsArgs(Args):
    status: Literal["open", "all"] | None = None
    since: str | None = None


class DecisionsArgs(Args):
    since: str | None = None


class OrderNotesArgs(Args):
    sku: str | None = None
    product_id: str | None = Field(None, description="Mọi biến thể của sản phẩm")
    from_: str = Field(alias="from")
    to: str
    limit: int | None = Field(None, ge=1, le=50)


class BatchArgs(Args):
    batch_id: str


class ColumnMap(Args):
    source_column: str
    target_field: Literal[
        "order_id", "ordered_at", "status", "product_name", "variant_name", "sku",
        "list_price", "effective_price", "qty", "line_total", "buyer_name", "buyer_phone",
        "buyer_address", "buyer_note", "ad_time", "campaign_id", "campaign_name",
        "campaign_status", "daily_budget", "spend", "impressions", "clicks",
        "attributed_orders", "attributed_revenue", "snapshot_date", "internal_code",
        "internal_name", "stock", "incoming_qty", "incoming_date", "stock_note", "cost_name",
        "unit_cost", "packaging_cost", "cost_note", "ignore",
    ]  # fmt: skip
    transform: Literal["none", "utc_to_gmt7", "parse_vnd", "strip_pii", "parse_date_vn"]
    confidence: float = Field(ge=0, le=1)


class ColumnMappingArgs(Args):
    batch_id: str
    file_kind: Literal["orders", "ads", "inventory", "cost", "campaigns", "commitments", "unknown"]
    mappings: list[ColumnMap]
    warnings: list[str]


class SuggestSkuArgs(Args):
    batch_id: str | None = Field(None, description="null = mọi tên chưa ghép")
    limit: int | None = Field(None, ge=1, le=50)


class SkuMatch(Args):
    alias: str = Field(description="alias_id trong hàng chờ ghép")
    sku: str
    confidence: float = Field(ge=0, le=1)


class ProposeSkuArgs(Args):
    batch_id: str | None = None
    matches: list[SkuMatch]


class DraftDecisionArgs(Args):
    campaign_id: str
    action: ActionArg
    scenario_id: str
    rationale: str


class RequestApprovalArgs(Args):
    decision_id: str


class HandoffArgs(Args):
    from_: str | None = Field(None, alias="from")
    to: str | None = None


class SuggestParameterArgs(Args):
    key: str
    value: float
    scope: Literal["sku", "campaign", "shop", "session"]
    scope_id: str | None = None
    reason: str
