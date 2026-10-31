"""Small M3 API fixes (docs/quyet-dinh/M3-api.md "Sửa lỗi nhỏ M3", M3-web §13 lỗi 1–6) on
the ``nho`` copy: what-if runs never replace the active recommendation, the echoed request
re-sends as is, Vietnamese times everywhere, ``meta.total`` on ``/skus``, goal label with
deadline, ``duplicate_of`` in OpenAPI."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.helpers import LEAD, OPS, VIEWER, err, ok

V1 = "/api/v1"
DEADLINE = "2026-11-12T00:00:00+07:00"
# an ISO time in UTC (``Z`` or ``+00:00``) leaking into a response
UTC_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?(Z|\+00:00)")


@pytest.fixture(scope="module")
def goal(api: TestClient) -> str:
    body = {"scope_id": "CD002", "kind": "profit", "deadline": DEADLINE}
    return str(ok(api.post(f"{V1}/goals", json=body, headers=LEAD), 201)["data"]["goal_id"])


@pytest.fixture(scope="module")
def standard(api: TestClient, goal: str) -> dict[str, Any]:
    body = {"campaign_id": "CD002", "goal_id": goal, "samples": 300}
    return ok(api.post(f"{V1}/scenarios/run", json=body, headers=OPS))["data"]


def _active_ids(api: TestClient) -> list[str]:
    recs = ok(api.get(f"{V1}/recommendations", params={"campaign_id": "CD002"}, headers=VIEWER))
    return [r["recommendation_id"] for r in recs["data"]]


def _sku_rec(api: TestClient) -> str | None:
    rows = ok(api.get(f"{V1}/skus", params={"campaign_id": "CD002"}, headers=VIEWER))["data"]
    noi = next(r for r in rows if r["sku"] == "NOICHIEN2L")
    return noi["recommendation"]["recommendation_id"] if noi["recommendation"] else None


# ------------------------------------------------------------------ lỗi 2
def test_what_if_runs_keep_the_active_recommendation(
    api: TestClient, goal: str, standard: dict[str, Any]
) -> None:
    rid = standard["recommendation"]["recommendation_id"]
    assert standard["recommendation"]["active"] and not standard["recommendation"]["what_if"]
    assert _active_ids(api) == [rid]
    base = {"campaign_id": "CD002", "goal_id": goal, "samples": 300}
    for extra in (
        {"param_overrides": {"leftover_value_per_unit": 0}},  # V slider
        {"param_overrides": {"restock_delay_days": -1}},  # restock slider
        {"session_id": "ais-test"},  # AI session parameters
        {"actions": [{"kind": "continue"}, {"kind": "pause"}]},  # own action list
    ):
        wi = ok(api.post(f"{V1}/scenarios/run", json={**base, **extra}, headers=OPS))["data"]
        rec = wi["recommendation"]
        assert rec["what_if"] is True and rec["active"] is False, extra
        assert rec["superseded_by"] is None
        assert _active_ids(api) == [rid], extra  # still the standard run
        assert _sku_rec(api) == rid
        camp = ok(api.get(f"{V1}/campaigns/CD002", headers=VIEWER))["data"]
        assert camp["recommendation"]["recommendation_id"] == rid
        # the what-if run itself is still readable
        got = ok(api.get(f"{V1}/scenarios/{wi['scenario_id']}", headers=VIEWER))["data"]
        assert got["recommendation"]["what_if"] is True and not got["recommendation"]["active"]
        one = ok(api.get(f"{V1}/recommendations/{rec['recommendation_id']}", headers=VIEWER))
        assert one["data"]["what_if"] is True and one["data"]["active"] is False
    # a new standard run (other sample count) does replace it
    again = ok(
        api.post(f"{V1}/scenarios/run", json={**base, "samples": 400}, headers=OPS)
    )["data"]  # fmt: skip
    new = again["recommendation"]["recommendation_id"]
    assert again["recommendation"]["active"] and _active_ids(api) == [new]
    old = ok(api.get(f"{V1}/scenarios/{standard['scenario_id']}", headers=VIEWER))["data"]
    assert old["recommendation"]["superseded_by"] == new
    assert old["recommendation"]["active"] is False
    # re-sending the old standard request hits the cache but says it is no longer active
    hit = ok(api.post(f"{V1}/scenarios/run", json=standard["request"], headers=OPS))
    assert hit["meta"]["cache_hit"] is True
    assert hit["data"]["recommendation"]["active"] is False
    assert _active_ids(api) == [new]


# ------------------------------------------------------------------ lỗi 1
def test_echoed_request_resends_as_run(
    api: TestClient, goal: str, standard: dict[str, Any]
) -> None:
    from app.api import scenarios

    base = {"campaign_id": "CD002", "goal_id": goal, "samples": 300}
    n_before = len(
        ok(api.get(f"{V1}/recommendations", params={"active": False}, headers=VIEWER))["data"]
    )
    big = {**base, "param_overrides": {"leftover_value_per_unit": 9_000_000}}
    err(api.post(f"{V1}/scenarios/run", json=big, headers=OPS), 400, "PARAM_OUT_OF_RANGE")
    n_after = len(
        ok(api.get(f"{V1}/recommendations", params={"active": False}, headers=VIEWER))["data"]
    )
    assert n_after == n_before  # rejected before anything is simulated or stored
    local_start = standard["request"]["start"]
    assert local_start.endswith("+07:00")
    utc_start = datetime.fromisoformat(local_start).astimezone(UTC).isoformat()
    wi = ok(
        api.post(
            f"{V1}/scenarios/run",
            json={**base, "start": utc_start.replace("+00:00", "Z"),
                  "param_overrides": {"leftover_value_per_unit": 20_000}},
            headers=OPS,
        )
    )["data"]  # fmt: skip
    for run in (standard, wi):
        scenarios._RUNS.clear()  # force the DB read-back
        got = ok(api.get(f"{V1}/scenarios/{run['scenario_id']}", headers=VIEWER))["data"]
        req = got["request"]
        assert req == run["request"]
        assert req["actions"] is None  # default actions, not the list that was simulated
        assert req["start"].endswith("+07:00")
        again = ok(api.post(f"{V1}/scenarios/run", json=req, headers=OPS))
        assert again["meta"]["cache_hit"] is True
        assert again["data"]["scenario_id"] == run["scenario_id"]
    assert wi["request"]["param_overrides"] == {"leftover_value_per_unit": 20_000.0}
    # same instant with another offset: same cache entry (simulator normalises to UTC)
    local = {**wi["request"], "start": local_start}
    assert wi["request"]["start"] == local_start
    hit = ok(api.post(f"{V1}/scenarios/run", json=local, headers=OPS))
    assert hit["meta"]["cache_hit"] is True and hit["data"]["scenario_id"] == wi["scenario_id"]


# ------------------------------------------------------------------ lỗi 3, 4
def test_no_utc_times_in_responses(api: TestClient, standard: dict[str, Any]) -> None:
    from app.api import scenarios

    sid = standard["scenario_id"]
    conn = api.app.state.db.write  # type: ignore[union-attr]
    query = "SELECT trace FROM scenario_calculation_traces WHERE scenario_id=?"
    stored = conn.execute(query, [sid]).fetchone()[0]
    scenarios._RUNS.clear()  # Also project UTC evidence from durable, older snapshots.
    rid = standard["recommendation"]["recommendation_id"]
    paths = [
        "/campaigns/CD002",
        "/campaigns",
        "/skus?campaign_id=CD002",
        "/skus/NOICHIEN2L",
        f"/scenarios/{sid}",
        f"/recommendations/{rid}",
        "/recommendations?active=false",
        "/goals?scope_id=CD002",
        "/freshness",
    ]
    for p in paths:
        text = api.get(f"{V1}{p}", headers=VIEWER).text
        assert not UTC_TIME.search(text), (p, UTC_TIME.search(text))
    camp = ok(api.get(f"{V1}/campaigns/CD002", headers=VIEWER))["data"]
    assert camp["recommendation"]["data_as_of"].endswith("+07:00")
    assert camp["recommendation"]["created_at"].endswith("+07:00")
    got = ok(api.get(f"{V1}/scenarios/{sid}", headers=VIEWER))["data"]
    original = json.loads(stored)["sources"]["forecasts"]
    displayed = got["calculation_trace"]["sources"]["forecasts"]
    for source, view in zip(original, displayed, strict=True):
        before = source["model_selection"]["forecast_origin"]
        after = view["model_selection"]["forecast_origin"]
        assert after.endswith("+07:00")
        assert datetime.fromisoformat(before) == datetime.fromisoformat(after)
    assert conn.execute(query, [sid]).fetchone()[0] == stored


def test_violation_details_in_vietnam_time(standard: dict[str, Any]) -> None:
    details = [d for r in standard["results"] for d in r["violation_details"]]
    for d in details:
        assert "+00:00" not in d and "T" not in d.split("từ")[-1], d
        assert re.search(r"từ \d\d:\d\d \d\d/\d\d\)", d), d


# ------------------------------------------------------------------ lỗi 5
def test_skus_total_and_goal_label(api: TestClient, standard: dict[str, Any]) -> None:
    first = ok(api.get(f"{V1}/skus", params={"limit": 5}, headers=VIEWER))
    total = first["meta"]["total"]
    rows, cursor = len(first["data"]), first["next_cursor"]
    while cursor:
        page = ok(api.get(f"{V1}/skus", params={"limit": 50, "cursor": cursor}, headers=VIEWER))
        assert page["meta"]["total"] == total
        rows, cursor = rows + len(page["data"]), page["next_cursor"]
    assert total == rows > 5
    some = ok(api.get(f"{V1}/skus", params={"campaign_id": "CD002"}, headers=VIEWER))
    assert some["meta"]["total"] == len(some["data"]) == 3
    camp = ok(api.get(f"{V1}/campaigns/CD002", headers=VIEWER))["data"]
    goals = ok(api.get(f"{V1}/goals", params={"scope_id": "CD002"}, headers=VIEWER))["data"]
    active = next(g for g in goals if g["active"])
    assert camp["goal"]["label"] == active["label"] == "Lợi nhuận tới 00:00 12/11"


# ------------------------------------------------------------------ lỗi 6
def test_openapi_has_duplicate_of_and_what_if(api: TestClient) -> None:
    spec = api.get(f"{V1}/openapi.json").json()
    if "components" not in spec:  # served at /openapi.json in some set-ups
        spec = api.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    assert "duplicate_of" in schemas["ScenarioResultOut"]["properties"]
    assert "what_if" in schemas["RecommendationOut"]["properties"]
    assert "total" in schemas["Meta"]["properties"]
    assert "data_as_of" in schemas["CampaignRecRef"]["properties"]
