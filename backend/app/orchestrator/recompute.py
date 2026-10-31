"""Recompute every campaign touched by new data (ORC-01 floor, 06 §1–§3).

``DataUpdated`` / ``GoalChanged`` / ``ParameterChanged`` → :meth:`Orchestrator.request` (SKUs
→ ``queued``) → one background worker coalesces the pending SKUs and runs
:meth:`Orchestrator.recompute` in a thread:

1. campaigns touched = campaigns with a changed SKU (or a combo component of one); the SKUs
   recomputed are all SKUs of those campaigns (+ components), so a shared budget is
   simulated in one pass (06 §2);
2. **baseline of the day**: champion forecast at local midnight (one per SKU and day,
   persisted, reused by every tick);
3. **drift** (03 §9): ``scan_drift`` of the actual hours since midnight against the
   baseline → ``DriftDetected`` once per (day, SKU, direction); ``up`` SKUs get the
   ``nowcast`` model for the next 24 h;
4. **forecast** at the next hour (``start`` of the simulator) for every SKU, persisted
   (``ForecastUpdated``);
5. **simulate + recommend** each touched campaign that has an active goal
   (``run_simulation``, same code and cache as ``POST /scenarios/run``) →
   ``RecommendationChanged``; a campaign without a goal is skipped (no goal = nothing to
   optimise, 05 §4);
6. ``sku_compute_state`` → ``fresh`` (or ``failed`` after 3 errors, ``ComputeFailed``);
7. :class:`~app.orchestrator.alerts_eval.AlertEvaluator` on the new state.

Priority triage (ORC-02) and the stage cache (ORC-03) are P1: at the floor, a tick
recomputes every touched campaign; the simulator has its own result cache.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import duckdb

from app.core.events import (
    ComputeFailed,
    DataUpdated,
    Event,
    EventBus,
    ForecastUpdated,
    GoalChanged,
    ParameterChanged,
    RecommendationChanged,
    RecomputeRequested,
    StageDone,
)
from app.core.logging import get_logger
from app.core.timeutil import local_date, local_day_bounds_utc, utcnow
from app.forecast.base import ForecastResult
from app.forecast.drift import DriftAlarm, active_up
from app.orchestrator import state

log = get_logger(__name__)

CursorFactory = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]
Notify = Callable[[str, dict[str, Any]], object]
FORECAST_HORIZON = 72  # = simulator MAX_HORIZON: every goal deadline is covered
BASELINE_HORIZON = 24


@dataclass
class RecomputeResult:
    as_of: datetime | None
    skus: list[str] = field(default_factory=list)
    campaigns: list[str] = field(default_factory=list)
    simulated: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    drift: list[DriftAlarm] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    duration_ms: float = 0.0


def campaign_map(conn: duckdb.DuckDBPyConnection) -> dict[str, list[str]]:
    """``campaign_id → SKUs`` (+ combo components, they share the stock)."""
    comps: dict[str, list[str]] = {}
    for b, c in conn.execute(
        "SELECT bundle_sku_id, component_sku_id FROM bundle_components ORDER BY 1, 2"
    ).fetchall():
        comps.setdefault(str(b), []).append(str(c))
    out: dict[str, list[str]] = {}
    for cid, sku in conn.execute(
        "SELECT campaign_id, sku_id FROM campaign_skus ORDER BY 1, 2"
    ).fetchall():
        members = out.setdefault(str(cid), [])
        for s in (str(sku), *comps.get(str(sku), [])):
            if s not in members:
                members.append(s)
    return out


def skus_in_scope(
    conn: duckdb.DuckDBPyConnection, scope: str, ids: Sequence[str]
) -> set[str] | None:
    """SKUs behind a ``GoalChanged`` / ``ParameterChanged`` scope; ``None`` = every SKU."""
    if scope == "campaign":
        camps = campaign_map(conn)
        return {s for c in ids for s in camps.get(c, [])}
    if scope == "sku":
        return set(ids)
    return None


def day_start(as_of: datetime) -> datetime:
    """Local midnight (UTC) of the day holding the last data hour (``as_of`` is exclusive)."""
    return local_day_bounds_utc(local_date(as_of - timedelta(microseconds=1)))[0]


def _hash(*parts: Any) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]


class Orchestrator:
    """Bus handlers + one background worker. ``lock`` serialises every DuckDB writer of the
    live path (watcher, recompute, alerts) so they never conflict with each other."""

    def __init__(
        self,
        cursor: CursorFactory,
        bus: EventBus,
        *,
        lock: threading.Lock | None = None,
        samples: int = 1000,
        simulate: bool = True,
        evaluator: Any = None,
        notify: Notify | None = None,
    ) -> None:
        self._cursor = cursor
        self.bus = bus
        self.lock = lock or threading.Lock()
        self.samples = samples
        self.simulate = simulate
        self.evaluator = evaluator
        self._notify = notify
        self._pending: set[str] = set()
        self._pending_all = False
        self._reasons: list[str] = []
        self._wake: asyncio.Event | None = None
        self._idle: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._drift_seen: set[tuple[date, str, str]] = set()
        self._drift_up: dict[str, DriftAlarm] = {}
        self.runs = 0
        self.last: RecomputeResult | None = None

    # ------------------------------------------------------------------ wiring
    def attach(self) -> None:
        self.bus.subscribe(DataUpdated, self.on_data_updated)
        self.bus.subscribe(GoalChanged, self.on_scope_changed)
        self.bus.subscribe(ParameterChanged, self.on_scope_changed)
        self.bus.subscribe(RecomputeRequested, self.on_recompute_requested)

    def start(self) -> None:
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="orchestrator")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @property
    def busy(self) -> bool:
        return bool(self._pending or self._pending_all) or (
            self._idle is not None and not self._idle.is_set()
        )

    # ------------------------------------------------------------------ handlers
    async def on_data_updated(self, event: Event) -> None:
        assert isinstance(event, DataUpdated)
        self.request(event.skus_changed or None, reason=f"data:{event.batch_id}")

    async def on_scope_changed(self, event: Event) -> None:
        assert isinstance(event, GoalChanged | ParameterChanged)
        with self._cursor() as cur:
            skus = skus_in_scope(cur, event.scope, event.ids)
        self.request(skus, reason=f"{event.type}:{event.scope}")

    async def on_recompute_requested(self, event: Event) -> None:
        assert isinstance(event, RecomputeRequested)
        if event.sku_ids:
            self.request(event.sku_ids, reason=event.reason)

    def request(self, sku_ids: Iterable[str] | None, reason: str = "") -> None:
        """Queue SKUs (``None`` = all) for the worker; marks them ``queued`` right away."""
        ids = None if sku_ids is None else {str(s) for s in sku_ids}
        if ids is None:
            self._pending_all = True
        else:
            self._pending |= ids
        self._reasons.append(reason)
        try:
            with self._cursor() as cur:
                camps = campaign_map(cur)
                targets = self._targets(camps, ids)[1]
                if targets:
                    state.mark(cur, targets, "queued")
        except duckdb.Error as exc:  # a concurrent write; the worker marks them anyway
            log.warning("mark_queued_failed", error=str(exc))
            targets = []
        if targets and self._notify is not None:
            self._notify("compute.state", {"skus": targets, "state": "queued"})
        if self._idle is not None:
            self._idle.clear()
        if self._wake is not None:
            self._wake.set()

    async def drain(self) -> None:
        """Wait until nothing is pending or running (wrap in ``asyncio.timeout``)."""
        while self.busy:
            if self._idle is None:
                return
            await self._idle.wait()
            await asyncio.sleep(0)

    # ------------------------------------------------------------------ worker
    async def _run(self) -> None:
        assert self._wake is not None and self._idle is not None
        while True:
            if not (self._pending or self._pending_all):
                self._idle.set()
                await self._wake.wait()
            self._wake.clear()
            if not (self._pending or self._pending_all):
                continue
            ids = None if self._pending_all else set(self._pending)
            self._pending, self._pending_all, self._reasons = set(), False, []
            try:
                res = await asyncio.to_thread(self._process, ids)
            except Exception as exc:  # never kill the worker
                log.exception("recompute_crashed", error=str(exc))
                await self.bus.publish(
                    ComputeFailed(target="orchestrator", stage="recompute", error=str(exc))
                )
                continue
            for ev in res.events:
                await self.bus.publish(ev)
            if self._notify is not None:
                fresh = sorted(set(res.skus) - set(res.failed))
                if fresh:
                    self._notify(
                        "compute.state",
                        {"skus": fresh, "state": "fresh", "computed_at": utcnow().isoformat()},
                    )
                if res.failed:
                    self._notify("compute.state", {"skus": res.failed, "state": "failed"})

    def _process(self, ids: set[str] | None) -> RecomputeResult:
        with self.lock, self._cursor() as conn:
            res = self.recompute(conn, ids)
            if self.evaluator is not None and res.as_of is not None:
                try:
                    res.events += self.evaluator.evaluate(conn, res.as_of, res.drift)
                except Exception as exc:
                    log.exception("alert_evaluate_failed", error=str(exc))
                    res.events.append(
                        ComputeFailed(target="alerts", stage="evaluate", error=str(exc))
                    )
            return res

    def reset_day(self) -> None:
        """Replay restarted from hour 0: forget drift alarms of the old run."""
        self._drift_seen.clear()
        self._drift_up.clear()

    # ------------------------------------------------------------------ core (sync)
    @staticmethod
    def _targets(camps: dict[str, list[str]], ids: set[str] | None) -> tuple[list[str], list[str]]:
        if ids is None:
            touched = sorted(camps)
        else:
            touched = sorted(c for c, skus in camps.items() if ids & set(skus))
        skus = sorted({s for c in touched for s in camps[c]})
        return touched, skus

    def recompute(self, conn: duckdb.DuckDBPyConnection, ids: set[str] | None) -> RecomputeResult:
        from app.forecast import service as fc_service
        from app.simulator.inputs import data_as_of, default_start

        t0 = time.perf_counter()
        as_of = data_as_of(conn)
        res = RecomputeResult(as_of=as_of)
        if as_of is None:
            return res
        camps = campaign_map(conn)
        touched, skus = self._targets(camps, ids)
        res.campaigns, res.skus = touched, skus
        if not skus:
            return res
        state.mark(conn, skus, "computing")
        start = default_start(conn)
        day0 = day_start(as_of)
        failed: set[str] = set()

        # baseline + drift + forecasts (one stage for all SKUs)
        forecasts: dict[str, ForecastResult] = {}
        t_fc = time.perf_counter()
        try:
            matrix = fc_service.load_matrix(conn)
            baseline = self.baseline(conn, skus, day0, matrix=matrix)
            res.drift = self._drift(baseline, matrix, as_of, day0)
            res.events += [a.event() for a in res.drift]
            drift_on = active_up(list(self._drift_up.values()), start)
            for r in fc_service.forecast_skus(
                conn,
                skus,
                origin=start,
                horizon_hours=FORECAST_HORIZON,
                drift_active=drift_on,
                matrix=matrix,
                persist=True,
            ):
                forecasts[r.sku_id] = r
                res.events.append(ForecastUpdated(sku_id=r.sku_id, forecast_id=r.forecast_id))
            res.events.append(
                StageDone(stage="forecast", duration_ms=(time.perf_counter() - t_fc) * 1e3)
            )
        except Exception as exc:
            log.exception("forecast_stage_failed", error=str(exc))
            failed |= self._fail(conn, skus, "forecast", exc, res)
            res.failed = sorted(failed)
            res.duration_ms = (time.perf_counter() - t0) * 1e3
            return res

        if self.simulate:
            for cid in touched:
                ok = self._simulate(conn, cid, camps[cid], forecasts, res)
                if not ok:
                    failed |= self._fail(conn, camps[cid], f"simulate:{cid}", None, res)
        good = [s for s in skus if s not in failed]
        state.mark_fresh(
            conn,
            good,
            data_as_of=as_of,
            input_hash=_hash(as_of.isoformat(), *sorted(f.forecast_id for f in forecasts.values())),
        )
        res.failed = sorted(failed)
        res.duration_ms = (time.perf_counter() - t0) * 1e3
        self.runs += 1
        self.last = res
        log.info(
            "recompute_done",
            as_of=as_of.isoformat(),
            skus=len(skus),
            campaigns=len(touched),
            simulated=len(res.simulated),
            drift=len(res.drift),
            failed=len(res.failed),
            ms=round(res.duration_ms),
        )
        return res

    def _fail(
        self,
        conn: duckdb.DuckDBPyConnection,
        skus: Sequence[str],
        stage: str,
        exc: Exception | None,
        res: RecomputeResult,
    ) -> set[str]:
        err = "" if exc is None else f"{type(exc).__name__}: {exc}"
        dead = state.mark_failed(conn, skus, err or stage)
        for sku in dead:
            res.events.append(ComputeFailed(target=f"sku:{sku}", stage=stage, error=err or stage))
        return set(skus)

    def _simulate(
        self,
        conn: duckdb.DuckDBPyConnection,
        campaign_id: str,
        skus: Sequence[str],
        forecasts: dict[str, ForecastResult],
        res: RecomputeResult,
    ) -> bool:
        from app.economics.goals import active_goal
        from app.simulator.locks import RUN_LOCK  # self.lock (held) → RUN_LOCK
        from app.simulator.schemas import SimRequest
        from app.simulator.service import run_simulation

        goal = active_goal(conn, campaign_id)
        if goal is None:
            return True
        t = time.perf_counter()
        events: list[RecommendationChanged] = []
        try:
            req = SimRequest(campaign_id=campaign_id, goal_id=goal.goal_id, samples=self.samples)
            with RUN_LOCK:
                out = run_simulation(
                    conn,
                    req,
                    requested_by="orchestrator",
                    forecasts={s: forecasts[s] for s in skus if s in forecasts},
                    events=events,
                )
        except Exception as exc:
            log.warning("simulate_failed", campaign_id=campaign_id, error=str(exc))
            res.events.append(
                ComputeFailed(
                    target=f"campaign:{campaign_id}",
                    stage="simulate",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            return False
        res.events += events
        res.simulated.append(campaign_id)
        res.events.append(
            StageDone(
                stage="simulate",
                campaign_id=campaign_id,
                duration_ms=(time.perf_counter() - t) * 1e3,
            )
        )
        if out.blocked:
            log.info("simulate_blocked", campaign_id=campaign_id)
        return True

    def baseline(
        self,
        conn: duckdb.DuckDBPyConnection,
        skus: Sequence[str],
        day0: datetime,
        *,
        matrix: Any = None,
    ) -> dict[str, ForecastResult]:
        """Forecast of the day (origin = local midnight), computed once and persisted."""
        from app.forecast import service as fc_service

        out = load_baseline(conn, skus, day0)
        missing = [s for s in skus if s not in out]
        if missing:
            for r in fc_service.forecast_skus(
                conn,
                missing,
                origin=day0,
                horizon_hours=BASELINE_HORIZON,
                matrix=matrix,
                persist=True,
            ):
                out[r.sku_id] = r
        return out

    def _drift(
        self,
        baseline: dict[str, ForecastResult],
        matrix: Any,
        as_of: datetime,
        day0: datetime,
    ) -> list[DriftAlarm]:
        from app.forecast import service as fc_service

        if as_of <= day0 or not baseline:
            return []
        day = local_date(day0)
        new: list[DriftAlarm] = []
        for a in fc_service.scan_drift(matrix, list(baseline.values()), as_of):
            if a.direction == "up":
                prev = self._drift_up.get(a.sku_id)
                if prev is None or a.hour > prev.hour:
                    self._drift_up[a.sku_id] = a
            key = (day, a.sku_id, a.direction)
            if key in self._drift_seen:
                continue
            self._drift_seen.add(key)
            new.append(a)
        return new


def load_baseline(
    conn: duckdb.DuckDBPyConnection, skus: Sequence[str], day0: datetime
) -> dict[str, ForecastResult]:
    rows = conn.execute(
        "SELECT sku_id, payload FROM forecasts WHERE list_contains(?, sku_id) AND origin = ?"
        " AND horizon_hours >= ? QUALIFY row_number() OVER (PARTITION BY sku_id"
        " ORDER BY created_at DESC) = 1",
        [list(skus), day0, BASELINE_HORIZON],
    ).fetchall()
    return {str(s): ForecastResult.model_validate_json(p) for s, p in rows}
