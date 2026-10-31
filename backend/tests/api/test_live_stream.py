"""API-07 (live part): ``GET /live/status``, ``POST /live/control``, ``GET /stream``; roles
GOV-02 (viewer reads, lead controls, DEMO_MODE only)."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.warehouse.db import Database
from tests.api.helpers import LEAD, OPS, VIEWER, err, ok
from tests.mockdata import require


def _client(src: Path, tmp: Path, *, demo_mode: bool = True) -> tuple[TestClient, Database]:
    dest = tmp / "live_api.duckdb"
    shutil.copy(src, dest)
    db = Database(dest)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        duckdb_path=dest,
        app_env="test",
        data_dir=require("nho"),
        live_dir=tmp / "live_stream",
        demo_mode=demo_mode,
    )
    return TestClient(create_app(settings=settings, db=db)), db


@pytest.fixture(scope="module")
def live_api(nho_db_file: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    client, db = _client(nho_db_file, tmp_path_factory.mktemp("live_api"))
    with client as c:
        yield c
    db.close()


def test_status_needs_user_and_reports_idle(live_api: TestClient) -> None:
    err(live_api.get("/api/v1/live/status"), 401, "UNAUTHENTICATED")
    body = ok(live_api.get("/api/v1/live/status", headers=VIEWER))
    d = body["data"]
    assert d["replay_running"] is False and d["orders_until"] is None
    assert d["live_dir"].endswith("live_stream")


def test_control_roles(live_api: TestClient) -> None:
    for h in (VIEWER, OPS):
        r = live_api.post("/api/v1/live/control", json={"command": "stop"}, headers=h)
        err(r, 403, "FORBIDDEN")
    err(
        live_api.post("/api/v1/live/control", json={"command": "jump"}, headers=LEAD),
        400,
        "VALIDATION_ERROR",
    )


def test_jump_loads_live_data_then_reset(live_api: TestClient) -> None:
    body = ok(
        live_api.post("/api/v1/live/control", json={"command": "jump", "hour": 9}, headers=LEAD)
    )
    d = body["data"]
    assert d["orders_until"] == "09:59" and d["ads_until"] == "07:59"
    assert d["day"] == "2026-11-11" and d["last_tick_at"].endswith("+07:00")
    assert body["meta"]["data_as_of"].startswith("2026-11-11T10:00")
    # the SSE ring has the tick (Last-Event-ID 0 = everything still buffered)
    r = live_api.get("/api/v1/stream?limit=2", headers={**VIEWER, "Last-Event-ID": "0"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [ln[7:] for ln in r.text.splitlines() if ln.startswith("event: ")]
    assert events == ["ingest.batch_loaded", "live.tick"], r.text
    data = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
    tick = next(d for d in data if "orders_until" in d)
    assert tick["orders_until"] == "09:59"
    # reconnect after the last id: nothing missed
    last = max(int(ln[4:]) for ln in r.text.splitlines() if ln.startswith("id: "))
    r2 = live_api.get(f"/api/v1/stream?idle_timeout=0.2&last_event_id={last}", headers=VIEWER)
    assert "id: " not in r2.text
    # reset: back to the history only
    body = ok(live_api.post("/api/v1/live/control", json={"command": "reset"}, headers=LEAD))
    assert body["meta"]["data_as_of"].startswith("2026-11-11T00:00")
    with live_api.app.state.db.reader() as c:  # type: ignore[attr-defined]
        audit = c.execute("SELECT count(*) FROM audit_log WHERE action LIKE 'live.%'").fetchone()
        logged = c.execute("SELECT count(*) FROM event_log WHERE type = 'LiveTick'").fetchone()
    assert audit is not None and audit[0] >= 2
    assert logged is not None and logged[0] >= 1


def test_stream_requires_viewer(live_api: TestClient) -> None:
    err(live_api.get("/api/v1/stream?limit=1"), 401, "UNAUTHENTICATED")


def test_control_only_in_demo_mode(nho_db_file: Path, tmp_path: Path) -> None:
    client, db = _client(nho_db_file, tmp_path, demo_mode=False)
    with client as c:
        r = c.post("/api/v1/live/control", json={"command": "stop"}, headers=LEAD)
        err(r, 409, "STATE_CONFLICT")
    db.close()
