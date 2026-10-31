"""ORC-01 / ORC-04 / API-07 units: ``sku_compute_state`` machine + stale rule, ``event_log``
sink with a failing handler, SSE hub (``Last-Event-ID``, limit), replay twin."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from app.api.common import compute_state
from app.core.events import (
    AlertRaised,
    ComputeFailed,
    DataUpdated,
    Event,
    EventBus,
    LiveTick,
    RecommendationChanged,
)
from app.live.replay import clear_live_stream, write_live_stream
from app.orchestrator import state
from app.orchestrator.event_log import EventLogSink, read_events
from app.orchestrator.stream import StreamHub
from app.warehouse.db import Database
from tests.mockdata import require

T0 = datetime(2026, 11, 11, 6, 0, tzinfo=UTC)  # 13:00 VN


@pytest.fixture
def conn(db: Database) -> duckdb.DuckDBPyConnection:
    db.migrate()
    return db.write


# ---------------------------------------------------------------------- ORC-04
def test_state_machine(conn: duckdb.DuckDBPyConnection) -> None:
    state.mark(conn, ["A", "B"], "queued")
    assert {k: v["state"] for k, v in state.get_states(conn).items()} == {
        "A": "queued",
        "B": "queued",
    }
    state.mark(conn, ["A", "B"], "computing")
    state.mark_fresh(conn, ["A"], data_as_of=T0, input_hash="h1")
    s = state.get_states(conn)
    assert s["A"]["state"] == "fresh" and s["A"]["data_as_of"] == T0
    assert s["B"]["state"] == "computing"
    # three failures in a row → failed; before that the SKU goes back to queued
    assert state.mark_failed(conn, ["B"], "boom") == []
    assert state.get_states(conn)["B"]["state"] == "queued"
    assert state.mark_failed(conn, ["B"], "boom") == []
    assert state.mark_failed(conn, ["B"], "boom") == ["B"]
    assert state.get_states(conn)["B"]["state"] == "failed"
    # a good run clears the counter
    state.mark_fresh(conn, ["B"], data_as_of=T0)
    assert state.get_states(conn)["B"] == {
        "state": "fresh",
        "computed_at": state.get_states(conn)["B"]["computed_at"],
        "data_as_of": T0,
        "error": None,
        "retries": 0,
    }
    # queued keeps the last good result's marks (UI shows it greyed)
    state.mark(conn, ["A"], "queued")
    assert state.get_states(conn)["A"]["data_as_of"] == T0


def test_stale_rule() -> None:
    h = timedelta(hours=1)
    assert compute_state(T0, T0) == "fresh"
    assert compute_state(T0, T0 + timedelta(minutes=20)) == "fresh"  # < 30 min
    assert compute_state(T0, T0 + h) == "stale"
    assert compute_state(None, T0) is None
    # stored queued/computing/failed always win
    for st in ("queued", "computing", "failed"):
        assert compute_state(T0, T0, st) == st
    # the orchestrator marked the SKU fresh on the new data (result itself older)
    assert compute_state(T0, T0 + h, "fresh", T0 + h) == "fresh"
    assert compute_state(T0, T0 + 2 * h, "fresh", T0 + h) == "stale"


# ---------------------------------------------------------------------- ORC-01 bus
async def test_event_log_and_failing_handler(db: Database) -> None:
    db.migrate()
    bus = EventBus()
    sink = EventLogSink(db.reader)
    bus.subscribe("*", sink)
    seen: list[str] = []

    async def boom(_ev: Event) -> None:
        raise RuntimeError("handler lỗi")

    async def ok(ev: Event) -> None:
        seen.append(ev.type)

    bus.subscribe(DataUpdated, boom)
    bus.subscribe(DataUpdated, ok)
    bus.start()
    await bus.publish(DataUpdated(batch_id="b1", sources=["orders"]))
    await bus.publish(DataUpdated(batch_id="b2", sources=["orders"]))
    await asyncio.wait_for(bus.join(), 10)
    assert bus.running  # the failing handler did not stop the bus
    assert seen == ["DataUpdated", "DataUpdated"]
    with db.reader() as cur:
        rows = read_events(cur)
    types = [r["type"] for r in rows]
    assert types.count("DataUpdated") == 2 and types.count("ComputeFailed") == 2
    failed = next(r for r in rows if r["type"] == "ComputeFailed")
    assert "handler lỗi" in failed["payload"]["error"]
    await bus.stop()


# ---------------------------------------------------------------------- API-07 hub
async def test_stream_hub_last_event_id_and_limit() -> None:
    hub = StreamHub()
    hub.bind_loop()
    tick = LiveTick(
        day=date(2026, 11, 11),
        orders_inventory_until=datetime(2026, 11, 11, 7, 59, tzinfo=UTC),
        ads_until=datetime(2026, 11, 11, 5, 59, tzinfo=UTC),
    )
    await hub.on_event(tick)
    await hub.on_event(RecommendationChanged(campaign_id="CD002", old=None, new={"x": 1}))
    await hub.on_event(ComputeFailed(target="sku:A", stage="forecast", error="e"))
    await hub.on_event(AlertRaised(alert_id="al-1"))  # no DB: id only
    assert hub.last_id == 4
    names = [e.event for e in hub.since(0)]
    assert names == ["live.tick", "recommendation.changed", "compute.failed", "alert.raised"]
    assert hub.since(0)[0].data["orders_until"] == "14:59"  # VN time
    assert hub.since(0)[0].data["ads_until"] == "12:59"

    # reconnect after id 2: gets 3 and 4, then a live one, then stops (limit)
    chunks: list[str] = []

    async def read() -> None:
        async for c in hub.subscribe(2, limit=3, heartbeat_s=0.05):
            chunks.append(c)

    task = asyncio.create_task(read())
    for _ in range(50):
        if hub.subscribers:
            break
        await asyncio.sleep(0.01)
    hub.push("heartbeat-test", {"n": 5})
    await asyncio.wait_for(task, 5)
    body = "".join(chunks)
    ids = [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]
    assert ids == [3, 4, 5]
    assert "event: compute.failed" in body and "event: heartbeat-test" in body
    data = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert data[-1] == {"n": 5}
    assert hub.subscribers == 0


async def test_stream_hub_idle_timeout_sends_heartbeat() -> None:
    hub = StreamHub()
    chunks = [c async for c in hub.subscribe(None, heartbeat_s=0.05, idle_timeout=0.2)]
    assert any(c.startswith("event: heartbeat") for c in chunks)


# ---------------------------------------------------------------------- LIVE-01 twin
def test_replay_twin_matches_eval_twin(tmp_path: Path) -> None:
    from eval.live_replay import write_live_stream as eval_write

    shop = require("nho")
    a, b = tmp_path / "a", tmp_path / "b"
    sa = write_live_stream(shop / "live_11_11" / "_replay.json", a, 5)
    sb = eval_write("nho", b, 5)
    assert sa["don_hang_va_ton_den_het"] == sb["don_hang_va_ton_den_het"] == "05:59"
    assert sa["ads_den_het"] == sb["ads_den_het"] == "03:59"
    for p in a.glob("*.csv"):
        assert p.read_bytes() == (b / p.name).read_bytes(), p.name
    assert clear_live_stream(a) == 4 and not list(a.iterdir())
    with pytest.raises(ValueError):
        write_live_stream(shop / "live_11_11" / "_replay.json", a, 24)
