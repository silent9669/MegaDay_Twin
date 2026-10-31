from __future__ import annotations

from datetime import timedelta

import duckdb
import numpy as np
import pytest

from app.api.scenarios import load_run
from app.economics.contribution import ContributionTable, SkuEconomics
from app.simulator import recommend, service
from app.simulator.constraints import UNLIMITED
from app.simulator.engine import Perturb, SimRun, simulate
from app.simulator.schemas import ActionSpec, SimOutput, SimRequest
from app.simulator.trace import build_trace
from app.warehouse.db import migrate
from tests.unit.simulator.factory import START, make_inputs, sku


@pytest.fixture
def conn():  # type: ignore[no-untyped-def]
    db = duckdb.connect(":memory:")
    migrate(db)
    yield db
    db.close()


def make_run():  # type: ignore[no-untyped-def]
    first = sku("A", 3, lam=8, stock=10, v=20)
    first.m = np.array([100.0, 200.0, 300.0])
    inp = make_inputs([first, sku("B", 3, lam=10, stock=200)], T=3, budget=1000)
    inp.penalty = 17.0
    return simulate(inp, [ActionSpec(kind="continue"), ActionSpec(kind="pause")], 100, 42)


def test_trace_reconciles_real_samples_and_hourly_sku_contribution(conn):  # type: ignore[no-untyped-def]
    run = make_run()
    trace = build_trace(conn, run, "hash-test")
    assert trace["start"].endswith("+07:00")
    assert trace["data_as_of"].endswith("+07:00")
    assert trace["economic_scope"] == "campaign"
    assert trace["sources"]["iroas"]["source"] == "prior"
    for actual, evidence in zip(run.runs, trace["actions"], strict=True):
        metrics = evidence["metrics"]
        assert metrics["objective"]["mean"] == pytest.approx(float(actual.j.mean()))
        assert metrics["stockout_penalty"]["mean"] == pytest.approx(
            float((actual.lost_units * 17).mean())
        )
        assert evidence["reconciliation"]["residual_vnd"] == pytest.approx(0)
        total = sum(s["contribution"]["mean"] for s in evidence["per_sku"])
        assert total == pytest.approx(metrics["contribution"]["mean"])
        assert np.allclose(sum(actual.contribution_by_sku.values()), actual.contribution)
        assert not np.allclose(actual.contribution_by_sku["A"], 100 * actual.sold_by_sku["A"])
        assert sum(evidence["spend_hourly"]) == pytest.approx(metrics["spend"]["mean"])


def test_trace_records_margin_components_and_unknown_stock(conn):  # type: ignore[no-untyped-def]
    item = sku("A", 2, stock=UNLIMITED, price=100, m=63)
    inp = make_inputs([item], T=2)
    inp.economics = ContributionTable(
        by_key={
            ("A", START.date()): SkuEconomics("A", START.date(), 100, "orders", 20, 5, 0.12, 63),
        }
    )
    run = simulate(inp, [ActionSpec(kind="continue")], 50, 42)
    evidence = build_trace(conn, run, "hash")["skus"][0]
    assert evidence["stock_start"] is None and evidence["stock_unlimited"]
    economics = evidence["economics_by_day"][0]
    assert economics["m"] == (
        economics["eff_price"]
        - economics["unit_cost"]
        - economics["fee_rate"] * economics["eff_price"]
        - economics["packaging_cost"]
    )


def test_source_batches_are_relevant_and_before_start(conn):  # type: ignore[no-untyped-def]
    for campaign in ("CDX", "OTHER"):
        conn.execute(
            "INSERT INTO campaigns VALUES (?, ?, 'test', 100, ?, FALSE)",
            [campaign, campaign, START],
        )
    for batch, campaign, hour in [
        ("valid", "CDX", START - timedelta(hours=1)),
        ("other", "OTHER", START - timedelta(hours=1)),
        ("future", "CDX", START + timedelta(hours=1)),
    ]:
        conn.execute(
            "INSERT INTO ingest_batches (batch_id, source_type, file_name, file_sha256,"
            " received_at, as_of, mode, status) VALUES (?, 'ads', ?, ?, ?, ?, 'history', 'ok')",
            [batch, f"{batch}.csv", batch, hour, hour],
        )
        conn.execute(
            "INSERT INTO ad_hourly (campaign_id, hour_utc, status, batch_id)"
            " VALUES (?, ?, 'running', ?)",
            [campaign, hour, batch],
        )
    trace = build_trace(conn, make_run(), "hash")
    assert [b["batch_id"] for b in trace["sources"]["batches"]] == ["valid"]


def test_focused_sell_target_never_counts_other_sku_sales():
    inp = make_inputs(
        [sku("A", 2, lam=0), sku("B", 2, lam=50, stock=1000)],
        T=2,
        goal_kind="sell_target",
        target_units=10,
    )
    inp.goal.params["focus_sku"] = "A"
    run = simulate(inp, [ActionSpec(kind="continue"), ActionSpec(kind="pause")], 100, 42)
    selection = recommend.select(run)
    assert selection.none_meets_goal and selection.p_target == [0, 0]
    for result in recommend.scenario_results(run, selection):
        assert result.goal_sku == "A" and result.goal_units_sold.mean == 0
        assert result.units_sold.mean > 10 and not result.goal_eligible


def test_focused_clear_stock_ranking_probability_and_reasons_use_selected_sku():
    inp = make_inputs([sku("A", 2), sku("B", 2)], T=2, goal_kind="clear_stock")
    inp.goal.params["focus_sku"] = "A"
    run = simulate(inp, [ActionSpec(kind="continue"), ActionSpec(kind="pause")], 50, 42)
    # Aggregate sales favor pause; focus sales favor continue. Money is eligible for both.
    for action, focused, aggregate in zip(run.runs, [20, 2], [30, 200], strict=True):
        action.sold_by_sku["A"] = np.full(50, focused, dtype=float)
        action.units_sold = np.full(50, aggregate, dtype=float)
        action.j = np.full(50, 1000, dtype=float)

    def resim(*_args):  # type: ignore[no-untyped-def]
        raise AssertionError("Focused quantities must not use a campaign economic zone bar")

    rec, _ = recommend.build_recommendation(run, resim, with_flips=False)
    assert rec.best.kind == "continue" and rec.p_best_beats_runner_up == 1
    assert any("18.0 sp" in reason for reason in rec.reasons)
    assert rec.zone_bar is None


def test_trace_and_complete_results_survive_readback_after_data_changes(conn):  # type: ignore[no-untyped-def]
    run = make_run()
    run.inputs.goal.kind = "sell_target"
    run.inputs.goal.target_units = 100
    run.inputs.goal.params["focus_sku"] = "A"

    def resim(acts, pt: Perturb, samples: int) -> SimRun:  # type: ignore[no-untyped-def]
        return simulate(run.inputs, acts, samples, run.seed, perturb=pt)

    rec, selection = recommend.build_recommendation(run, resim, with_flips=False)
    out = SimOutput(
        scenario_id="scn-trace",
        results=recommend.scenario_results(run, selection),
        recommendation=rec,
        input_hash="hash",
        calculation_trace=build_trace(conn, run, "hash"),
    )
    req = SimRequest(
        campaign_id="CDX",
        goal_id=run.inputs.goal.goal_id,
        start=START,
        horizon_hours=3,
        samples=100,
        seed=42,
    )
    recommend.persist(
        conn,
        out,
        run.inputs,
        overrides={},
        samples=100,
        seed=42,
        requested_by="tester",
        request_json=req.model_dump_json(),
    )
    run.inputs.skus[0].m[:] = -999999
    conn.execute("DELETE FROM forecasts")
    loaded = load_run(conn, "scn-trace")
    assert loaded.calculation_trace == out.calculation_trace
    assert [r.goal_units_sold for r in loaded.results] == [r.goal_units_sold for r in out.results]
    assert [r.p_target for r in loaded.results] == [r.p_target for r in out.results]
    assert [r.goal_eligible for r in loaded.results] == [r.goal_eligible for r in out.results]
    conn.execute("DELETE FROM scenario_calculation_traces WHERE scenario_id = 'scn-trace'")
    assert load_run(conn, "scn-trace").calculation_trace is None


def test_failed_trace_persistence_rolls_back_scenario_and_recommendation(conn, monkeypatch):  # type: ignore[no-untyped-def]
    inp = make_run().inputs
    monkeypatch.setattr(service, "get_goal", lambda *_args: inp.goal)
    monkeypatch.setattr(service, "load_param_set", lambda *_args, **_kw: inp.params)
    monkeypatch.setattr(service, "load_inputs", lambda *_args, **_kw: inp)
    original = recommend.persist

    def failing(*args, **kwargs):  # type: ignore[no-untyped-def]
        original(*args, **kwargs)
        raise RuntimeError("forced persistence failure")

    monkeypatch.setattr(recommend, "persist", failing)
    with pytest.raises(RuntimeError, match="forced"):
        service.run_simulation(
            conn,
            SimRequest(campaign_id="CDX", goal_id=inp.goal.goal_id, samples=50),
            use_cache=False,
            with_flips=False,
        )
    for table in [
        "scenarios",
        "scenario_calculation_traces",
        "scenario_results",
        "recommendations",
    ]:
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
