from datetime import datetime, timedelta

import pytest

from tests.api.helpers import LEAD, OPS, VIEWER


def test_context_uses_live_sources_and_rejects_stale_refresh(api):  # type: ignore[no-untyped-def]
    response = api.get("/api/v1/campaign-analysis/CD002", headers=VIEWER)
    assert response.status_code == 200, response.text
    ctx = response.json()
    assert ctx["campaign"]["campaign_id"] == "CD002"
    assert not ctx["canRun"]
    assert len(ctx["revision"]) == 64
    assert (
        api.post(
            "/api/v1/campaign-analysis/CD002/refresh",
            headers=VIEWER,
            json={"revision": ctx["revision"], "goal_id": "none"},
        ).status_code
        == 403
    )
    assert (
        api.post(
            "/api/v1/campaign-analysis/CD002/refresh",
            headers=OPS,
            json={"revision": "old", "goal_id": "none"},
        ).status_code
        == 409
    )


def test_goal_change_invalidates_revision_without_clock_change(api):  # type: ignore[no-untyped-def]
    before = api.get("/api/v1/campaign-analysis/CD002", headers=LEAD).json()
    response = api.post(
        "/api/v1/goals",
        headers=LEAD,
        json={
            "scope": "campaign",
            "scope_id": "CD002",
            "kind": "profit",
            "deadline": "2026-11-12T00:00:00+07:00",
            "params": {"planner_start": "2026-11-11T00:00:00+07:00"},
        },
    )
    assert response.status_code == 201, response.text
    after = api.get("/api/v1/campaign-analysis/CD002", headers=LEAD).json()
    assert after["revision"] != before["revision"]
    assert after["dataAsOf"] == before["dataAsOf"]
    assert after["goal"]["goal_id"] == response.json()["data"]["goal_id"]


def test_refresh_persists_real_tables_and_does_not_replace_confirmed_goal(api):  # type: ignore[no-untyped-def]
    ctx = api.get("/api/v1/campaign-analysis/CD002", headers=LEAD).json()
    start = datetime.fromisoformat(ctx["dataAsOf"])
    if start.minute or start.second:
        start = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    goal = api.post(
        "/api/v1/goals",
        headers=LEAD,
        json={
            "scope": "campaign",
            "scope_id": "CD002",
            "kind": "profit",
            "deadline": (start + timedelta(hours=3)).isoformat(),
            "params": {"planner_start": start.isoformat()},
        },
    ).json()["data"]
    ctx = api.get("/api/v1/campaign-analysis/CD002", headers=OPS).json()
    response = api.post(
        "/api/v1/campaign-analysis/CD002/refresh",
        headers=OPS,
        json={"revision": ctx["revision"], "goal_id": goal["goal_id"]},
    )
    assert response.status_code == 200, response.text
    run = response.json()["scenario"]
    assert run["request"]["goal_id"] == goal["goal_id"]
    for action in run["calculation_trace"]["actions"]:
        m = action["metrics"]
        assert m["after_ads"]["mean"] == pytest.approx(
            m["revenue"]["mean"] - m["selling_cost"]["mean"] - m["spend"]["mean"]
        )
    saved = api.get(f"/api/v1/scenarios/{run['scenario_id']}", headers=VIEWER).json()["data"]
    assert saved["calculation_trace"] == run["calculation_trace"]
    latest = api.get("/api/v1/campaign-analysis/CD002", headers=VIEWER).json()
    assert latest["revision"] == ctx["revision"]
    assert latest["goal"]["goal_id"] == goal["goal_id"]
    assert latest["history"][0]["scenario_id"] == run["scenario_id"]
