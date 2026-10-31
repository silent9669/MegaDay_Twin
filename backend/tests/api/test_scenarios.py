"""API-05: scenarios, break-even, recommendations on the ``nho`` copy (contract + errors)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.helpers import LEAD, OPS, VIEWER, err, is_local_iso, ok

V1 = "/api/v1"
DEADLINE = "2026-11-12T00:00:00+07:00"


@pytest.fixture(scope="module")
def goal_cd002(api: TestClient) -> str:
    body = {"scope_id": "CD002", "kind": "profit", "deadline": DEADLINE}
    return str(ok(api.post(f"{V1}/goals", json=body, headers=LEAD), 201)["data"]["goal_id"])


@pytest.fixture(scope="module")
def first_run(api: TestClient, goal_cd002: str) -> dict[str, Any]:
    body = {"campaign_id": "CD002", "goal_id": goal_cd002, "samples": 300}
    return ok(api.post(f"{V1}/scenarios/run", json=body, headers=OPS))


def test_run_roles_and_missing_goal(api: TestClient) -> None:
    body = {"campaign_id": "CD003", "samples": 300}
    err(api.post(f"{V1}/scenarios/run", json=body, headers=VIEWER), 403, "FORBIDDEN")
    err(api.post(f"{V1}/scenarios/run", json={}, headers=OPS), 400, "VALIDATION_ERROR")
    e = err(api.post(f"{V1}/scenarios/run", json=body, headers=OPS), 422, "BLOCKING_DATA_ISSUE")
    assert e["error"]["details"]["missing"] == "goal"
    err(
        api.post(f"{V1}/scenarios/run", json={"campaign_id": "CD999"}, headers=OPS),
        404,
        "NOT_FOUND",
    )


def test_run_contract(first_run: dict[str, Any], goal_cd002: str) -> None:
    d, meta = first_run["data"], first_run["meta"]
    assert d["scenario_id"].startswith("scn-")
    req = d["request"]
    assert req["campaign_id"] == "CD002" and req["goal_id"] == goal_cd002
    assert req["samples"] == 300 and req["start"] and req["horizon_hours"] >= 1
    kinds = {r["action"]["kind"] for r in d["results"]}
    assert {"continue", "pause", "reduce_budget", "schedule_hours"} <= kinds
    for r in d["results"]:
        assert {"key", "label", "spend", "units_sold", "leftover_units", "objective"} <= set(r)
        if r["feasible"]:
            for k in ("spend", "units_sold", "leftover_units"):
                assert {"mean", "p10", "p50", "p90"} <= set(r[k])
        assert is_local_iso(r["stockout_hour_p50"])
    rec = d["recommendation"]
    assert rec["status"] in ("recommend", "undetermined", "none_meets_goal")
    assert len(rec["reason_sources"]) == len(rec["reasons"])
    assert all(s is None or s["href"].startswith("/api/v1/") for s in rec["reason_sources"])
    assert rec["scenario_id"] == d["scenario_id"] and rec["active"]
    assert is_local_iso(rec["inputs_ref"]["data_as_of"])
    if rec["best"]:
        assert rec["best_label"]
    if rec["zone_bar"] and rec["zone_bar"]["inputs"]:
        assert rec["zone_bar"]["inputs"]["units_left"] > 0
    assert meta["cache_hit"] is False and meta["compute_state"] == "fresh"
    assert meta["confidence"] == rec["confidence"] and meta["data_as_of"]
    assert meta["sources"][0] == {**meta["sources"][0], "kind": "scenario", "id": d["scenario_id"]}


def test_run_cache_hit(api: TestClient, first_run: dict[str, Any]) -> None:
    req = first_run["data"]["request"]
    again = ok(api.post(f"{V1}/scenarios/run", json=req, headers=OPS))
    assert again["meta"]["cache_hit"] is True
    assert again["data"]["scenario_id"] == first_run["data"]["scenario_id"]


def test_overrides(api: TestClient, goal_cd002: str) -> None:
    base = {"campaign_id": "CD002", "goal_id": goal_cd002, "samples": 300}
    e = err(
        api.post(f"{V1}/scenarios/run", json={**base, "param_overrides": {"khong_co": 1}},
                 headers=OPS),
        400,
        "PARAM_OUT_OF_RANGE",
    )  # fmt: skip
    assert e["error"]["details"]["key"] == "khong_co"
    err(
        api.post(
            f"{V1}/scenarios/run",
            json={**base, "param_overrides": {"leftover_value_per_unit": -9_000_000}},  # < −cost
            headers=OPS,
        ),
        400,
        "PARAM_OUT_OF_RANGE",
    )
    big = {"leftover_value_per_unit": 9_000_000}
    e2 = err(
        api.post(f"{V1}/scenarios/run", json={**base, "param_overrides": big}, headers=OPS),
        400,
        "PARAM_OUT_OF_RANGE",
    )
    assert e2["error"]["details"]["max"] == 690_000
    early = ok(
        api.post(
            f"{V1}/scenarios/run",
            json={**base, "param_overrides": {"restock_delay_days": -1}},
            headers=OPS,
        )
    )
    assert early["data"]["request"]["param_overrides"] == {"restock_delay_days": -1.0}
    assert early["data"]["recommendation"]["confidence"] != "verified"  # scenario param ≠ 0


def test_disable_sku_on_shared_budget_409(api: TestClient, goal_cd002: str) -> None:
    body = {"campaign_id": "CD002", "goal_id": goal_cd002, "disable_skus": ["GIAYLOT50"]}
    e = err(api.post(f"{V1}/scenarios/run", json=body, headers=OPS), 409, "CONSTRAINT_VIOLATION")
    assert e["error"]["details"]["constraint"] == "sku_level_control"
    assert "tách SKU" in e["error"]["message"]
    err(
        api.post(f"{V1}/scenarios/run", json={**body, "disable_skus": ["COCMEME-A"]}, headers=OPS),
        400,
        "VALIDATION_ERROR",
    )


def test_get_scenario_and_recommendations(api: TestClient, first_run: dict[str, Any]) -> None:
    sid = first_run["data"]["scenario_id"]
    got = ok(api.get(f"{V1}/scenarios/{sid}", headers=VIEWER))["data"]
    assert got["scenario_id"] == sid and len(got["results"]) == len(first_run["data"]["results"])
    err(api.get(f"{V1}/scenarios/scn-khong-co", headers=VIEWER), 404, "NOT_FOUND")
    recs = ok(api.get(f"{V1}/recommendations", params={"campaign_id": "CD002"}, headers=VIEWER))
    assert len(recs["data"]) == 1 and recs["data"][0]["active"]  # older ones superseded
    rid = recs["data"][0]["recommendation_id"]
    by_sku = ok(api.get(f"{V1}/recommendations", params={"sku": "GIAYLOT50"}, headers=VIEWER))
    assert [r["recommendation_id"] for r in by_sku["data"]] == [rid]
    every = ok(
        api.get(
            f"{V1}/recommendations", params={"campaign_id": "CD002", "active": False},
            headers=VIEWER,
        )
    )["data"]  # fmt: skip
    assert len(every) >= 2 and sum(r["active"] for r in every) == 1
    one = ok(api.get(f"{V1}/recommendations/{rid}", headers=VIEWER))
    assert one["data"]["recommendation_id"] == rid
    assert len(one["data"]["reason_sources"]) == len(one["data"]["reasons"])
    assert one["meta"]["confidence"] == one["data"]["confidence"]
    err(api.get(f"{V1}/recommendations/rec-khong-co", headers=VIEWER), 404, "NOT_FOUND")


def test_scenario_read_back_from_db(api: TestClient, first_run: dict[str, Any]) -> None:
    from app.api import scenarios

    sid = first_run["data"]["scenario_id"]
    scenarios._RUNS.clear()
    got = ok(api.get(f"{V1}/scenarios/{sid}", headers=VIEWER))["data"]
    want = {r["key"]: r for r in first_run["data"]["results"]}
    for r in got["results"]:
        assert r["objective"] == want[r["key"]]["objective"]
        assert r["lost_units"] is None  # not persisted
    assert (
        got["recommendation"]["recommendation_id"]
        == (first_run["data"]["recommendation"]["recommendation_id"])
    )
    assert got["request"]["samples"] == 300


# ---------------------------------------------------------------- break-even (ECO-04)
PAGE10 = {
    "units_left": 120,
    "hours_left": 4,
    "contribution_per_unit_vnd": 45000,
    "spend_per_hour_vnd": 450000,
    "rate_continue_per_hour": 40,
    "pause_rate_interval": [18, 24, 31],
    "leftover_value_per_unit_vnd": 0,
}


def test_breakeven_page10(api: TestClient) -> None:
    body = ok(api.post(f"{V1}/scenarios/breakeven", json=PAGE10, headers=OPS))
    d = body["data"]
    zb = d["zone_bar"]
    assert zb["threshold"] == pytest.approx(22.5) and zb["verdict"] == "undetermined"
    assert zb["alt_thresholds"][0]["threshold"] == pytest.approx(16.5)
    assert d["flip_leftover_value_vnd"] == 16875
    assert zb["inputs"]["units_left"] == 120 and body["meta"]["confidence"] == "scenario"
    v20 = ok(
        api.post(
            f"{V1}/scenarios/breakeven",
            json={**PAGE10, "leftover_value_per_unit_vnd": 20000},
            headers=OPS,
        )
    )["data"]
    assert v20["zone_bar"]["threshold"] == pytest.approx(16.5)
    assert v20["zone_bar"]["verdict"] == "pause_better"


def test_breakeven_errors(api: TestClient) -> None:
    err(api.post(f"{V1}/scenarios/breakeven", json=PAGE10, headers=VIEWER), 403, "FORBIDDEN")
    err(
        api.post(f"{V1}/scenarios/breakeven", json={**PAGE10, "hours_left": 0}, headers=OPS),
        400,
        "VALIDATION_ERROR",
    )
    err(
        api.post(
            f"{V1}/scenarios/breakeven",
            json={**PAGE10, "pause_rate_interval": [30, 20, 10]},
            headers=OPS,
        ),
        400,
        "VALIDATION_ERROR",
    )
    hold = ok(
        api.post(
            f"{V1}/scenarios/breakeven",
            json={**PAGE10, "leftover_value_per_unit_vnd": 50000},
            headers=OPS,
        )
    )["data"]
    assert hold["zone_bar"] is None and hold["status"] == "hold_better" and hold["reason"]


def test_run_waits_for_the_live_write_lock(api: TestClient, first_run: dict[str, Any]) -> None:
    """M5: a run started while the orchestrator recomputes (new goal, live tick) used to
    fail with DuckDB "Conflict on update" (both write forecasts / demand groups); it now
    waits for the live write lock."""
    import threading
    import time

    lock = api.app.state.runtime.lock  # type: ignore[attr-defined]
    lock.acquire()
    timer = threading.Timer(0.5, lock.release)
    timer.start()
    t0 = time.perf_counter()
    body = {"campaign_id": "CD002", "samples": 300}
    out = ok(api.post(f"{V1}/scenarios/run", json=body, headers=OPS))
    waited = time.perf_counter() - t0
    timer.join()
    assert out["data"]["scenario_id"] == first_run["data"]["scenario_id"]  # cache hit
    assert waited >= 0.45


@pytest.mark.parametrize("which", ["live", "run"])
def test_ai_compute_tools_wait_for_the_simulation_locks(
    api: TestClient, first_run: dict[str, Any], which: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M5 Q5: the AI compute tools (``compare_actions``, ``run_scenario``,
    ``compute_breakeven``) write forecasts / demand groups like ``POST /scenarios/run``; they
    take the runtime's live write lock (found without a request: the runtime registers it on
    start) and then ``RUN_LOCK``, so a question asked while a tick recomputes waits."""
    import threading
    import time

    from app.ai.tools import ToolContext, compute
    from app.ai.tools.schemas import CompareActionsArgs, RunScenarioArgs
    from app.simulator import locks

    runtime_lock = api.app.state.runtime.lock  # type: ignore[attr-defined]
    assert locks.live_write_lock() is runtime_lock  # registered by Runtime.start
    lock = runtime_lock if which == "live" else locks.RUN_LOCK
    db = api.app.state.db  # type: ignore[attr-defined]
    settings = api.app.state.settings  # type: ignore[attr-defined]
    started: list[float] = []
    real = compute.run_simulation

    def spy(*a: Any, **kw: Any) -> Any:
        started.append(time.perf_counter())
        assert runtime_lock.locked() and locks.RUN_LOCK.locked()  # both held by the tool
        return real(*a, **kw)

    monkeypatch.setattr(compute, "run_simulation", spy)
    whatif = RunScenarioArgs.model_validate(
        {"campaign_id": "CD002", "goal_override": {"kind": "sell_target", "target_units": 97}}
    )
    calls = (
        lambda ctx: compute.compare_actions(CompareActionsArgs(campaign_id="CD002"), ctx),
        lambda ctx: compute.run_scenario(whatif, ctx),
    )
    for call in calls:
        released: list[float] = []

        def release(released: list[float] = released) -> None:
            released.append(time.perf_counter())
            lock.release()

        lock.acquire()
        timer = threading.Timer(0.3, release)
        timer.start()
        with db.reader() as conn:
            out = call(ToolContext(conn=conn, settings=settings, log_calls=False))
        timer.join()
        assert out.data["results"], out.data
        assert started[-1] >= released[0]  # the simulation began only after the release
    assert not runtime_lock.locked() and not locks.RUN_LOCK.locked()
