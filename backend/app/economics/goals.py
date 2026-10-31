"""Goals → objective + constraints (ECO-03, plan 05 §4).

A goal is confirmed by a person (``confirmed_by``); the AI never creates one. For every
Monte Carlo sample ``s`` and action ``a`` the simulator gives ``J_s(a)`` (VND), units sold
and spend. :func:`select_actions` turns those into a ranking per goal kind:

| kind          | ranking metric            | goal constraint                                   |
|---------------|---------------------------|---------------------------------------------------|
| ``profit`` | ``E[J]`` | — |
| ``sell_target`` | ``E[J]`` | ``P(Σ sold ≥ N) ≥ sell_target_prob`` |
| ``clear_stock`` | ``E[Σ sold]`` | ``E[J] ≥ −max_loss_vnd`` |
| ``efficiency`` | ``E[J]`` | every extra đồng earns ``≥ min_marginal_return`` |

No eligible action → ``none_meets_goal`` (except ``efficiency``, which falls back to the
cheapest feasible action, normally ``pause``).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import duckdb
import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field

from app.core.errors import NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.timeutil import utcnow

FloatArr = npt.NDArray[np.float64]
GoalKind = Literal["profit", "sell_target", "clear_stock", "efficiency"]
GOAL_KINDS: tuple[GoalKind, ...] = ("profit", "sell_target", "clear_stock", "efficiency")
GOAL_LABEL_VI: dict[str, str] = {
    "profit": "lợi nhuận",
    "sell_target": "bán đủ số lượng",
    "clear_stock": "xả hàng",
    "efficiency": "hiệu quả chi",
}


class Goal(BaseModel):
    goal_id: str
    scope: Literal["campaign", "sku"] = "campaign"
    scope_id: str
    kind: GoalKind
    deadline: datetime
    target_units: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    confirmed_by: str
    created_at: datetime
    active: bool = True
    version: int = 1


def validate_goal_params(params: Mapping[str, Any]) -> None:
    """Validate the numeric constraints consumed by the simulator, without coercion."""
    if "focus_sku" in params and (
        not isinstance(params["focus_sku"], str)
        or not params["focus_sku"].strip()
        or len(params["focus_sku"]) > 150
    ):
        raise ValidationFailed("focus_sku phải là mã SKU hợp lệ.", key="focus_sku")
    for key in ("max_spend_vnd", "max_loss_vnd", "sell_target_prob", "min_marginal_return"):
        if key not in params:
            continue
        value = params[key]
        try:
            finite = isinstance(value, (int, float)) and math.isfinite(value)
        except OverflowError:
            finite = False
        if (
            isinstance(value, bool)
            or not finite
            or value < 0
            or (key == "sell_target_prob" and not 0 < value <= 1)
        ):
            raise ValidationFailed(f"Ràng buộc {key} không hợp lệ.", key=key)


def validate_target_units(value: Any, *, required: bool) -> None:
    if (required and value is None) or (
        value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0)
    ):
        raise ValidationFailed("Mục tiêu bán đủ số lượng cần số sp mục tiêu (> 0).")


def _validate(goal: Goal) -> None:
    validate_target_units(goal.target_units, required=goal.kind == "sell_target")
    validate_goal_params(goal.params)
    who = goal.confirmed_by.strip().lower()
    if not who or who.startswith("ai"):
        raise ValidationFailed("Mục tiêu phải do người dùng xác nhận; AI không tự tạo mục tiêu.")


def create_goal(
    conn: duckdb.DuckDBPyConnection,
    *,
    scope_id: str,
    kind: GoalKind,
    deadline: datetime,
    confirmed_by: str,
    scope: Literal["campaign", "sku"] = "campaign",
    target_units: int | None = None,
    params: dict[str, Any] | None = None,
    goal_id: str | None = None,
) -> Goal:
    validate_target_units(target_units, required=kind == "sell_target")
    now = utcnow()
    goal = Goal(
        goal_id=goal_id or new_id("goal"),
        scope=scope,
        scope_id=scope_id,
        kind=kind,
        deadline=deadline,
        target_units=target_units,
        params=params or {},
        confirmed_by=confirmed_by,
        created_at=now,
    )
    _validate(goal)
    validate_focus_sku(conn, goal.scope, goal.scope_id, goal.params)
    conn.execute(
        "INSERT INTO goals (goal_id, scope, scope_id, kind, deadline, target_units, params,"
        " confirmed_by, created_at, active, version, valid_from) VALUES"
        " (?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, 1, ?)",
        [
            goal.goal_id,
            goal.scope,
            goal.scope_id,
            goal.kind,
            goal.deadline,
            goal.target_units,
            json.dumps(goal.params),
            goal.confirmed_by,
            now,
            now,
        ],
    )
    return goal


def validate_focus_sku(
    conn: duckdb.DuckDBPyConnection, scope: str, scope_id: str, params: Mapping[str, Any]
) -> None:
    focus = params.get("focus_sku")
    if focus is None:
        return
    validate_goal_params(params)
    if (
        scope != "campaign"
        or conn.execute(
            "SELECT 1 FROM campaign_skus WHERE campaign_id = ? AND sku_id = ?",
            [scope_id, focus],
        ).fetchone()
        is None
    ):
        raise ValidationFailed("SKU mục tiêu không thuộc chiến dịch đã chọn.", focus_sku=focus)


_COLS = (
    "goal_id, scope, scope_id, kind, deadline, target_units, params, confirmed_by,"
    " created_at, active, version"
)


def _goal(r: Sequence[Any]) -> Goal:
    params = json.loads(r[6]) if isinstance(r[6], str) else (r[6] or {})
    return Goal(
        goal_id=r[0],
        scope=r[1],
        scope_id=r[2],
        kind=r[3],
        deadline=r[4],
        target_units=r[5],
        params=params,
        confirmed_by=r[7],
        created_at=r[8],
        active=bool(r[9]),
        version=int(r[10] or 1),
    )


def get_goal(conn: duckdb.DuckDBPyConnection, goal_id: str) -> Goal:
    row = conn.execute(f"SELECT {_COLS} FROM goals WHERE goal_id = ?", [goal_id]).fetchone()
    if row is None:
        raise NotFound(f"Không tìm thấy mục tiêu {goal_id}.", goal_id=goal_id)
    return _goal(row)


def active_goal(conn: duckdb.DuckDBPyConnection, campaign_id: str) -> Goal | None:
    row = conn.execute(
        f"SELECT {_COLS} FROM goals WHERE scope = 'campaign' AND scope_id = ? AND active"
        " ORDER BY created_at DESC LIMIT 1",
        [campaign_id],
    ).fetchone()
    return None if row is None else _goal(row)


# ---------------------------------------------------------------------- objectives
@dataclass(frozen=True)
class Outcome:
    """Per-sample results of one action (same samples for every action)."""

    key: str
    feasible: bool
    j: FloatArr  # [S] VND
    sold: FloatArr  # [S] units (campaign total)
    spend: FloatArr  # [S] VND


@dataclass
class Selection:
    """Ranking of eligible actions (best first) and per-action goal diagnostics."""

    kind: GoalKind
    metric: Literal["objective", "units_sold"]
    ranked: list[int]
    eligible: list[bool]
    goal_value: list[float]  # E[J] or E[sold]
    p_target: list[float | None]  # sell_target: P(Σ sold ≥ N)
    notes: dict[int, str] = field(default_factory=dict)
    none_meets_goal: bool = False
    # ``ranked[1]`` (runner-up) meets the goal constraint too. False only for goals with a
    # constraint (sell_target, clear_stock) when the best is the only action meeting it:
    # the runner-up is then the best action that MISSES the goal, a reference not a rival.
    runner_up_meets_goal: bool = True
    # action index → index of an earlier-ranked action with identical results on every
    # sample (e.g. pause ≡ pause_until_restock when no restock lands in the horizon);
    # duplicates are left out of ``ranked`` so they never become a fake runner-up.
    duplicate_of: dict[int, int] = field(default_factory=dict)

    def score(self, o: Outcome) -> FloatArr:
        return o.sold if self.metric == "units_sold" else o.j


def same_outcome(a: Outcome, b: Outcome) -> bool:
    """Identical per-sample results (J, units, spend): choosing one or the other is the
    same decision."""
    return (
        np.array_equal(a.j, b.j)
        and np.array_equal(a.sold, b.sold)
        and np.array_equal(a.spend, b.spend)
    )


def select_actions(
    goal_kind: GoalKind,
    outcomes: Sequence[Outcome],
    *,
    target_units: int | None = None,
    sell_target_prob: float = 0.8,
    max_loss_vnd: float | None = None,
    min_marginal_return: float = 1.0,
    max_spend_vnd: float | None = None,
) -> Selection:
    constraints = {"sell_target_prob": sell_target_prob, "min_marginal_return": min_marginal_return}
    if max_loss_vnd is not None:
        constraints["max_loss_vnd"] = max_loss_vnd
    if max_spend_vnd is not None:
        constraints["max_spend_vnd"] = max_spend_vnd
    validate_goal_params(constraints)
    validate_target_units(target_units, required=goal_kind == "sell_target")
    n = len(outcomes)
    ej = [float(np.mean(o.j)) for o in outcomes]
    es = [float(np.mean(o.sold)) for o in outcomes]
    sp = [float(np.mean(o.spend)) for o in outcomes]
    feasible = [o.feasible for o in outcomes]
    eligible = list(feasible)
    p_target: list[float | None] = [None] * n
    notes: dict[int, str] = {}
    # The total spend limit applies to every simulated outcome, not just its mean
    # or one day's budget. Exclude these actions from fallback and references too.
    for i, outcome in enumerate(outcomes):
        if max_spend_vnd is not None and not np.all(outcome.spend <= max_spend_vnd):
            feasible[i] = eligible[i] = False
            notes[i] = f"Chi quảng cáo trong kỳ mô phỏng vượt giới hạn {max_spend_vnd:,.0f}đ"
        if (
            max_loss_vnd is not None
            and goal_kind != "clear_stock"
            and (not math.isfinite(ej[i]) or ej[i] < -max_loss_vnd)
        ):
            feasible[i] = eligible[i] = False
            notes[i] = f"Lỗ dự kiến {-ej[i]:,.0f}đ vượt mức chấp nhận {max_loss_vnd:,.0f}đ"
    metric: Literal["objective", "units_sold"] = "objective"
    value = ej
    fallback_cheapest = False

    if goal_kind == "sell_target":
        if target_units is None:
            raise ValidationFailed("Mục tiêu bán đủ số lượng cần target_units.")
        for i, o in enumerate(outcomes):
            p = float(np.mean(o.sold >= target_units))
            p_target[i] = p
            if p < sell_target_prob:
                eligible[i] = False
                notes.setdefault(
                    i, f"Xác suất bán đủ {target_units} sp là {p:.0%} < {sell_target_prob:.0%}"
                )
    elif goal_kind == "clear_stock":
        metric, value = "units_sold", es
        loss_limit = max_loss_vnd if max_loss_vnd is not None else 0.0
        for i in range(n):
            if ej[i] < -loss_limit:
                eligible[i] = False
                notes.setdefault(
                    i, f"Lỗ dự kiến {-ej[i]:,.0f}đ vượt mức chấp nhận {loss_limit:,.0f}đ"
                )
    elif goal_kind == "efficiency":
        fallback_cheapest = True
        for i in range(n):
            if not feasible[i]:
                continue
            for j in range(n):
                if j == i or not feasible[j] or sp[j] >= sp[i]:
                    continue
                ratio = (ej[i] - ej[j]) / (sp[i] - sp[j])
                if ratio < min_marginal_return:
                    eligible[i] = False
                    notes[i] = (
                        f"Mỗi 1đ chi thêm so với {outcomes[j].key} chỉ mang về {ratio:.2f}đ"
                        f" (< {min_marginal_return:.2f})"
                    )
                    break

    duplicate_of: dict[int, int] = {}

    def dedupe(idx: list[int], kept: list[int]) -> list[int]:
        out: list[int] = []
        for i in idx:
            twin = next((k for k in (*kept, *out) if same_outcome(outcomes[i], outcomes[k])), None)
            if twin is None:
                out.append(i)
            else:
                duplicate_of[i] = twin
        return out

    order = sorted((i for i in range(n) if eligible[i]), key=lambda i: (-value[i], sp[i]))
    ranked = dedupe(order, [])
    none = not ranked
    if none and fallback_cheapest:
        cheap = sorted((i for i in range(n) if feasible[i]), key=lambda i: (sp[i], -ej[i]))
        ranked, none = cheap[:1], not bool(cheap)
    runner_meets = True
    if ranked and not none and len(ranked) < 2:
        # runner-up = the best feasible action outside the eligible set (it misses the goal)
        rest = sorted(
            (i for i in range(n) if feasible[i] and i not in ranked and i not in duplicate_of),
            key=lambda i: (-value[i], sp[i]),
        )
        rest = dedupe(rest, ranked)
        if rest and goal_kind in ("sell_target", "clear_stock"):
            runner_meets = False
        ranked = ranked + rest
    return Selection(
        kind=goal_kind,
        metric=metric,
        ranked=ranked if not none else [],
        eligible=eligible,
        goal_value=value,
        p_target=p_target,
        notes=notes,
        none_meets_goal=none,
        runner_up_meets_goal=runner_meets,
        duplicate_of=duplicate_of,
    )
