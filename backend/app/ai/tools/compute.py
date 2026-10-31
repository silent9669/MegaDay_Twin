"""Compute tools (AI-03): ``run_scenario``, ``compare_actions``, ``compute_breakeven``.

They map 1–1 onto ``SimRequest`` (05 §1) and call :func:`app.simulator.service.run_simulation`
exactly like ``POST /scenarios/run``, under the same locks (``app.simulator.locks``: live
write lock, then ``RUN_LOCK``). Nothing here changes business state:

* a run **without** assumptions is persisted like an API run (scenario id usable by
  ``draft_decision``);
* a run **with** ``param_overrides`` or ``goal_override`` is a what-if: ``persist=False``,
  labelled ``is_scenario``. A hypothetical goal ("nếu sếp giao chỉ tiêu 97 cái") is inserted
  inside a transaction that is always rolled back, so ``goals`` never holds an AI goal
  (05 §4, 10 §1).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from app.ai.tools._util import (
    campaign_of,
    iso,
    label_until,
    now_data,
    parse_ts,
    r2,
    require_campaign,
    require_sku,
)
from app.ai.tools.registry import Source, ToolContext, ToolData, tool
from app.ai.tools.schemas import (
    BreakevenArgs,
    CompareActionsArgs,
    GoalOverrideArg,
    RunScenarioArgs,
)
from app.core.errors import AppError, DataQualityError, ValidationFailed
from app.core.ids import new_id
from app.core.timeutil import local_date, local_day_bounds_utc
from app.economics.breakeven import BreakevenRequest, uniform_breakeven
from app.economics.goals import GOAL_LABEL_VI, Goal, active_goal
from app.economics.parameters import load_param_set, parse_overrides
from app.simulator.locks import simulation_locks
from app.simulator.schemas import ActionSpec, Dist, SimOutput, SimRequest
from app.simulator.service import run_simulation


def _dist(d: Dist, keys: tuple[str, ...] = ("p10", "p50", "p90", "mean")) -> dict[str, float]:
    return {k: round(float(getattr(d, k)), 1) for k in keys}


def split_overrides(
    items: list[tuple[str, float]],
) -> tuple[dict[str, float], list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate overrides one by one: (accepted dict, applied list, rejected list + reason)."""
    ok: dict[str, float] = {}
    applied, rejected = [], []
    for key, value in items:
        try:
            parse_overrides({key: value})
        except AppError as e:
            rejected.append({"key": key, "value": value, "reason": e.message})
            continue
        ok[key] = float(value)
        applied.append({"key": key, "value": value})
    return ok, applied, rejected


def _default_deadline(ctx: ToolContext, campaign_id: str) -> datetime:
    g = active_goal(ctx.conn, campaign_id)
    if g is not None:
        return g.deadline
    as_of = now_data(ctx.conn)
    return local_day_bounds_utc(local_date(as_of - timedelta(microseconds=1)))[1]


def _hypothetical_goal(ctx: ToolContext, campaign_id: str, g: GoalOverrideArg) -> Goal:
    if g.kind == "sell_target" and not g.target_units:
        raise ValidationFailed("Mục tiêu bán đủ số lượng cần target_units (số sp).")
    deadline = parse_ts(g.deadline, _default_deadline(ctx, campaign_id))
    params: dict[str, Any] = {}
    if g.sell_target_prob is not None:
        params["sell_target_prob"] = g.sell_target_prob
    if g.max_loss_vnd is not None:
        params["max_loss_vnd"] = g.max_loss_vnd
    return Goal(
        goal_id=new_id("goal-whatif"),
        scope_id=campaign_id,
        kind=g.kind,
        deadline=deadline,
        target_units=g.target_units,
        params=params,
        confirmed_by=f"whatif:{ctx.user_id}",  # never stored: rolled back below
        created_at=now_data(ctx.conn),
        active=False,
    )


def _run_with_goal(ctx: ToolContext, req: SimRequest, goal: Goal) -> SimOutput:
    conn = ctx.conn
    conn.execute("BEGIN TRANSACTION")
    try:
        conn.execute(
            "INSERT INTO goals (goal_id, scope, scope_id, kind, deadline, target_units, params,"
            " confirmed_by, created_at, active, version, valid_from) VALUES"
            " (?, 'campaign', ?, ?, ?, ?, ?, ?, ?, FALSE, 1, ?)",
            [
                goal.goal_id,
                goal.scope_id,
                goal.kind,
                goal.deadline,
                goal.target_units,
                json.dumps(goal.params),
                goal.confirmed_by,
                goal.created_at,
                goal.created_at,
            ],
        )
        req = req.model_copy(update={"goal_id": goal.goal_id})
        return run_simulation(conn, req, requested_by="ai", persist=False, use_cache=False)
    finally:
        conn.execute("ROLLBACK")


def simulate(
    ctx: ToolContext,
    campaign_id: str,
    *,
    horizon_hours: int | None,
    actions: list[ActionSpec] | None,
    overrides: list[tuple[str, float]],
    goal_override: GoalOverrideArg | None,
) -> tuple[SimOutput, dict[str, Any]]:
    """Run the simulator; returns the output and the run metadata for the LLM."""
    require_campaign(ctx.conn, campaign_id)
    accepted, applied, rejected = split_overrides(overrides)
    hypothetical = goal_override is not None
    is_scenario = hypothetical or bool(accepted)
    base_goal = active_goal(ctx.conn, campaign_id)
    if base_goal is None and not hypothetical:
        raise DataQualityError(
            f"Chiến dịch {campaign_id} chưa có mục tiêu. Lead cần đặt mục tiêu trước;"
            " AI không tự tạo mục tiêu.",
            missing="goal",
        )
    req = SimRequest(
        campaign_id=campaign_id,
        goal_id=base_goal.goal_id if base_goal else "pending",
        horizon_hours=horizon_hours,
        actions=actions,
        param_overrides=dict(accepted),
        samples=ctx.settings.mc_samples,
        seed=ctx.settings.default_seed,
    )
    goal_info: dict[str, Any]
    # same locks and order as POST /scenarios/run and the orchestrator: a question asked
    # while a live tick recomputes waits instead of "Conflict on update" (M5 Q5)
    if goal_override is not None:
        goal = _hypothetical_goal(ctx, campaign_id, goal_override)
        with simulation_locks(ctx.live_lock):
            out = _run_with_goal(ctx, req, goal)
    else:
        assert base_goal is not None
        goal = base_goal
        with simulation_locks(ctx.live_lock):
            out = run_simulation(
                ctx.conn,
                req,
                requested_by="ai",
                persist=not is_scenario,
                use_cache=not is_scenario,
            )
    goal_info = {
        "kind": goal.kind,
        "label": GOAL_LABEL_VI.get(goal.kind, goal.kind),
        "target_units": goal.target_units,
        # the probability a sell_target must reach (goal param, else the parameter store):
        # a structured value, so an answer may say "chắc 80%" (only goal_note had it, as text)
        "sell_target_prob": (
            float(
                goal.params.get(
                    "sell_target_prob",
                    load_param_set(ctx.conn, campaign_id=campaign_id).num("sell_target_prob"),
                )
            )
            if goal.kind == "sell_target"
            else None
        ),
        "deadline": iso(goal.deadline),
        "hypothetical": hypothetical,
        "goal_id": None if hypothetical else goal.goal_id,
    }
    meta = {
        "goal": goal_info,
        "param_overrides_applied": applied,
        "param_overrides_rejected": rejected,
        "is_scenario": is_scenario,
        "persisted": not is_scenario,
    }
    return out, meta


def scenario_payload(out: SimOutput, meta: dict[str, Any]) -> dict[str, Any]:
    rec = out.recommendation
    if out.blocked:
        return {
            "scenario_id": out.scenario_id,
            "campaign_id": rec.campaign_id,
            "blocked": True,
            "reasons": rec.reasons,
            **meta,
        }
    results = []
    for r in out.results:
        results.append(
            {
                "action": r.action.model_dump(mode="json"),
                "key": r.action.key,
                "label": r.action.label_vi(),
                "feasible": r.feasible,
                "violations": r.violations,
                "goal_eligible": r.goal_eligible,
                "p_target": r2(r.p_target, 3),
                "goal_note": r.goal_note,
                "objective": _dist(r.objective),
                "units_sold": _dist(r.units_sold, ("p50", "mean")),
                "spend": _dist(r.spend, ("p50", "mean")),
                "leftover_units": _dist(r.leftover_units, ("p50", "mean")),
                "stockout_hour_p50": iso(r.stockout_hour_p50),
            }
        )
    zb = rec.zone_bar
    return {
        "scenario_id": out.scenario_id,
        "campaign_id": rec.campaign_id,
        "data_as_of": iso(rec.inputs_ref.data_as_of),
        "results": results,
        "recommendation": {
            "status": rec.status,
            "best": rec.best.model_dump(mode="json") if rec.best else None,
            "best_label": rec.best.label_vi() if rec.best else None,
            "runner_up": rec.runner_up.model_dump(mode="json") if rec.runner_up else None,
            "runner_up_label": rec.runner_up.label_vi() if rec.runner_up else None,
            "runner_up_meets_goal": rec.runner_up_meets_goal,
            "p_best_beats_runner_up": r2(rec.p_best_beats_runner_up, 3),
            "expected_gain_vnd": rec.expected_gain_vnd,
            "goal_cost_vnd": rec.goal_cost_vnd,
            "flip_conditions": [
                {"factor": f.factor, "flip_at": r2(f.flip_at, 3), "text": f.text}
                for f in rec.flip_conditions
            ],
            "reasons": rec.reasons,
            "confidence": rec.confidence,
            "zone_bar": (
                {
                    "threshold": r2(zb.threshold, 3),
                    "interval": [r2(x, 3) for x in zb.interval],
                    "verdict": zb.verdict,
                    "pause_feasible": zb.pause_feasible,
                    "verdict_note": zb.verdict_note,
                }
                if zb
                else None
            ),
        },
        **meta,
        "confidence": "scenario" if meta.get("is_scenario") else rec.confidence,
    }


def _sources(out: SimOutput, meta: dict[str, Any], samples: int) -> list[Source]:
    ref = out.recommendation.inputs_ref
    label = f"Mô phỏng {out.scenario_id} · {samples:,} mẫu · {label_until(ref.data_as_of)}"
    if meta.get("is_scenario"):
        label = "Kịch bản giả định · " + label
    srcs = [
        Source(
            id=out.scenario_id,
            kind="scenario",
            label=label.replace(",", "."),
            as_of=iso(ref.data_as_of),
            href=f"/api/v1/scenarios/{out.scenario_id}" if meta.get("persisted") else None,
        )
    ]
    if ref.estimate_id:
        srcs.append(Source(id=ref.estimate_id, kind="iroas", label="Hiệu quả thật của ads"))
    return srcs


def _actions(args_actions: list[Any] | None) -> list[ActionSpec] | None:
    if not args_actions:
        return None
    out = []
    for a in args_actions:
        windows = [tuple(w) for w in a.windows_local] if a.windows_local else None
        try:
            out.append(ActionSpec(kind=a.kind, pct=a.pct, windows_local=windows))
        except ValueError as e:
            raise ValidationFailed(f"Hành động không hợp lệ: {e}") from e
    return out


@tool(
    "run_scenario",
    "Chạy mô phỏng cho một chiến dịch với các hành động và giả định (tham số hoặc mục tiêu giả"
    " định). Không thay đổi dữ liệu thật.",
    RunScenarioArgs,
    kind="compute",
)
def run_scenario(args: RunScenarioArgs, ctx: ToolContext) -> ToolData:
    out, meta = simulate(
        ctx,
        args.campaign_id,
        horizon_hours=args.horizon_hours,
        actions=_actions(args.actions),
        overrides=[(o.key, o.value) for o in args.param_overrides],
        goal_override=args.goal_override,
    )
    payload = scenario_payload(out, meta)
    return ToolData(
        data=payload,
        sources=_sources(out, meta, ctx.settings.mc_samples),
        confidence=payload.get("confidence"),
    )


@tool(
    "compare_actions",
    "So sánh đủ các hành động hợp lệ của chiến dịch với mục tiêu đang áp dụng, kèm 3 lý do"
    " ảnh hưởng nhất.",
    CompareActionsArgs,
    kind="compute",
)
def compare_actions(args: CompareActionsArgs, ctx: ToolContext) -> ToolData:
    out, meta = simulate(
        ctx,
        args.campaign_id,
        horizon_hours=args.horizon_hours,
        actions=None,
        overrides=[],
        goal_override=None,
    )
    payload = scenario_payload(out, meta)
    payload["why"] = out.recommendation.reasons[:3]
    return ToolData(
        data=payload,
        sources=_sources(out, meta, ctx.settings.mc_samples),
        confidence=payload.get("confidence"),
    )


@tool(
    "compute_breakeven",
    "Tính ngưỡng lượng bán mỗi giờ khi pause để pause ngang với tiếp tục, và giá trị hàng dư"
    " làm đổi kết luận.",
    BreakevenArgs,
    kind="compute",
)
def compute_breakeven(args: BreakevenArgs, ctx: ToolContext) -> ToolData:
    require_sku(ctx.conn, args.sku)
    cid = campaign_of(ctx.conn, args.sku)
    if cid is None:
        raise ValidationFailed(f"{args.sku} không thuộc chiến dịch nào.")
    out, meta = simulate(
        ctx, cid, horizon_hours=args.horizon_hours, actions=None, overrides=[], goal_override=None
    )
    rec = out.recommendation
    bar = rec.zone_bar
    data: dict[str, Any] = {"sku": args.sku, "campaign_id": cid, "scenario_id": out.scenario_id}
    if bar is None:
        data.update(
            {
                "zone": None,
                "available": False,
                "message": "Không có thanh vùng cho chiến dịch này (ví dụ giữ hàng lợi hơn bán,"
                " hoặc pause vẫn chi tiền).",
            }
        )
    else:
        data.update(
            {
                "available": True,
                "threshold_units_per_hour": r2(bar.threshold, 3),
                "forecast_pause_per_hour": dict(
                    zip(("p10", "p50", "p90"), (r2(x, 3) for x in bar.interval), strict=True)
                ),
                "zone": bar.verdict,
                "pause_feasible": bar.pause_feasible,
                "verdict_note": bar.verdict_note,
            }
        )
        if bar.inputs is not None:
            req: BreakevenRequest = bar.inputs
            if args.leftover_value_per_unit is not None:
                req = req.model_copy(
                    update={"leftover_value_per_unit_vnd": args.leftover_value_per_unit}
                )
            res = uniform_breakeven(req.case())
            data["flip_leftover_value"] = (
                round(res.flip_leftover_value_vnd)
                if res.flip_leftover_value_vnd is not None
                else None
            )
            if res.zone_bar is not None and args.leftover_value_per_unit is not None:
                data["threshold_units_per_hour"] = r2(res.zone_bar.threshold, 3)
                data["zone"] = res.zone_bar.verdict
                data["leftover_value_per_unit"] = args.leftover_value_per_unit
    return ToolData(
        data=data, sources=_sources(out, meta, ctx.settings.mc_samples), confidence="scenario"
    )


def to_sim_request(ctx: ToolContext, args: RunScenarioArgs) -> SimRequest:
    """``RunScenarioArgs`` (what-if structured output) → ``SimRequest`` (05 §1). The goal is
    the active one, or a placeholder id when ``goal_override`` holds a hypothetical goal."""
    accepted, _, _ = split_overrides([(o.key, o.value) for o in args.param_overrides])
    g = active_goal(ctx.conn, args.campaign_id)
    return SimRequest(
        campaign_id=args.campaign_id,
        goal_id=(g.goal_id if g and args.goal_override is None else "whatif-goal"),
        horizon_hours=args.horizon_hours,
        actions=_actions(args.actions),
        param_overrides=dict(accepted),
        start=None,
        samples=ctx.settings.mc_samples,
        seed=ctx.settings.default_seed,
    )
