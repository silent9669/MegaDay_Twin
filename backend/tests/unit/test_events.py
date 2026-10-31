from __future__ import annotations

import asyncio
import time
from datetime import UTC, date, datetime

import pytest

from app.core.events import (
    AUX_EVENTS,
    CORE_EVENTS,
    EVENT_TYPES,
    ComputeFailed,
    DataUpdated,
    DecisionRecorded,
    Event,
    EventBus,
    LiveTick,
    RecomputeRequested,
)


def test_all_plan_events_defined() -> None:
    core = {e.__name__ for e in CORE_EVENTS}
    aux = {e.__name__ for e in AUX_EVENTS}
    assert core == {
        "DataUpdated",
        "RecomputeRequested",
        "ForecastUpdated",
        "DriftDetected",
        "RecommendationChanged",
        "AlertRaised",
        "DecisionRecorded",
    }
    assert aux == {
        "GoalChanged",
        "ParameterChanged",
        "StageDone",
        "ComputeFailed",
        "AlertResolved",
        "LiveTick",
    }
    assert core | aux <= set(EVENT_TYPES)


def test_event_payload_and_log_row() -> None:
    ev = DataUpdated(batch_id="b1", sources=["orders"], skus_changed=["NOICHIEN2L"])
    assert ev.type == "DataUpdated"
    assert ev.id.startswith("evt-")
    assert ev.at.tzinfo is not None
    assert ev.payload == {
        "batch_id": "b1",
        "sources": ["orders"],
        "as_of": None,
        "skus_changed": ["NOICHIEN2L"],
    }
    row = ev.to_log_row()
    assert set(row) == {"id", "type", "payload", "at"}


def test_event_is_frozen() -> None:
    ev = DecisionRecorded(decision_id="dec-1")
    with pytest.raises(ValueError):
        ev.decision_id = "x"  # type: ignore[misc]


def test_live_tick_payload_json() -> None:
    ev = LiveTick(
        day=date(2026, 11, 11),
        orders_inventory_until=datetime(2026, 11, 11, 7, 59, tzinfo=UTC),
        ads_until=None,
    )
    assert ev.payload["day"] == "2026-11-11"


def test_subscribe_unknown_type_rejected() -> None:
    bus = EventBus()

    async def h(_: Event) -> None:
        return None

    with pytest.raises(ValueError):
        bus.subscribe("NoSuchEvent", h)


async def test_publish_delivers_under_100ms() -> None:
    # 01 "Tiêu chí hoàn thành": DataUpdated → listener receives in < 100 ms
    bus = EventBus()
    got: asyncio.Future[float] = asyncio.get_running_loop().create_future()

    async def orchestrator(ev: Event) -> None:
        assert isinstance(ev, DataUpdated)
        got.set_result(time.perf_counter())

    bus.subscribe(DataUpdated, orchestrator)
    bus.start()
    t0 = time.perf_counter()
    await bus.publish(DataUpdated(batch_id="b", sources=["orders"]))
    t1 = await asyncio.wait_for(got, timeout=1.0)
    await bus.stop()
    assert (t1 - t0) < 0.1


async def test_wildcard_and_typed_handlers_and_order() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def typed(ev: Event) -> None:
        seen.append(f"typed:{ev.type}")

    async def everything(ev: Event) -> None:
        seen.append(f"all:{ev.type}")

    bus.subscribe("RecomputeRequested", typed)
    bus.subscribe("*", everything)
    bus.start()
    await bus.publish(RecomputeRequested(sku_ids=["A"], reason="test"))
    await bus.publish(DecisionRecorded(decision_id="dec-1"))
    await bus.join()
    await bus.stop()
    assert seen == [
        "typed:RecomputeRequested",
        "all:RecomputeRequested",
        "all:DecisionRecorded",
    ]


async def test_failing_handler_does_not_kill_bus_and_emits_compute_failed() -> None:
    bus = EventBus()
    failures: list[ComputeFailed] = []
    delivered: list[str] = []

    async def boom(_: Event) -> None:
        raise RuntimeError("kaboom")

    async def ok(ev: Event) -> None:
        delivered.append(ev.id)

    async def on_failed(ev: Event) -> None:
        assert isinstance(ev, ComputeFailed)
        failures.append(ev)

    bus.subscribe(DataUpdated, boom)
    bus.subscribe(DataUpdated, ok)
    bus.subscribe(ComputeFailed, on_failed)
    bus.start()
    e1 = DataUpdated(batch_id="1", sources=["ads"])
    e2 = DataUpdated(batch_id="2", sources=["ads"])
    await bus.publish(e1)
    await bus.publish(e2)
    await bus.join()
    assert bus.running
    await bus.stop()
    assert delivered == [e1.id, e2.id]  # second handler still ran, bus kept going
    assert len(failures) == 2
    assert "kaboom" in failures[0].error
    assert failures[0].stage == "handle:DataUpdated"


async def test_failing_compute_failed_handler_does_not_loop() -> None:
    bus = EventBus()
    calls = 0

    async def bad(_: Event) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("again")

    bus.subscribe(ComputeFailed, bad)
    bus.start()
    await bus.publish(ComputeFailed(target="x", stage="s", error="e"))
    await bus.join()
    await bus.stop()
    assert calls == 1
    assert bus.pending == 0


async def test_unsubscribe() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def h(ev: Event) -> None:
        seen.append(ev.type)

    bus.subscribe(DecisionRecorded, h)
    bus.unsubscribe(DecisionRecorded, h)
    bus.start()
    await bus.publish(DecisionRecorded(decision_id="d"))
    await bus.join()
    await bus.stop()
    assert seen == []
