"""Replay upload and ingestion through authenticated API contracts."""

import io
import zipfile

from app.live.archive import archive_summary
from app.orchestrator.runtime import Runtime
from tests.api.helpers import LEAD, VIEWER
from tests.mockdata import require


def test_upload_stages_then_jump_ingests_only_one_hour(api, tmp_path):
    # Private stream: never write the repository's demo/live_stream during tests.
    rt = api.app.state.runtime
    rt.watcher.root = tmp_path / "stream"
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(require("nho") / "live_11_11/_replay.json", "live_11_11/_replay.json")
    preview = api.post(
        "/api/v1/sale-plans/preview",
        headers=LEAD,
        json={
            "campaign_id": "CD002",
            "expected_goal": None,
            "goal": {"kind": "profit", "deadline": "2026-11-11T04:00:00+07:00"},
            "start": "2026-11-11T00:00:00+07:00",
            "horizon_hours": 4,
            "samples": 50,
        },
    )
    assert preview.status_code == 200, preview.text
    applied = api.post(
        f"/api/v1/sale-plans/{preview.json()['data']['preview_id']}/apply",
        headers=LEAD,
        json={"confirmed": True, "expected_goal": None},
    )
    assert applied.status_code == 200, applied.text
    before = api.get("/api/v1/campaign-analysis/CD002", headers=VIEWER).json()
    old_inputs = {"revision": before["revision"], "goal_id": before["goal"]["goal_id"]}
    calculated = api.post("/api/v1/campaign-analysis/CD002/refresh", headers=LEAD, json=old_inputs)
    assert calculated.status_code == 200, calculated.text
    old_run = calculated.json()["scenario"]
    assert (
        api.post("/api/v1/live/replay-archive", headers=VIEWER, content=raw.getvalue()).status_code
        == 403
    )
    response = api.post("/api/v1/live/replay-archive", headers=LEAD, content=raw.getvalue())
    assert response.status_code == 200, response.text
    assert response.json()["uploaded"]
    assert "rows" not in response.json()
    assert archive_summary(rt.control.source)["sha256"] == response.json()["sha256"]
    staged = api.get("/api/v1/campaign-analysis/CD002", headers=VIEWER).json()
    assert staged["revision"] == before["revision"]
    assert staged["dataAsOf"] == before["dataAsOf"]
    assert staged["goal"] == before["goal"]
    response = api.post("/api/v1/live/control", headers=LEAD, json={"command": "jump", "hour": 0})
    assert response.status_code == 200, response.text
    after = api.get("/api/v1/campaign-analysis/CD002", headers=VIEWER).json()
    assert after["dataAsOf"] == "2026-11-11T01:00:00+07:00"
    assert after["revision"] != before["revision"]
    assert after["goal"] == before["goal"]
    assert response.json()["data"]["ads_until"] is None
    assert (
        api.post(
            "/api/v1/campaign-analysis/CD002/refresh", headers=LEAD, json=old_inputs
        ).status_code
        == 409
    )
    refreshed = api.post(
        "/api/v1/campaign-analysis/CD002/refresh",
        headers=LEAD,
        json={"revision": after["revision"], "goal_id": after["goal"]["goal_id"]},
    )
    assert refreshed.status_code == 200, refreshed.text
    new_run = refreshed.json()["scenario"]
    assert new_run["scenario_id"] != old_run["scenario_id"]
    assert new_run["request"]["horizon_hours"] == 3
    assert new_run["request"]["goal_id"] == old_run["request"]["goal_id"]
    saved = api.get(f"/api/v1/scenarios/{old_run['scenario_id']}", headers=VIEWER).json()["data"]
    assert saved["calculation_trace"] == old_run["calculation_trace"]
    restored = Runtime(rt.db, rt.bus, rt.settings, compute=False, watch=False)
    assert restored.control.source == rt.control.source
    assert restored.control.hour == 0
    assert not restored.control.running
    with rt.db.reader() as conn:
        assert conn.execute(
            "SELECT max(ordered_at) < TIMESTAMPTZ '2026-11-11 01:00:00+07:00' FROM orders"
        ).fetchone()[0]
    rt.settings.demo_mode = False
    try:
        assert (
            api.post(
                "/api/v1/live/replay-archive", headers=LEAD, content=raw.getvalue()
            ).status_code
            == 409
        )
    finally:
        rt.settings.demo_mode = True
