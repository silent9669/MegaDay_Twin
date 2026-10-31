"""SIM-04 / SIM-05 · recommendation rules, zone bar, flip conditions, persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import duckdb
import numpy as np

from app.economics.parameters import ParamSet, parse_overrides
from app.simulator.engine import Perturb, SimRun, simulate
from app.simulator.recommend import (
    build_recommendation,
    min_gain_needed,
    persist,
    scenario_results,
)
from app.simulator.schemas import ActionSpec, SimOutput
from app.warehouse.db import migrate
from tests.unit.simulator.factory import make_inputs, sku

ACTS = [
    ActionSpec(kind="continue"),
    ActionSpec(kind="pause"),
    ActionSpec(kind="reduce_budget", pct=0.5),
    ActionSpec(kind="schedule_hours", windows_local=[(0, 2), (12, 14)]),
]


def _run(inp, acts=ACTS, samples=1000):  # type: ignore[no-untyped-def]
    run = simulate(inp, acts, samples, 20261111)

    def resim(a: Sequence[ActionSpec], pt: Perturb, n: int) -> SimRun:
        return simulate(inp, a, n, 20261111, perturb=pt)

    return run, resim


def test_flash_day_schedule_hours_wins() -> None:
    """Toy version of 05 §10: demand ≫ stock, thin margin, committed flash hours."""
    T = 23  # noqa: N806
    noi = sku(
        "NOI",
        T,
        lam=6.0,
        m=9_280,
        price=531_000,
        stock=76,
        reserve=23,
        plans=[(date(2026, 11, 12), 400)],
    )
    inp = make_inputs(
        [noi], T=T, budget=5_592_000, must_run_hours=[0, 1, 12, 13], flash_hours=[0, 1, 12, 13]
    )
    run, resim = _run(inp, [*ACTS, ActionSpec(kind="pause_until_restock")])
    rec, sel = build_recommendation(run, resim)
    assert rec.status == "recommend"
    assert rec.best is not None and rec.best.kind == "schedule_hours"
    assert rec.p_best_beats_runner_up is not None and rec.p_best_beats_runner_up > 0.99
    by = run.by_key()
    assert not by["pause"].feasible and not by["pause_until_restock"].feasible
    assert any("cam kết với sàn" in r for r in rec.reasons)
    assert any("9.280đ" in r for r in rec.reasons)
    res = scenario_results(run, sel)
    assert [r.violations for r in res if r.action.kind == "pause"] == [["campaign_must_run"]]
    assert rec.inputs_ref.forecast_ids == ["fc-NOI"] and rec.inputs_ref.iroas_source == "prior"
    assert "decision_min_prob" in rec.inputs_ref.param_version


def test_undetermined_when_gain_small() -> None:
    inp = make_inputs([sku("A", 24, lam=0.3, m=40_000, stock=1000)], budget=24_000)
    run, resim = _run(inp, ACTS[:2])
    rec, _ = build_recommendation(run, resim, with_flips=False)
    assert rec.status == "undetermined"
    assert rec.best is not None and rec.runner_up is not None


def test_zone_bar_and_flip_on_leftover_value() -> None:
    inp = make_inputs(
        [sku("A", 24, lam=1.5, m=40_000, price=89_000, stock=1000, v=10_000)], budget=600_000
    )
    run, resim = _run(inp, ACTS[:3])
    rec, _ = build_recommendation(run, resim)
    assert rec.zone_bar is not None
    assert rec.zone_bar.verdict in ("pause_better", "undetermined", "continue_better")
    assert [a.label for a in rec.zone_bar.alt_thresholds] == ["hàng dư 0đ"]
    for f in rec.flip_conditions:
        assert f.text.startswith("Nếu")


def test_hold_better_has_no_zone_bar() -> None:
    inp = make_inputs([sku("A", 24, lam=1.0, m=9_000, stock=100, v=200_000)])
    run, resim = _run(inp, ACTS[:2], 300)
    rec, _ = build_recommendation(run, resim, with_flips=False)
    assert rec.zone_bar is None and any("Giữ hàng lợi hơn bán" in r for r in rec.reasons)


def test_sell_target_none_meets_goal() -> None:
    inp = make_inputs([sku("A", 24, lam=1.0, stock=100)], goal_kind="sell_target", target_units=500)
    run, resim = _run(inp, ACTS[:2], 300)
    rec, sel = build_recommendation(run, resim)
    assert rec.status == "none_meets_goal" and rec.best is None
    assert sel.p_target == [0.0, 0.0]
    assert "chưa giải quyết được" in rec.reasons[0]


def test_confirmed_total_budget_is_forwarded_and_excludes_misleading_economic_comparison() -> None:
    inp = make_inputs([sku("A", 24, lam=1.0, stock=100)], budget=600_000)
    inp.goal = inp.goal.model_copy(update={"params": {"max_spend_vnd": 0}})
    run, resim = _run(inp, ACTS[:3], 200)
    rec, sel = build_recommendation(run, resim)
    assert rec.best is not None and rec.best.kind == "pause"
    assert sel.eligible == [False, True, False]
    assert rec.zone_bar is None and rec.flip_conditions == []
    assert any("vượt giới hạn chi" in reason for reason in rec.reasons)
    results = scenario_results(run, sel)
    assert not results[0].goal_eligible and results[0].goal_note is not None


def test_confirmed_budget_can_leave_no_feasible_action() -> None:
    inp = make_inputs(
        [sku("A", 24, lam=1.0, stock=100)],
        budget=600_000,
        goal_kind="efficiency",
        must_run_hours=[0, 1],
    )
    inp.goal = inp.goal.model_copy(update={"params": {"max_spend_vnd": 0}})
    run, resim = _run(inp, ACTS[:3], 200)
    rec, sel = build_recommendation(run, resim)
    assert sel.none_meets_goal and rec.status == "none_meets_goal"
    assert rec.best is None and rec.runner_up is None


def test_scenario_param_caps_label() -> None:
    ps = ParamSet(stored={}, overrides=parse_overrides({"rank_loss_after_pause": 0.1}))
    inp = make_inputs([sku("A", 24)], params=ps)
    inp.iroas = inp.iroas.model_copy(update={"confidence": "verified"})
    inp.iroas_source = "estimate"
    for s in inp.skus:
        s.label = "verified"
    run, resim = _run(inp, ACTS[:2], 200)
    rec, _ = build_recommendation(run, resim, with_flips=False)
    assert rec.confidence == "reference"


def test_persist_and_supersede() -> None:
    conn = duckdb.connect()
    migrate(conn)
    inp = make_inputs([sku("A", 24, lam=2.0, stock=100)])
    run, resim = _run(inp, ACTS[:2], 200)
    events = []
    for i in range(2):
        rec, sel = build_recommendation(run, resim, with_flips=False)
        out = SimOutput(
            scenario_id=f"scn-{i}",
            results=scenario_results(run, sel),
            recommendation=rec,
            input_hash="h",
        )
        events.append(
            persist(conn, out, inp, overrides={}, samples=200, seed=1, requested_by="user")
        )
    assert events[0] is not None and events[1] is None  # same best → no change event
    rows = conn.execute(
        "SELECT recommendation_id, superseded_by FROM recommendations ORDER BY created_at"
    ).fetchall()
    assert rows[0][1] == rows[1][0] and rows[1][1] is None
    assert conn.execute("SELECT count(*) FROM scenario_results").fetchone() == (4,)
    sc = conn.execute("SELECT param_version, inputs_ref FROM scenarios").fetchall()
    assert len(sc) == 2 and "forecast_ids" in sc[0][1]


# ---------------------------------------------------------------- M3-whatif regressions
def test_zone_bar_never_says_pause_when_pause_blocked() -> None:
    """M3-whatif §5.4: CD002 11.11 (V = 0) showed "pause tốt hơn" while pause is banned."""
    T = 23  # noqa: N806
    noi = sku("NOI", T, lam=6.0, m=9_280, price=531_000, stock=76, reserve=23)
    inp = make_inputs(
        [noi], T=T, budget=5_592_000, must_run_hours=[0, 1, 12, 13], flash_hours=[0, 1, 12, 13]
    )
    run, resim = _run(inp)
    rec, _ = build_recommendation(run, resim, with_flips=False)
    bar = rec.zone_bar
    assert bar is not None and bar.pause_feasible is False
    assert bar.pause_violations == ["campaign_must_run"]
    assert bar.verdict_note and "bị cấm" in bar.verdict_note and "flash sale" in bar.verdict_note
    assert bar.verdict_note in rec.reasons
    # free campaign: the bar is unchanged (no note)
    free = make_inputs([sku("A", 24, lam=1.5, m=40_000, price=89_000, stock=1000)])
    rec2, _ = build_recommendation(*_run(free, ACTS[:2], 300), with_flips=False)
    assert rec2.zone_bar is None or (
        rec2.zone_bar.pause_feasible and not rec2.zone_bar.verdict_note
    )


def test_identical_twin_is_not_runner_up() -> None:
    """M3-whatif §5.1: pause ≡ pause_until_restock (restock after the horizon) → p = 0."""
    a = sku("A", 24, lam=2.0, m=9_000, stock=1000, plans=[(date(2026, 11, 20), 100)])
    inp = make_inputs([a], budget=2_400_000)
    run, resim = _run(inp, [*ACTS, ActionSpec(kind="pause_until_restock")], 500)
    by = run.by_key()
    assert (by["pause"].j == by["pause_until_restock"].j).all()
    rec, sel = build_recommendation(run, resim, with_flips=False)
    assert rec.best is not None and rec.runner_up is not None
    assert {rec.best.kind, rec.runner_up.kind} != {"pause", "pause_until_restock"}
    assert rec.p_best_beats_runner_up is not None and rec.p_best_beats_runner_up > 0.5
    assert len(sel.duplicate_of) == 1


def test_sell_target_only_one_action_meets_goal() -> None:
    """M3-whatif §5.2: runner-up missing the goal is a reference, not a rival (no p)."""
    inp = make_inputs([sku("A", 24, lam=2.0, m=9_000, stock=1000)], budget=2_400_000)
    run, resim = _run(inp, ACTS[:2], 500)
    by = run.by_key()
    sold_c = by["continue"].units_sold
    target = int(np.floor(np.quantile(sold_c, 0.15)))
    others = [k for k in by if k != "continue"]
    assert all(np.mean(by[k].units_sold >= target) < 0.8 for k in others)
    inp.goal = inp.goal.model_copy(update={"kind": "sell_target", "target_units": target})
    rec, _ = build_recommendation(run, resim)
    assert rec.best is not None and rec.best.kind == "continue"
    assert rec.status == "recommend" and not rec.runner_up_meets_goal
    assert rec.runner_up is not None and rec.p_best_beats_runner_up is None
    assert rec.expected_gain_vnd is None and rec.goal_cost_vnd is not None
    assert rec.goal_cost_vnd > 0 and rec.flip_conditions == []
    assert any(f"Chỉ Chạy tiếp đạt mục tiêu bán đủ {target} sp" in r for r in rec.reasons), (
        rec.reasons
    )


def test_min_gain_ignores_leftover_term() -> None:
    """M3-whatif §5.3: 5 % of |E[J]| was inflated by V × stock_end."""
    inp = make_inputs([sku("A", 24, lam=1.0, m=40_000, stock=5000, v=100_000)])
    run, _ = _run(inp, ACTS[:2], 300)
    best = run.by_key()["pause"]
    full = 0.05 * abs(float(np.mean(best.j)))
    need = min_gain_needed(best, 200_000)
    assert full > 20_000_000  # 5 % of ~500 tr of leftover value
    assert need == max(200_000, 0.05 * abs(float(np.mean(best.j - best.leftover_value))))
    assert need < 1_000_000


def test_all_actions_identical_is_undetermined() -> None:
    """Campaign that spends nothing in the horizon: continue ≡ pause ≡ … → no fake
    "recommend continue" (CD008 / CD033 on 06/11)."""
    inp = make_inputs([sku("A", 24, lam=1.0, stock=100)], budget=0.0)
    run, resim = _run(inp, ACTS, 300)
    rec, sel = build_recommendation(run, resim, with_flips=False)
    assert len(sel.duplicate_of) == len(ACTS) - 1 and rec.runner_up is None
    assert rec.status == "undetermined"
    assert any("như nhau" in r for r in rec.reasons)
