"""``POST /scenarios/run`` on ``demo`` at 11/11 01:00 (history + live stream up to 00:59,
same set-up as ``tests/e2e/test_noichien_1111.py``): CD002 → ``schedule_hours``, ``pause``
blocked by ``campaign_must_run``. Timing of a run once forecasts exist (05 §5.5: < 1 s)
is printed here and asserted only in the ``perf`` test."""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.ingest.pipeline import ingest_live_delta
from app.panel.build import build_hourly_panel
from app.simulator import engine
from eval.live_replay import write_live_stream
from tests.api.helpers import LEAD, OPS, VIEWER, client_for, err, ok
from tests.mockdata import load_mock, require

pytestmark = pytest.mark.e2e

V1 = "/api/v1"
START = "2026-11-11T01:00:00+07:00"
END = "2026-11-12T00:00:00+07:00"


def _show(text: str) -> None:
    try:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.flush()
    except (AttributeError, ValueError):
        print(text.encode("ascii", "backslashreplace").decode())


@pytest.fixture(scope="module")
def demo_api(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    require("demo")
    root = tmp_path_factory.mktemp("api_demo_0100")
    path = root / "demo.duckdb"
    conn = duckdb.connect(str(path))
    load_mock(conn, "demo", build_panel=False)
    write_live_stream("demo", root / "live_stream", 0)  # orders + stock up to 00:59
    assert ingest_live_delta(conn, root / "live_stream", build_panel=False).status == "loaded"
    build_hourly_panel(conn)
    from app.ads_effect.iroas import estimate_all, write_estimates

    write_estimates(conn, estimate_all(conn).estimates)
    conn.close()
    engine.cache_clear()
    client, db = client_for(path)
    with client as c:
        yield c
    db.close()


@pytest.fixture(scope="module")
def goal_id(demo_api: TestClient) -> str:
    body = {"scope_id": "CD002", "kind": "profit", "deadline": END, "reason": "Lãi 11.11"}
    return str(ok(demo_api.post(f"{V1}/goals", json=body, headers=LEAD), 201)["data"]["goal_id"])


@pytest.fixture(scope="module")
def run_1000(demo_api: TestClient, goal_id: str) -> dict[str, Any]:
    body = {"campaign_id": "CD002", "goal_id": goal_id, "start": START, "samples": 1000}
    t0 = time.perf_counter()
    out = ok(demo_api.post(f"{V1}/scenarios/run", json=body, headers=OPS))
    _show(f"\nAPI CD002 01:00 first run (forecasts computed): {time.perf_counter() - t0:.2f} s")
    return out


def test_cd002_schedule_hours_wins(run_1000: dict[str, Any]) -> None:
    d = run_1000["data"]
    rec = d["recommendation"]
    assert rec["best"]["kind"] == "schedule_hours"
    assert rec["best"]["windows_local"] == [[0, 2], [12, 14]]
    assert rec["status"] == "recommend"
    by = {r["key"]: r for r in d["results"]}
    assert by["pause"]["feasible"] is False
    assert by["pause"]["violations"] == ["campaign_must_run"]
    # VN time, not ISO UTC (sửa lỗi nhỏ M3, lỗi 3): first committed hour left is 01:00 11/11
    assert by["pause"]["violation_details"] == [
        "Tắt chiến dịch trong khung flash sale đã cam kết với sàn (3 giờ, từ 01:00 11/11)"
    ]
    assert by["pause_until_restock"]["feasible"] is False
    assert any("cam kết với sàn" in r for r in rec["reasons"])
    kinds = [s["kind"] if s else None for s in rec["reason_sources"]]
    assert "commitment" in kinds and "forecast" in kinds
    assert d["request"]["start"] == START and d["request"]["horizon_hours"] == 23
    assert run_1000["meta"]["data_as_of"] == START  # data up to 00:59
    assert rec["inputs_ref"]["iroas_source"] == "estimate"


def test_cd002_slider_runs_are_fast(
    demo_api: TestClient, goal_id: str, run_1000: dict[str, Any]
) -> None:
    """Drag (300 samples) then release (1000) with a what-if override: forecasts exist."""
    base = {"campaign_id": "CD002", "goal_id": goal_id, "start": START}
    times = []
    for samples, delay in ((300, -1), (1000, -1), (300, 2)):
        body = {**base, "samples": samples, "param_overrides": {"restock_delay_days": delay}}
        t0 = time.perf_counter()
        out = ok(demo_api.post(f"{V1}/scenarios/run", json=body, headers=OPS))
        times.append(time.perf_counter() - t0)
        assert out["meta"]["cache_hit"] is False
        assert out["data"]["recommendation"]["best"]["kind"] == "schedule_hours"
    _show("API /scenarios/run with forecasts: " + ", ".join(f"{t * 1e3:.0f} ms" for t in times))
    assert run_1000["data"]["scenario_id"]


def test_cd002_disable_sku_409(demo_api: TestClient, goal_id: str) -> None:
    body = {"campaign_id": "CD002", "goal_id": goal_id, "disable_skus": ["NOICHIEN2L"]}
    err(demo_api.post(f"{V1}/scenarios/run", json=body, headers=OPS), 409, "CONSTRAINT_VIOLATION")


def test_cd002_recommendation_visible(demo_api: TestClient, run_1000: dict[str, Any]) -> None:
    skus = ok(demo_api.get(f"{V1}/skus", params={"campaign_id": "CD002"}, headers=VIEWER))
    noi = next(r for r in skus["data"] if r["sku"] == "NOICHIEN2L")
    assert noi["recommendation"]["best"]["kind"] == "schedule_hours"
    assert noi["stock_available"] is not None and noi["hours_of_cover"] is not None
    camp = ok(demo_api.get(f"{V1}/campaigns/CD002", headers=VIEWER))["data"]
    assert camp["goal"]["kind"] == "profit" and camp["price_max_vnd"] == 690_000


@pytest.mark.perf
def test_cd002_run_under_1s(demo_api: TestClient, goal_id: str, run_1000: dict[str, Any]) -> None:
    body = {"campaign_id": "CD002", "goal_id": goal_id, "start": START, "samples": 1000,
            "seed": 7}  # fmt: skip
    best = min(_timed(demo_api, {**body, "seed": 7 + i}) for i in range(3))
    _show(f"perf API /scenarios/run 1000 samples: {best * 1e3:.0f} ms")
    assert best < 1.0


def _timed(c: TestClient, body: dict[str, Any]) -> float:
    t0 = time.perf_counter()
    ok(c.post(f"{V1}/scenarios/run", json=body, headers=OPS))
    return time.perf_counter() - t0
