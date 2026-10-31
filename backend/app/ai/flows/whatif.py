"""What-if in Vietnamese (AI-06, 07 §4.2).

``WhatIfIntent`` is the **structured output** the fast model fills from the question
(strict JSON schema). :func:`resolve_intent` turns it into ``RunScenarioArgs`` (the
``run_scenario`` tool arguments, 1–1 with ``SimRequest``) or a question to ask back;
:func:`app.ai.tools.compute.to_sim_request` gives the ``SimRequest`` itself.

:func:`heuristic_intent` is a rule-based parser for the common phrasings (no LLM): used
when the AI is off and as a cross-check in tests. Examples (M3-whatif.md §4):

* "nếu sếp giao chỉ tiêu 97 cái" → ``goal_override = sell_target, target_units 97``
* "nếu hàng về sớm 1 ngày" → ``restock_delay_days = −1``
* "nếu hàng dư còn 20.000đ/sp" → ``leftover_value_per_unit = 20000``
* "giảm ngân sách 30%" → action ``reduce_budget 0.3`` (+ continue for comparison)
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field
from unidecode import unidecode

from app.ai.client import LlmClient, LlmRequest, json_schema_format, model_for, user_item
from app.ai.grounding import extract_mentions
from app.ai.prompts import system_prompt
from app.ai.tools._util import campaign_of
from app.ai.tools.registry import ToolContext
from app.ai.tools.schemas import (
    ActionArg,
    Args,
    GoalOverrideArg,
    ParamOverrideArg,
    RunScenarioArgs,
    strict_schema,
)
from app.economics.goals import active_goal


class WhatIfIntent(Args):
    campaign_id: str | None = Field(None, description="Mã chiến dịch nếu người dùng nêu")
    sku: str | None = Field(None, description="SKU nếu người dùng nêu")
    horizon_hours: int | None = Field(None, ge=1, le=72)
    actions: list[ActionArg] | None = Field(None, description="null = đủ hành động mặc định")
    param_overrides: list[ParamOverrideArg] = Field(default_factory=list)
    goal_override: GoalOverrideArg | None = None
    needs_clarification: str | None = Field(
        None, description="Câu hỏi lại người dùng khi thiếu thông tin bắt buộc"
    )
    assumptions: list[str] = Field(default_factory=list, description="Giả định, tiếng Việt")


class AskBack(BaseModel):
    question: str
    missing: str


INTENT_INSTRUCTIONS = (
    "Tách câu hỏi what-if thành JSON theo schema. Không tự tính số. Chỉ điền giá trị người"
    " dùng nêu; ngày tương đối để null và ghi vào assumptions. Thiếu thông tin bắt buộc thì"
    " điền needs_clarification."
)


def parse_intent(
    client: LlmClient, ctx: ToolContext, question: str, *, model: str | None = None
) -> WhatIfIntent:
    """Structured output (fast model, strict schema) → :class:`WhatIfIntent`."""
    req = LlmRequest(
        model=model or _model_name(client, ctx),
        instructions=system_prompt("whatif") + "\n\n" + INTENT_INSTRUCTIONS,
        input=[user_item(question)],
        tools=[],
        temperature=0.0,
        text_format=json_schema_format("whatif_intent", strict_schema(WhatIfIntent)),
        flow="whatif_intent",
        task="fast",
    )
    resp = client.create(req)
    return WhatIfIntent.model_validate(json.loads(resp.text or "{}"))


def _model_name(client: LlmClient, ctx: ToolContext) -> str:
    live = client.name == "openai" or client.name.startswith("record")
    return model_for("fast", ctx.settings) if live else client.name


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", unidecode(text).lower())


def heuristic_intent(question: str) -> WhatIfIntent:
    """Rule-based parser for the demo phrasings (no LLM)."""
    q = _key(question)
    overrides: list[ParamOverrideArg] = []
    actions: list[ActionArg] | None = None
    goal: GoalOverrideArg | None = None
    assumptions: list[str] = []
    m = re.search(
        r"(chi tieu|ban du|ban duoc|muc tieu ban)\D{0,20}(\d+)\s*(cai|sp|san pham|chiec)?", q
    )
    if m:
        n = int(m.group(2))
        goal = GoalOverrideArg(kind="sell_target", target_units=n)
        assumptions.append(f"Mục tiêu giả định: bán đủ {n} sp trước hạn hiện tại")
    m = re.search(r"ve (som|tre|cham) (\d+) ngay", q)
    if m:
        d = int(m.group(2))
        overrides.append(
            ParamOverrideArg(key="restock_delay_days", value=-d if m.group(1) == "som" else d)
        )
        assumptions.append(f"Hàng về {'sớm' if m.group(1) == 'som' else 'trễ'} {d} ngày")
    if "hang du" in q or "con lai sau han" in q:
        vnd = [x for x in extract_mentions(question) if x.unit == "vnd"]
        if vnd:
            overrides.append(ParamOverrideArg(key="leftover_value_per_unit", value=vnd[0].value))
            assumptions.append(f"Giá trị hàng dư {vnd[0].raw}/sp")
    m = re.search(r"giam (ngan sach|budget)\D{0,10}(\d+)\s*%", q)
    if m:
        pct = int(m.group(2)) / 100
        actions = [
            ActionArg(kind="continue"),
            ActionArg(kind="reduce_budget", pct=pct),
        ]
    if re.search(r"\bxa (hang|het)\b", q) and goal is None:
        goal = GoalOverrideArg(kind="clear_stock")
    camp = re.search(r"\b(cd\d{3})\b", q)
    sku = re.search(r"\b([A-Z][A-Z0-9]{3,}(?:-[A-Z0-9]+)*)\b", question)
    return WhatIfIntent(
        campaign_id=camp.group(1).upper() if camp else None,
        sku=sku.group(1) if sku and not (camp and sku.group(1) == camp.group(1).upper()) else None,
        actions=actions,
        param_overrides=overrides,
        goal_override=goal,
        assumptions=assumptions,
    )


def resolve_intent(
    ctx: ToolContext, intent: WhatIfIntent, *, default_campaign: str | None = None
) -> RunScenarioArgs | AskBack:
    """Fill the campaign (from the SKU or the screen context) and check what the simulator
    needs; otherwise return the question to ask back (07 §4.2 step 2)."""
    if intent.needs_clarification:
        return AskBack(question=intent.needs_clarification, missing="llm")
    cid = intent.campaign_id
    if cid is None and intent.sku:
        cid = campaign_of(ctx.conn, intent.sku)
    cid = cid or default_campaign
    if cid is None:
        return AskBack(
            question="Bạn muốn xem cho chiến dịch hoặc sản phẩm nào?", missing="campaign_id"
        )
    g = intent.goal_override
    if g is not None and g.kind == "sell_target" and not g.target_units:
        return AskBack(question="Chỉ tiêu cần bán là bao nhiêu sản phẩm?", missing="target_units")
    if g is None and active_goal(ctx.conn, cid) is None:
        return AskBack(
            question=f"Chiến dịch {cid} chưa có mục tiêu. Lead cần đặt mục tiêu trước, hay bạn"
            " muốn xem theo mục tiêu lợi nhuận giả định?",
            missing="goal",
        )
    if (
        g is not None
        and g.kind == "clear_stock"
        and not any(o.key == "leftover_value_per_unit" for o in intent.param_overrides)
    ):
        return AskBack(
            question="Xả hàng: mỗi sản phẩm còn lại sau hạn đáng bao nhiêu (so với giá vốn)?"
            " Mặc định đang là 0đ (bán sau vẫn hòa vốn).",
            missing="leftover_value_per_unit",
        )
    return RunScenarioArgs(
        campaign_id=cid,
        horizon_hours=intent.horizon_hours,
        actions=intent.actions,
        param_overrides=intent.param_overrides,
        goal_override=g,
    )


def run_args_json(args: RunScenarioArgs) -> dict[str, Any]:
    """Arguments of the ``run_scenario`` call as the LLM sends them (strict: every key)."""
    return json.loads(args.model_dump_json(by_alias=True))
