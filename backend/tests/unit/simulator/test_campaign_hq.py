from datetime import timedelta

import duckdb
import numpy as np
import pytest

from app.api.campaign_analysis import remaining_goal, revision
from app.core.errors import DataQualityError
from app.simulator.engine import simulate
from app.simulator.recommend import select
from app.simulator.schemas import ActionSpec
from app.simulator.trace import build_trace
from app.warehouse.db import migrate
from tests.unit.simulator.factory import START, make_inputs, sku


def test_hourly_revenue_and_combo_stock_reconcile():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    pot = sku("POT", 3, stock=20, lam=2, price=100, m=30)
    liner = sku("LINER", 3, stock=40, lam=1, price=10, m=2)
    combo = sku("COMBO", 3, lam=4, price=105, m=28, bundle=[("POT", 1), ("LINER", 2)])
    pot.price = np.array([100.0, 200.0, 400.0])
    inp = make_inputs([pot, liner, combo], T=3)
    run = simulate(inp, [ActionSpec(kind="continue"), ActionSpec(kind="pause")], 100, 42)
    trace = build_trace(conn, run, "test")
    for action, saved in zip(run.runs, trace["actions"], strict=True):
        assert np.allclose(
            action.leftover_by_sku["POT"],
            20 - action.sold_by_sku["POT"] - action.sold_by_sku["COMBO"],
        )
        assert np.allclose(
            action.leftover_by_sku["LINER"],
            40 - action.sold_by_sku["LINER"] - 2 * action.sold_by_sku["COMBO"],
        )
        assert not np.allclose(action.revenue_by_sku["POT"], action.sold_by_sku["POT"] * 100)
        m = saved["metrics"]
        assert m["revenue"]["mean"] - m["selling_cost"]["mean"] == pytest.approx(
            m["contribution"]["mean"]
        )
        assert m["after_ads"]["mean"] == pytest.approx(
            m["contribution"]["mean"] - m["spend"]["mean"]
        )
        assert next(s for s in saved["per_sku"] if s["sku_id"] == "COMBO")["leftover_units"] is None
    conn.close()


def test_progress_does_not_reset_target_or_budget():
    inp = make_inputs([sku("A", 1, lam=0)], T=1, goal_kind="sell_target", target_units=10)
    inp.goal.params = {"max_spend_vnd": 100, "hq_progress": {"units": 10, "spend": 100, "cash": 0}}
    run = simulate(inp, [ActionSpec(kind="continue"), ActionSpec(kind="pause")], 50)
    selected = select(run)
    assert selected.p_target == [1.0, 1.0]
    assert not selected.eligible[0]  # no fresh allowance after refresh
    assert selected.eligible[1]
    assert all(np.all(a.units_sold == 0) for a in run.runs)  # table still shows future only


def test_flash_sale_pause_never_becomes_eligible():
    inp = make_inputs([sku("A", 2)], T=2, must_run_hours=[1])
    run = simulate(inp, [ActionSpec(kind="continue"), ActionSpec(kind="pause")], 50)
    assert not run.runs[1].feasible
    assert not select(run).eligible[1]


def test_refresh_waits_for_missing_ads_before_financial_constraints():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    goal = make_inputs([sku("A", 3)], T=3).goal
    goal.params = {"planner_start": START.isoformat(), "max_spend_vnd": 1000}
    with pytest.raises(DataQualityError, match="ads"):
        remaining_goal(conn, goal, START + timedelta(hours=1))
    assert "hq_progress" not in goal.params
    assert revision(conn, "CDX") == revision(conn, "CDX")
    conn.close()
