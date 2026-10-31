"""ORC-05 DoD on ``demo`` (300 SKUs): replaying 11.11 through the live path raises the
water bottle surge (~10:00) and the thermal shirt size M stock-out (~13:00), without spam.

Selected hours only (each tick recomputes ~200 SKUs; the full 24-hour replay is measured
in docs/quyet-dinh/M4-dieu-phoi.md)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.events import EventBus
from app.demo_import import OBSERVATIONS, REFERENCES
from app.ingest.pipeline import ingest_files
from app.orchestrator.runtime import Runtime
from app.warehouse import repo
from app.warehouse.db import Database
from tests.mockdata import require

pytestmark = pytest.mark.e2e

HOURS = [9, 10, 12, 13]


async def test_demo_replay_raises_the_two_story_alerts(tmp_path: Path) -> None:
    shop = require("demo")
    db = Database(tmp_path / "demo.duckdb")
    db.migrate()
    repo.seed_bundles(db.write)
    ingest_files(db.write, [shop / n for n in [*OBSERVATIONS, *REFERENCES]], build_panel=True)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        duckdb_path=db.path,
        data_dir=shop,
        live_dir=tmp_path / "live_stream",
        app_env="dev",
        live_enabled=False,
    )
    bus = EventBus()
    bus.start()
    rt = Runtime(db, bus, settings, compute=True, watch=False)
    rt.start()
    try:
        for h in HOURS:
            await rt.control.jump(h, wait=True)
        with db.reader() as c:
            rows = c.execute(
                "SELECT kind, status, target_id, group_key, dedupe_key,"
                " detail->>'first_data_hour' FROM alerts ORDER BY raised_at"
            ).fetchall()
    finally:
        await rt.stop()
        await bus.stop()
        db.close()
    bottle = [r for r in rows if r[3] == "BINHNUOC500"]
    assert [r[0] for r in bottle] == ["sales_above_forecast"], rows
    assert bottle[0][5] in ("09:59", "10:59"), bottle
    shirt_out = [r for r in rows if r[2] == "AOGIUNHIET-M" and str(r[4]).endswith(":out")]
    assert len(shirt_out) == 1 and shirt_out[0][5] in ("12:59", "13:59"), rows
    assert shirt_out[0][1] == "open"
    live = [r[4] for r in rows if r[1] != "resolved"]
    assert len(live) == len(set(live))  # one live alert per problem
    assert len(rows) <= 20, rows


@pytest.mark.perf
async def test_demo_tick_to_fresh_under_5_minutes(tmp_path: Path) -> None:
    """06 §6: ``LiveTick`` → last ``StageDone`` of the tick < 5 min (target < 60 s)."""
    shop = require("demo")
    db = Database(tmp_path / "demo.duckdb")
    db.migrate()
    repo.seed_bundles(db.write)
    ingest_files(db.write, [shop / n for n in [*OBSERVATIONS, *REFERENCES]], build_panel=True)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        duckdb_path=db.path,
        data_dir=shop,
        live_dir=tmp_path / "live_stream",
        app_env="dev",
        live_enabled=False,
    )
    bus = EventBus()
    bus.start()
    rt = Runtime(db, bus, settings, compute=True, watch=False)
    rt.start()
    try:
        await rt.control.jump(12, wait=True)
        await rt.control.jump(13, wait=True)
        with db.reader() as c:
            row = c.execute(
                "SELECT max(\"at\") FILTER (WHERE type = 'LiveTick'),"
                " max(\"at\") FILTER (WHERE type = 'StageDone') FROM event_log"
            ).fetchone()
    finally:
        await rt.stop()
        await bus.stop()
        db.close()
    assert row is not None and row[0] is not None and row[1] is not None
    assert (row[1] - row[0]).total_seconds() < 300
