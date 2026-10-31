"""Campaign HQ: reuse confirmed goals, freeze each run, never fabricate live data."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

import duckdb
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.api.campaigns import get_campaign
from app.api.common import data_as_of, source_as_of
from app.api.deps import Conn, Operator, Viewer, audit
from app.api.scenarios import live_write_lock, load_run
from app.core.errors import DataQualityError, StateConflict, ValidationFailed
from app.core.timeutil import to_local
from app.economics.contribution import load_contributions
from app.economics.goals import Goal, active_goal
from app.economics.parameters import load_param_set
from app.simulator.inputs import default_start
from app.simulator.locks import RUN_LOCK
from app.simulator.schemas import SimRequest
from app.simulator.service import run_simulation
from app.warehouse.repo import fetch_all

router = APIRouter(tags=["campaign-analysis"])


def planned_start(goal: Goal) -> datetime:
    try:
        value = datetime.fromisoformat(str(goal.params.get("planner_start", "")))
    except ValueError as exc:
        raise ValidationFailed("Xác nhận mốc bắt đầu trong phần mục tiêu trước khi tính.") from exc
    if value.tzinfo is None or value.minute or value.second or value.microsecond:
        raise ValidationFailed("Mốc bắt đầu mục tiêu phải là giờ tròn có múi giờ.")
    return value


def revision(conn: duckdb.DuckDBPyConnection, campaign_id: str) -> str:
    # Conservative invalidation: corrections at the same data clock also invalidate a run.
    goal = active_goal(conn, campaign_id)
    payload = [
        fetch_all(conn, "SELECT * FROM ingest_batches ORDER BY batch_id"),
        fetch_all(conn, "SELECT * FROM parameters ORDER BY key, scope, scope_id, version"),
        load_param_set(conn, campaign_id=campaign_id).versions,
        goal.model_dump(mode="json") if goal else None,
    ]
    return hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()


@router.get("/campaign-analysis/{campaign_id}")
def context(campaign_id: str, conn: Conn, user: Viewer, request: Request) -> dict[str, Any]:
    with live_write_lock(request):
        campaign = get_campaign(campaign_id, conn, user).data
        clock = data_as_of(conn)
        goal = active_goal(conn, campaign_id)
        return {
            "campaign": campaign.model_dump(mode="json"),
            "goal": goal.model_dump(mode="json") if goal else None,
            "revision": revision(conn, campaign_id),
            "dataAsOf": to_local(clock).isoformat() if clock else None,
            "sources": {
                k: to_local(v[0]).isoformat() if v[0] else None
                for k, v in source_as_of(conn).items()
            },
            "canRun": user.at_least("operator"),
            "history": fetch_all(
                conn,
                "SELECT scenario_id, created_at FROM scenarios "
                "WHERE campaign_id = ? "
                "AND json_extract_string(request, '$.session_id') = 'campaign-hq' "
                "ORDER BY created_at DESC LIMIT 5",
                [campaign_id],
            ),
        }


def remaining_goal(conn: duckdb.DuckDBPyConnection, goal: Goal, start: datetime) -> Goal:
    """Retain confirmed target; credit observed sales/cash only in goal selection.

    Never reset a whole-period spending cap on refresh. Incomplete ads cannot prove
    remaining budget/loss capacity, so financial constraints wait for those observations.
    """
    since = planned_start(goal)
    if start <= since:
        return goal
    ids = [
        str(r[0])
        for r in conn.execute(
            "SELECT sku_id FROM campaign_skus WHERE campaign_id = ?", [goal.scope_id]
        ).fetchall()
    ]
    rows = fetch_all(
        conn,
        "SELECT l.sku_id, o.ordered_at, l.qty, l.line_total FROM orders o "
        "JOIN order_lines l USING(order_id) WHERE list_contains(?, l.sku_id) "
        "AND o.ordered_at >= ? AND o.ordered_at < ? "
        "AND o.status NOT IN ('cancelled', 'returned')",
        [ids, since, start],
    )
    focus = goal.params.get("focus_sku")
    sold = sum(int(r["qty"]) for r in rows if not focus or r["sku_id"] == focus)
    ads = conn.execute(
        "SELECT count(DISTINCT CASE WHEN spend IS NOT NULL THEN hour_utc END), "
        "coalesce(sum(spend), 0) FROM ad_hourly "
        "WHERE campaign_id = ? AND hour_utc >= ? AND hour_utc < ?",
        [goal.scope_id, since, start],
    ).fetchone()
    hours = int((start - since).total_seconds() / 3600)
    complete = ads is not None and ads[0] >= hours
    needs_cash = "max_loss_vnd" in goal.params or goal.kind == "clear_stock"
    if not complete and ("max_spend_vnd" in goal.params or needs_cash):
        raise DataQualityError(
            "Báo cáo ads chưa đủ cho phần thời gian đã qua. Chưa thể xác nhận ngân sách/mức lỗ "
            "còn lại; hãy chờ ads hoặc xem lại mục tiêu. Không coi giờ thiếu ads là chi 0đ."
        )
    spent = float(ads[1]) if ads else 0.0
    cash = 0.0
    if needs_cash and rows:
        params = load_param_set(conn, campaign_id=goal.scope_id)
        econ = load_contributions(
            conn,
            ids,
            {to_local(r["ordered_at"]).date() for r in rows},
            start,
            fee_rate=params.num("platform_fee_rate"),
        )
        for row in rows:
            e = econ.by_key.get((str(row["sku_id"]), to_local(row["ordered_at"]).date()))
            if e is None:
                raise DataQualityError("Thiếu giá vốn/phí để đối chiếu mức lỗ của kỳ đã qua.")
            cash += float(row["line_total"]) * (1 - e.fee_rate) - int(row["qty"]) * (
                e.unit_cost + e.packaging_cost
            )
    return goal.model_copy(
        update={
            "params": {
                **goal.params,
                "hq_progress": {
                    "since": since.isoformat(),
                    "until": start.isoformat(),
                    "units": sold,
                    "spend": spent,
                    "cash": cash - spent if needs_cash else 0,
                    "ads_complete": complete,
                    "note": "Đơn chưa hủy/trả có thể còn tạm tính; "
                    "sẽ đối chiếu lại khi dữ liệu thay đổi.",
                },
            }
        }
    )


class Refresh(BaseModel):
    revision: str
    goal_id: str


@router.post("/campaign-analysis/{campaign_id}/refresh")
def refresh(
    campaign_id: str, body: Refresh, conn: Conn, user: Operator, request: Request
) -> dict[str, Any]:
    with live_write_lock(request), RUN_LOCK:
        goal = active_goal(conn, campaign_id)
        if not goal or goal.goal_id != body.goal_id or revision(conn, campaign_id) != body.revision:
            raise StateConflict("Dữ liệu hoặc mục tiêu đã đổi. Tải lại trước khi tính.")
        clock = data_as_of(conn)
        if clock is None:
            raise DataQualityError(
                "Chưa có dữ liệu đã ingest. Gửi nguồn vào backend trước khi tính."
            )
        start = max(default_start(conn), planned_start(goal))
        hours = (goal.deadline - start).total_seconds() / 3600
        if not hours.is_integer() or not 1 <= hours <= 72:
            raise ValidationFailed(
                "Mục tiêu đã hết hạn hoặc thời gian còn lại ngoài 1–72 giờ tròn."
            )
        effective = remaining_goal(conn, goal, start)
        effective = effective.model_copy(
            update={
                "params": {
                    **effective.params,
                    "hq_revision": body.revision,
                    "hq_goal_version": goal.version,
                    "hq_deadline": goal.deadline.isoformat(),
                }
            }
        )
        out = run_simulation(
            conn,
            SimRequest(
                campaign_id=campaign_id,
                goal_id=goal.goal_id,
                start=start,
                horizon_hours=int(hours),
                session_id="campaign-hq",
            ),
            requested_by=user.id,
            goal_override=effective,
        )
        if out.blocked:
            raise DataQualityError("Chưa đủ dữ liệu để tính.", reasons=out.recommendation.reasons)
        run = load_run(conn, out.scenario_id)
        audit(
            conn,
            request,
            user,
            "campaign.analysis",
            "campaign",
            campaign_id,
            after={"scenario_id": out.scenario_id, "revision": body.revision},
        )
        return {
            "revision": body.revision,
            "scenario": run.model_dump(mode="json"),
            "goal": goal.model_dump(mode="json"),
            "progress": effective.params.get("hq_progress"),
            "end": to_local(start + timedelta(hours=int(hours))).isoformat(),
        }
