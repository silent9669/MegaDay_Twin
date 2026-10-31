"""LIVE-01 + ORC-01/04/05 on ``nho``: the 11.11 replay goes through the real watcher,
orchestrator (forecast, drift, simulate) and AlertEvaluator, as in the FastAPI process.

DoD (12): the thermal shirt size M out of stock (~13:00) and the water bottle surge
(~10:00) raise their alert, nothing is spammed, and a replay restarted from hour 0 is
handled."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.core.events import EventBus
from app.demo_import import OBSERVATIONS, REFERENCES
from app.economics.goals import create_goal
from app.ingest.pipeline import ingest_files
from app.orchestrator import state
from app.orchestrator.runtime import Runtime
from app.warehouse import repo
from app.warehouse.db import Database
from tests.mockdata import require

pytestmark = pytest.mark.integration

HOURS = [0, 8, 9, 10, 12, 13]


@pytest.fixture(scope="module")
def history(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, Path]]:
    shop = require("nho")
    root = tmp_path_factory.mktemp("live_orc")
    db = Database(root / "live.duckdb")
    db.migrate()
    repo.seed_bundles(db.write)
    rep = ingest_files(db.write, [shop / n for n in [*OBSERVATIONS, *REFERENCES]], build_panel=True)
    assert not [f for f in rep.files if f.status == "failed"]
    create_goal(
        db.write,
        scope_id="CD005",
        kind="profit",
        deadline=datetime.fromisoformat("2026-11-12T00:00:00+07:00"),
        confirmed_by="u-lead-1",
    )
    db.close()
    yield root, shop


def _alerts(db: Database) -> list[dict[str, Any]]:
    with db.reader() as c:
        rows = c.execute(
            "SELECT kind, status, target_id, group_key, dedupe_key, detail->>'first_data_hour',"
            " title FROM alerts ORDER BY raised_at, id"
        ).fetchall()
    keys = ["kind", "status", "target", "group", "key", "first", "title"]
    return [dict(zip(keys, r, strict=True)) for r in rows]


async def test_replay_alerts_compute_state_and_restart(history: tuple[Path, Path]) -> None:
    root, shop = history
    db = Database(root / "live.duckdb")
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        duckdb_path=db.path,
        data_dir=shop,
        live_dir=root / "live_stream",
        app_env="dev",
        live_enabled=False,
        mc_samples=300,
    )
    bus = EventBus()
    bus.start()
    rt = Runtime(db, bus, settings, compute=True, watch=False)
    rt.start()
    try:
        for h in HOURS:
            await rt.control.jump(h, wait=True)
            assert rt.watcher.last_tick is not None
        alerts = _alerts(db)

        # ---- water bottle surge: one product alert, first raised on data up to 09:59/10:59
        bottle = [a for a in alerts if a["group"] == "BINHNUOC500"]
        assert [a["kind"] for a in bottle] == ["sales_above_forecast"], bottle
        assert bottle[0]["first"] in ("09:59", "10:59"), bottle
        # ---- shirt size M: out of stock on data up to 13:59 (stock hit 0 during 13:00)
        shirt = [
            a for a in alerts if a["target"] == "AOGIUNHIET-M" and a["kind"] == "stockout_risk"
        ]
        out = [a for a in shirt if a["key"].endswith(":out")]
        assert len(out) == 1 and out[0]["first"] in ("12:59", "13:59"), shirt
        assert out[0]["status"] == "open" and "hết hàng" in out[0]["title"]
        # ---- no spam: one live alert per key, few alerts for 30 SKUs over 14 hours
        live = [a["key"] for a in alerts if a["status"] != "resolved"]
        assert len(live) == len(set(live))
        assert len(alerts) <= 12, alerts
        with db.reader() as c:
            n_open = c.execute("SELECT count(*) FROM alerts WHERE status = 'open'").fetchone()
        assert n_open is not None and n_open[0] <= 8

        # ---- compute state: campaign SKUs fresh on the last data mark
        with db.reader() as c:
            states = state.get_states(c)
            as_of = c.execute(
                "SELECT max(as_of) FROM ingest_batches WHERE source_type = 'orders'"
            ).fetchone()
            types = dict(c.execute("SELECT type, count(*) FROM event_log GROUP BY 1").fetchall())
            rec = c.execute(
                "SELECT count(*) FROM recommendations WHERE campaign_id = 'CD005'"
            ).fetchone()
        assert as_of is not None
        assert states["AOGIUNHIET-M"]["state"] == "fresh"
        assert states["AOGIUNHIET-M"]["data_as_of"] == as_of[0]
        assert types["LiveTick"] == len(HOURS) and types["DataUpdated"] == len(HOURS)
        assert types["DriftDetected"] >= 3 and types["StageDone"] >= len(HOURS)
        assert rec is not None and rec[0] >= len(HOURS)  # CD005 has a goal: simulated each tick

        # ---- replay restarted from the beginning: old alerts resolved, data reloaded
        await rt.control.jump(3, wait=True)
        assert rt.watcher.restarts == 1
        after = _alerts(db)
        assert all(a["status"] == "resolved" for a in after if a["group"] == "BINHNUOC500")
        assert not [a for a in after if a["status"] != "resolved" and a["target"] == "AOGIUNHIET-M"]
        with db.reader() as c:
            mark = c.execute(
                "SELECT max(as_of) FROM ingest_batches WHERE source_type = 'orders'"
            ).fetchone()
        assert mark is not None and mark[0].isoformat() == "2026-11-10T21:00:00+00:00"  # 04:00
        # same mark again: nothing new to load
        rep = await rt.watcher.poll()
        assert rep.status == "unchanged"
    finally:
        await rt.stop()
        await bus.stop()
        db.close()
