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
    before = api.get("/api/v1/campaign-analysis/CD002", headers=VIEWER).json()
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
    assert response.json()["data"]["ads_until"] is None
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
