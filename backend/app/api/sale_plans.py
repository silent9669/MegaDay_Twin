"""Sale planning: isolated previews, then explicit optimistic-concurrency activation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

import duckdb
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.common import data_as_of, parse_json
from app.api.deps import Conn, Lead, Operator, Viewer, audit, publish
from app.api.economics import GoalBody, GoalOut, goal_out
from app.api.scenarios import ScenarioRun, live_write_lock, load_run
from app.api.schemas import Envelope, Meta
from app.core.errors import (
    DataQualityError,
    NotFound,
    StaleDataError,
    StateConflict,
    ValidationFailed,
)
from app.core.events import GoalChanged
from app.core.ids import new_id
from app.core.timeutil import to_utc, utcnow
from app.economics.goals import (
    Goal,
    active_goal,
    create_goal,
    get_goal,
    validate_focus_sku,
    validate_goal_params,
)
from app.economics.parameters import load_param_set
from app.simulator.locks import RUN_LOCK
from app.simulator.schemas import SimRequest
from app.simulator.service import run_simulation
from app.warehouse.repo import fetch_one

router = APIRouter(tags=["sale-plans"])


class GoalToken(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_id: str = Field(min_length=1, max_length=150)
    version: int = Field(ge=1, strict=True)


class PlanContext(BaseModel):
    campaign_id: str
    active_goal: GoalOut | None
    base_goal: GoalToken | None


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign_id: str = Field(min_length=1, max_length=150)
    goal: GoalBody
    start: datetime
    horizon_hours: int = Field(ge=1, le=72, strict=True)
    samples: int = Field(default=1000, ge=50, le=5000, strict=True)
    seed: int = Field(default=20261111, strict=True)
    expected_goal: GoalToken | None
    focus_sku: str | None = Field(default=None, min_length=1, max_length=150)


class PlanChange(BaseModel):
    field: str
    before: Any
    after: Any


class PlanPreview(BaseModel):
    preview_id: str
    base_goal: GoalOut | None
    effective_goal: GoalOut
    scenario: ScenarioRun
    changes: list[PlanChange]
    notices: list[str]
    can_apply: bool
    applied_goal_id: str | None = None


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]
    expected_goal: GoalToken | None

    @field_validator("confirmed", mode="before")
    @classmethod
    def require_confirmation(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("Cần xác nhận rõ trước khi áp dụng.")
        return value


class ApplyResult(BaseModel):
    preview_id: str
    goal: GoalOut
    already_applied: bool = False


def token(goal: Goal | GoalOut | None) -> GoalToken | None:
    return GoalToken(goal_id=goal.goal_id, version=goal.version) if goal else None


def check_expected(current: Goal | None, expected: GoalToken | None) -> None:
    if token(current) != expected:
        raise StateConflict(
            "Mục tiêu chiến dịch đã thay đổi. "
            "Hãy tải mục tiêu mới và mô phỏng lại trước khi áp dụng.",
            current_goal=goal_out(current).model_dump(mode="json") if current else None,
        )


def check_campaign(conn: duckdb.DuckDBPyConnection, campaign_id: str) -> None:
    if (
        conn.execute("SELECT 1 FROM campaigns WHERE campaign_id = ?", [campaign_id]).fetchone()
        is None
    ):
        raise NotFound(f"Không có chiến dịch {campaign_id}.")


@router.get("/sale-plans/context", response_model=Envelope[PlanContext])
def plan_context(
    campaign_id: str, conn: Conn, user: Viewer, request: Request
) -> Envelope[PlanContext]:
    with live_write_lock(request), RUN_LOCK:
        check_campaign(conn, campaign_id)
        goal = active_goal(conn, campaign_id)
        result = PlanContext(
            campaign_id=campaign_id,
            active_goal=goal_out(goal) if goal else None,
            base_goal=token(goal),
        )
    return Envelope(data=result, meta=Meta(computed_at=utcnow()))


def effective_params(
    body: PreviewRequest, base: Goal | None, conn: duckdb.DuckDBPyConnection
) -> tuple[dict[str, Any], list[str], bool]:
    if body.goal.constraints or body.goal.reason:
        raise ValidationFailed(
            "Bản nháp dùng params cho ràng buộc; không nhận trường constraints/reason thay thế."
        )
    params = dict(body.goal.params)
    if "focus_sku" in params and params["focus_sku"] != body.focus_sku:
        raise ValidationFailed("focus_sku phải khớp SKU của bản nháp.")
    if body.focus_sku:
        params["focus_sku"] = body.focus_sku
    elif "focus_sku" in params:
        raise ValidationFailed("Chọn SKU hoặc bỏ focus_sku khỏi bản nháp chiến dịch.")
    # The endpoint owns parent linkage and preview provenance; clients cannot fabricate them.
    for key in list(params):
        if key.startswith("planner_parent_") or key == "planner_preview_id":
            del params[key]
    validate_goal_params(params)
    notices = ["Đây là mô phỏng thử. Mục tiêu và đề xuất đang áp dụng chưa thay đổi."]
    compatible = True
    if body.focus_sku and base:
        for key in ("max_spend_vnd", "max_loss_vnd"):
            inherited = base.params.get(
                key, 0 if key == "max_loss_vnd" and base.kind == "clear_stock" else None
            )
            if inherited is not None:
                wanted = params.get(key)
                params[key] = min(inherited, wanted) if wanted is not None else inherited
                if wanted != params[key]:
                    label = (
                        "tổng ngân sách quảng cáo" if key == "max_spend_vnd" else "mức lỗ kỳ vọng"
                    )
                    notices.append(f"Giữ giới hạn {label} của kế hoạch chung: {params[key]:,.0f}đ.")
        parent_sku = base.params.get("focus_sku")
        compatible = (
            base.kind == "profit"
            and body.goal.kind in ("profit", "sell_target")
            and parent_sku in (None, body.focus_sku)
        ) or (base.kind == body.goal.kind and parent_sku == body.focus_sku)
        if base.kind == "sell_target" and compatible:
            if body.goal.target_units is None or body.goal.target_units < (base.target_units or 0):
                compatible = False
            default_probability = load_param_set(conn, campaign_id=body.campaign_id).num(
                "sell_target_prob"
            )
            inherited_probability = base.params.get("sell_target_prob", default_probability)
            params["sell_target_prob"] = max(
                params.get("sell_target_prob", default_probability), inherited_probability
            )
        if base.kind == "efficiency" and compatible:
            default_return = load_param_set(conn, campaign_id=body.campaign_id).num(
                "min_marginal_return"
            )
            params["min_marginal_return"] = max(
                params.get("min_marginal_return", default_return),
                base.params.get("min_marginal_return", default_return),
            )
        if body.goal.deadline > base.deadline:
            compatible = False
            notices.append(
                "Thời hạn của SKU vượt kế hoạch đang áp dụng; cần chỉnh kế hoạch chung trước."
            )
        parent_start = base.params.get("planner_start")
        if isinstance(parent_start, str):
            try:
                if body.start < datetime.fromisoformat(parent_start):
                    compatible = False
                    notices.append("Khoảng mô phỏng bắt đầu trước kế hoạch chung.")
            except (ValueError, TypeError):
                compatible = False
                notices.append("Chưa xác minh được thời điểm bắt đầu của kế hoạch chung.")
        if not compatible:
            notices.append(
                "Mục tiêu SKU này thay đổi ưu tiên hoặc phạm vi của kế hoạch chung. "
                "Chỉ xem mô phỏng để đối chiếu; hãy chỉnh mục tiêu chiến dịch rồi mô phỏng lại "
                "nếu muốn thay ưu tiên."
            )
        else:
            notices.append(
                "Mục tiêu SKU nằm trong giới hạn kế hoạch chung. "
                "Chỉ số J và ngân sách vẫn thuộc toàn chiến dịch; "
                "chưa tối ưu đồng thời nhiều SKU."
            )
        params["planner_parent_goal_id"] = base.goal_id
        params["planner_parent_goal_version"] = base.version
        params["planner_parent_goal_kind"] = base.kind
    params["planner_start"] = body.start.isoformat()
    return params, notices, compatible


def goal_changes(base: Goal | None, goal: Goal) -> list[PlanChange]:
    before = base.model_dump(mode="json") if base else {}
    after = goal.model_dump(mode="json")
    changes = []
    for key in ("kind", "target_units", "deadline"):
        if key == "deadline" and base and base.deadline == goal.deadline:
            continue
        if before.get(key) != after[key]:
            changes.append(PlanChange(field=key, before=before.get(key), after=after[key]))
    previous = base.params if base else {}
    for key in (
        "focus_sku",
        "max_spend_vnd",
        "max_loss_vnd",
        "sell_target_prob",
        "min_marginal_return",
        "planner_start",
    ):
        if previous.get(key) != goal.params.get(key):
            changes.append(
                PlanChange(field=key, before=previous.get(key), after=goal.params.get(key))
            )
    return changes


def read_preview(
    conn: duckdb.DuckDBPyConnection, preview_id: str
) -> tuple[dict[str, Any], PlanPreview]:
    row = fetch_one(conn, "SELECT * FROM sale_plan_previews WHERE preview_id = ?", [preview_id])
    if row is None:
        raise NotFound("Không tìm thấy bản mô phỏng thử.", preview_id=preview_id)
    snapshot = parse_json(row["snapshot"])
    preview = PlanPreview.model_validate(
        {
            **snapshot,
            "scenario": load_run(conn, row["scenario_id"]),
            "applied_goal_id": row["applied_goal_id"],
        }
    )
    return row, preview


def input_fingerprint(conn: duckdb.DuckDBPyConnection) -> str:
    """Conservative warehouse revision, including same-timestamp imports and direct cost edits.

    SQL aggregation keeps raw business rows out of the preview document. Goals and
    simulation outputs are excluded: those have their own token / immutable snapshot.
    An unrelated input change may also require a fresh preview; never silently accept it.
    """
    tables = (
        "ingest_batches",
        "skus",
        "sku_aliases",
        "sku_demand_labels",
        "bundle_components",
        "orders",
        "order_lines",
        "inventory_snapshots",
        "restock_plans",
        "restock_receipts",
        "campaigns",
        "campaign_skus",
        "ad_hourly",
        "cost_items",
        "price_events",
        "sale_commitments",
        "hourly_panel",
        "parameters",
        "model_registry",
        "model_scores",
        "iroas_estimates",
    )
    values = [
        (table, conn.execute(f"SELECT count(*), bit_xor(hash(t)) FROM {table} t").fetchone())
        for table in tables
    ]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()


@router.get("/sale-plans/{preview_id}", response_model=Envelope[PlanPreview])
def get_preview(preview_id: str, conn: Conn, user: Viewer) -> Envelope[PlanPreview]:
    _, preview = read_preview(conn, preview_id)
    return Envelope(data=preview, meta=Meta(computed_at=utcnow()))


@router.post("/sale-plans/preview", response_model=Envelope[PlanPreview])
def preview_plan(
    body: PreviewRequest, conn: Conn, user: Operator, request: Request
) -> Envelope[PlanPreview]:
    if body.start.tzinfo is None or body.goal.deadline.tzinfo is None:
        raise ValidationFailed("Giờ bắt đầu và hạn kết thúc cần múi giờ.")
    if (
        body.start.timestamp() % 3600
        or body.goal.deadline.timestamp() % 3600
        or (body.goal.deadline - body.start).total_seconds() != body.horizon_hours * 3600
    ):
        raise ValidationFailed("Khoảng mô phỏng phải khớp thời hạn, từ 1–72 giờ và ở giờ tròn.")
    if body.goal.kind != "sell_target" and body.goal.target_units is not None:
        raise ValidationFailed("Chỉ mục tiêu bán đủ số lượng nhận target_units.")
    for key, kind in (("sell_target_prob", "sell_target"), ("min_marginal_return", "efficiency")):
        if key in body.goal.params and body.goal.kind != kind:
            raise ValidationFailed(f"Ràng buộc {key} không được áp dụng cho loại mục tiêu này.")
    with live_write_lock(request), RUN_LOCK:
        check_campaign(conn, body.campaign_id)
        base = active_goal(conn, body.campaign_id)
        check_expected(base, body.expected_goal)
        params, notices, compatible = effective_params(body, base, conn)
        preview_id = new_id("plan")
        params["planner_preview_id"] = preview_id
        goal = create_goal(
            conn,
            scope_id=body.campaign_id,
            kind=body.goal.kind,
            deadline=body.goal.deadline,
            confirmed_by=user.id,
            target_units=body.goal.target_units,
            params=params,
            active=False,
        )
        req = SimRequest(
            campaign_id=body.campaign_id,
            goal_id=goal.goal_id,
            start=body.start,
            horizon_hours=body.horizon_hours,
            samples=body.samples,
            seed=body.seed,
            session_id=preview_id,
        )
        out = run_simulation(conn, req, requested_by=user.id, use_cache=False, with_flips=False)
        if out.blocked:
            raise DataQualityError(
                "Dữ liệu chưa đủ để mô phỏng thử.", reasons=out.recommendation.reasons
            )
        run = load_run(conn, out.scenario_id)
        if not run.calculation_trace or not run.recommendation or not run.recommendation.what_if:
            raise StateConflict(
                "Mô phỏng chưa có bằng chứng hoặc chưa được cách ly khỏi đề xuất đang áp dụng."
            )
        eligible = any(result.feasible and result.goal_eligible for result in run.results)
        if not eligible:
            notices.append(
                "Chưa có phương án đáp ứng mục tiêu và giới hạn đã chọn; "
                "hãy chỉnh bản nháp trước khi áp dụng."
            )
        preview = PlanPreview(
            preview_id=preview_id,
            base_goal=goal_out(base) if base else None,
            effective_goal=goal_out(goal),
            scenario=run,
            changes=goal_changes(base, goal),
            notices=notices,
            can_apply=compatible and eligible,
        )
        snapshot = preview.model_dump(mode="json", exclude={"scenario"})
        snapshot["input_fingerprint"] = input_fingerprint(conn)
        conn.execute(
            "INSERT INTO sale_plan_previews "
            "(preview_id,campaign_id,goal_id,scenario_id,snapshot,created_at) VALUES (?,?,?,?,?,?)",
            [
                preview_id,
                body.campaign_id,
                goal.goal_id,
                run.scenario_id,
                json.dumps(snapshot),
                utcnow(),
            ],
        )
        audit(
            conn,
            request,
            user,
            "sale_plan.preview",
            "campaign",
            body.campaign_id,
            after={"preview_id": preview_id, "scenario_id": run.scenario_id},
        )
    return Envelope(data=preview, meta=Meta(computed_at=utcnow()))


@router.post("/sale-plans/{preview_id}/apply", response_model=Envelope[ApplyResult])
def apply_plan(
    preview_id: str, body: ApplyRequest, conn: Conn, user: Lead, request: Request
) -> Envelope[ApplyResult]:
    event = None
    with live_write_lock(request), RUN_LOCK:
        row, preview = read_preview(conn, preview_id)
        if body.expected_goal != token(preview.base_goal):
            raise StateConflict("Bản xác nhận không khớp mục tiêu gốc của mô phỏng.")
        current = active_goal(conn, row["campaign_id"])
        if row["applied_goal_id"]:
            if not current or current.goal_id != row["applied_goal_id"]:
                raise StateConflict(
                    "Kế hoạch này từng được áp dụng nhưng đã được thay thế. Không tự áp dụng lại."
                )
            return Envelope(
                data=ApplyResult(
                    preview_id=preview_id, goal=goal_out(current), already_applied=True
                ),
                meta=Meta(computed_at=utcnow()),
            )
        check_expected(current, body.expected_goal)
        if not preview.can_apply:
            raise StateConflict(
                "Mô phỏng này chưa đáp ứng mục tiêu chung hoặc chưa có phương án phù hợp. "
                "Hãy chỉnh và mô phỏng lại."
            )
        goal = get_goal(conn, row["goal_id"])
        if goal.active or goal_out(goal) != preview.effective_goal:
            raise StateConflict("Bản nháp đã thay đổi sau khi mô phỏng.")
        validate_focus_sku(conn, "campaign", row["campaign_id"], goal.params)
        if parse_json(row["snapshot"]).get("input_fingerprint") != input_fingerprint(conn):
            raise StaleDataError(
                "Dữ liệu hoặc mô hình đã thay đổi sau mô phỏng. Hãy tính lại trước khi áp dụng."
            )
        trace = preview.scenario.calculation_trace or {}
        now_as_of = data_as_of(conn)
        if (
            trace.get("data_as_of")
            and now_as_of
            and to_utc(datetime.fromisoformat(trace["data_as_of"])) != to_utc(now_as_of)
        ):
            raise StaleDataError(
                "Dữ liệu đã cập nhật sau mô phỏng. Hãy tính lại trước khi áp dụng."
            )
        current_params = load_param_set(conn, campaign_id=row["campaign_id"])
        for previous in trace.get("sources", {}).get("parameters", []):
            latest = current_params.resolve(
                previous["key"], previous["scope_id"] if previous["scope"] == "sku" else None
            )
            if (
                latest.version != previous["version"]
                or latest.value != previous["value"]
                or latest.scope != previous["scope"]
                or latest.scope_id != previous["scope_id"]
            ):
                raise StaleDataError(
                    "Tham số mô hình đã thay đổi. Hãy mô phỏng lại trước khi áp dụng."
                )
        version_row = conn.execute(
            "SELECT max(version) FROM goals WHERE scope='campaign' AND scope_id=?",
            [row["campaign_id"]],
        ).fetchone()
        version = int(version_row[0] or 0) + 1 if version_row else 1
        now = utcnow()
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "UPDATE goals SET active=FALSE,valid_to=? "
                "WHERE scope='campaign' AND scope_id=? AND active",
                [now, row["campaign_id"]],
            )
            conn.execute(
                "UPDATE goals SET active=TRUE,version=?,confirmed_by=?,valid_from=?,valid_to=NULL "
                "WHERE goal_id=?",
                [version, user.id, now, goal.goal_id],
            )
            conn.execute(
                "UPDATE sale_plan_previews SET applied_goal_id=?,applied_at=? WHERE preview_id=?",
                [goal.goal_id, now, preview_id],
            )
            goal = get_goal(conn, goal.goal_id)
            audit(
                conn,
                request,
                user,
                "sale_plan.apply",
                "campaign",
                row["campaign_id"],
                before={
                    "goal": preview.base_goal.model_dump(mode="json") if preview.base_goal else None
                },
                after={"preview_id": preview_id, "goal": goal.model_dump(mode="json")},
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        event = GoalChanged(scope="campaign", ids=[row["campaign_id"]], version=version)
    publish(request, event)
    return Envelope(
        data=ApplyResult(preview_id=preview_id, goal=goal_out(goal)),
        meta=Meta(computed_at=utcnow()),
    )
