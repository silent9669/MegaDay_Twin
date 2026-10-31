"""ECO-03 · goals table + 4 objectives (05 §4)."""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb
import numpy as np
import pytest

from app.core.errors import NotFound, ValidationFailed
from app.economics.goals import (
    Outcome,
    active_goal,
    create_goal,
    get_goal,
    select_actions,
    validate_goal_params,
)
from app.warehouse.db import migrate

S = 2000
RNG = np.random.default_rng(1)


def _o(
    key: str, j: float, sold: float, spend: float, feasible: bool = True, sd: float = 1.0
) -> Outcome:
    return Outcome(
        key,
        feasible,
        j + RNG.normal(0, sd, S) * max(abs(j) * 0.01, 1),
        np.maximum(sold + RNG.normal(0, 3, S), 0),
        np.full(S, spend),
    )


def test_create_and_read_goal() -> None:
    c = duckdb.connect()
    migrate(c)
    g = create_goal(
        c,
        scope_id="CD002",
        kind="profit",
        deadline=datetime(2026, 11, 11, 17, tzinfo=UTC),
        confirmed_by="lead-an",
    )
    assert get_goal(c, g.goal_id).kind == "profit"
    a = active_goal(c, "CD002")
    assert a is not None and a.goal_id == g.goal_id
    with pytest.raises(NotFound):
        get_goal(c, "nope")
    with pytest.raises(ValidationFailed):
        create_goal(c, scope_id="CD002", kind="profit", deadline=g.deadline, confirmed_by="ai")
    with pytest.raises(ValidationFailed):
        create_goal(
            c, scope_id="CD002", kind="sell_target", deadline=g.deadline, confirmed_by="lead"
        )


def test_profit_ignores_infeasible() -> None:
    outs = [
        _o("continue", -4e6, 83, 5e6),
        _o("pause", 1e6, 80, 0, feasible=False),
        _o("schedule", -2e5, 83, 1.2e6),
    ]
    sel = select_actions("profit", outs)
    assert sel.ranked[:2] == [2, 0]
    assert not sel.none_meets_goal


def test_sell_target() -> None:
    outs = [_o("continue", 1e6, 100, 5e5), _o("pause", 2e6, 60, 0)]
    sel = select_actions("sell_target", outs, target_units=90, sell_target_prob=0.8)
    assert sel.ranked[0] == 0 and sel.eligible == [True, False]
    assert sel.p_target[0] is not None and sel.p_target[0] > 0.9
    assert sel.p_target[1] == 0.0 and "Xác suất bán đủ" in sel.notes[1]
    none = select_actions("sell_target", outs, target_units=500)
    assert none.none_meets_goal and none.ranked == []


def test_clear_stock() -> None:
    outs = [
        _o("continue", -50_000, 30, 3e5),
        _o("pause", 1e5, 20, 0),
        _o("reduce", 20_000, 28, 1e5),
    ]
    sel = select_actions("clear_stock", outs, max_loss_vnd=100_000)
    assert sel.metric == "units_sold" and sel.ranked[0] == 0
    strict = select_actions("clear_stock", outs, max_loss_vnd=0)
    assert strict.ranked[0] == 2 and not strict.eligible[0]


def test_efficiency_marginal_return() -> None:
    outs = [
        _o("pause", 0, 10, 0),
        _o("reduce", 600_000, 15, 500_000),
        _o("continue", 700_000, 18, 1e6),
    ]
    sel = select_actions("efficiency", outs, min_marginal_return=1.0)
    # reduce: +600k per 500k spend (1,2 ≥ 1) ok; continue: +100k for the extra 500k (0,2) no
    assert sel.ranked[0] == 1 and not sel.eligible[2]
    tight = select_actions("efficiency", outs, min_marginal_return=5.0)
    assert tight.ranked[0] == 0  # defaults to pause


def test_identical_actions_never_runner_up() -> None:
    """M3-whatif §5.1: pause ≡ pause_until_restock (no restock in the horizon) gave p = 0."""
    pause = _o("pause", 3e6, 95, 0)
    twin = Outcome("pause_until_restock", True, pause.j.copy(), pause.sold.copy(), pause.spend)
    outs = [_o("continue", 1e6, 106, 5e6), pause, twin, _o("reduce", 2e6, 100, 2e6)]
    sel = select_actions("profit", outs)
    assert sel.ranked[:2] == [1, 3] and 2 not in sel.ranked
    assert sel.duplicate_of == {2: 1}
    # the twin listed first ranks first; the original becomes the duplicate
    sel2 = select_actions("profit", [twin, pause, outs[0]])
    assert sel2.ranked == [0, 2] and sel2.duplicate_of == {1: 0}


def test_sell_target_runner_up_from_goal_meeting_actions() -> None:
    """M3-whatif §5.2: the runner-up meets the goal when ≥ 2 actions do; with one only, it is
    the best action missing the goal and ``runner_up_meets_goal`` is False."""
    outs = [
        _o("continue", -1.5e6, 106, 5e6),
        _o("schedule", 2.5e6, 90, 1e6),
        _o("reduce30", 1e5, 104, 3.6e6),
        _o("reduce50", 1.2e6, 98, 2.5e6),
    ]
    two = select_actions("sell_target", outs, target_units=100, sell_target_prob=0.8)
    assert two.ranked[:2] == [2, 0] and two.runner_up_meets_goal
    one = select_actions("sell_target", outs, target_units=105, sell_target_prob=0.6)
    assert one.ranked[0] == 0 and not one.runner_up_meets_goal
    assert one.ranked[1] == 1  # best (by E[J]) among the actions missing the goal
    prof = select_actions("profit", outs)
    assert prof.runner_up_meets_goal


@pytest.mark.parametrize("kind", ["profit", "sell_target", "clear_stock", "efficiency"])
def test_zero_total_spend_limit_excludes_paid_actions(kind: str) -> None:
    outs = [_o("paid", 100_000, 100, 1), _o("free", 10_000, 100, 0)]
    sel = select_actions(
        kind, outs, target_units=1 if kind == "sell_target" else None, max_spend_vnd=0
    )  # type: ignore[arg-type]
    assert sel.ranked == [1]
    assert sel.eligible == [False, True]
    assert "giới hạn 0đ" in sel.notes[0]


def test_spend_limit_applies_to_each_sample_not_average() -> None:
    outs = [
        Outcome("variable", True, np.array([100.0, 100.0]), np.ones(2), np.array([0.0, 200.0])),
        Outcome("within", True, np.array([10.0, 10.0]), np.ones(2), np.array([100.0, 100.0])),
    ]
    sel = select_actions("profit", outs, max_spend_vnd=100)
    assert sel.ranked == [1] and not sel.eligible[0]


@pytest.mark.parametrize("kind", ["profit", "sell_target", "efficiency"])
def test_explicit_loss_limit_applies_to_all_goal_kinds(kind: str) -> None:
    outs = [_o("losing", -100, 100, 0, sd=0), _o("within", 50, 100, 20, sd=0)]
    sel = select_actions(
        kind, outs, target_units=1 if kind == "sell_target" else None, max_loss_vnd=0
    )  # type: ignore[arg-type]
    assert sel.ranked == [1] and not sel.eligible[0]


def test_efficiency_never_falls_back_outside_hard_constraints() -> None:
    outs = [_o("expensive", 100, 100, 200, sd=0), _o("cheap_loss", -100, 100, 0, sd=0)]
    sel = select_actions("efficiency", outs, max_loss_vnd=0, max_spend_vnd=100)
    assert sel.none_meets_goal and not sel.ranked
    assert sel.eligible == [False, False]


def test_clear_stock_default_still_rejects_expected_loss() -> None:
    sel = select_actions("clear_stock", [_o("loss", -100, 100, 0, sd=0)])
    assert sel.none_meets_goal
    # Profit without an explicit loss constraint preserves the existing behavior.
    assert select_actions("profit", [_o("loss", -100, 100, 0, sd=0)]).ranked == [0]


@pytest.mark.parametrize(
    "key", ["max_spend_vnd", "max_loss_vnd", "sell_target_prob", "min_marginal_return"]
)
@pytest.mark.parametrize("value", [-1, True, "100", None, float("nan"), float("inf"), 10**400])
def test_goal_constraints_reject_nonfinite_or_coerced_values(key: str, value: object) -> None:
    with pytest.raises(ValidationFailed):
        validate_goal_params({key: value})


@pytest.mark.parametrize("probability", [0, 1.01])
def test_goal_probability_must_be_between_zero_and_one(probability: float) -> None:
    with pytest.raises(ValidationFailed):
        validate_goal_params({"sell_target_prob": probability})


@pytest.mark.parametrize("target", [0, -1, True, 1.5, "3"])
def test_target_is_a_positive_integer_without_coercion(target: object) -> None:
    with duckdb.connect() as conn, pytest.raises(ValidationFailed):
        create_goal(
            conn,
            scope_id="CD002",
            kind="sell_target",
            target_units=target,  # type: ignore[arg-type]
            deadline=datetime(2026, 11, 11, 17, tzinfo=UTC),
            confirmed_by="lead",
        )
