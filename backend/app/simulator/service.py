"""Entry point of the decision layer (``POST /scenarios/run``, orchestrator, AI tool).

:func:`run_simulation` = goal + parameters → :func:`inputs.load_inputs` →
:func:`engine.simulate` (all actions, common random numbers) →
:func:`recommend.build_recommendation` (+ zone bar, flip conditions) → persisted to
``scenarios`` / ``scenario_results`` / ``recommendations``.

Results are cached in memory by ``(campaign_id, forecast_ids, estimate_id, param_version,
goal_id, as_of, start, horizon, actions, overrides, samples, seed, session_id)`` (05 §5.5, 06 §3);
a hit returns the stored output with ``cache_hit = True`` and writes nothing.

A **what-if** run (:func:`is_what_if`: ``param_overrides``, ``session_id`` or an action list
other than the default one) is persisted for ``GET /scenarios/{id}`` but its recommendation
never replaces the campaign's active one: only a standard run supersedes (sửa lỗi nhỏ M3).
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

import duckdb
import numpy as np

from app.ads_effect.schemas import IroasEstimate
from app.core.events import RecommendationChanged
from app.core.ids import new_id
from app.core.timeutil import to_utc
from app.economics.goals import Goal, get_goal
from app.economics.parameters import load_param_set
from app.forecast.base import ForecastResult
from app.simulator import engine, recommend
from app.simulator.engine import Perturb, SimRun
from app.simulator.inputs import SimInputs, load_inputs
from app.simulator.policies import default_actions, peak_windows, windows_from_hours
from app.simulator.schemas import ActionSpec, IroasPosterior, SimOutput, SimRequest
from app.simulator.trace import build_trace


def default_action_list(inp: SimInputs) -> list[ActionSpec]:
    """5 kinds; ``schedule_hours`` = committed (``campaign_must_run``) hours when there are
    any, else the peak-demand hours (decision log M3)."""
    if inp.must_run_windows:
        windows = windows_from_hours(
            sorted({h for a, b in inp.must_run_windows for h in range(a, b)})
        )
    else:
        tot = np.sum([s.lam_sq for s in inp.skus if s.in_campaign], axis=0)
        windows = peak_windows(np.asarray(tot, dtype=np.float64), inp.hour_local)
    return default_actions(
        inp.params.num_list("reduce_budget_options"),
        windows,
        has_restock=inp.restock_t() is not None
        or any(s.restock_plans for s in inp.stock_skus if s.in_campaign),
    )


def is_what_if(
    req: SimRequest, actions: Sequence[ActionSpec], default: Sequence[ActionSpec]
) -> bool:
    """A run is a what-if when it changes an assumption (``param_overrides``, session
    parameters) or compares its own set of actions; the standard run of the campaign uses
    the stored parameters and the default actions (``actions = null``)."""
    if req.param_overrides or req.session_id is not None:
        return True
    return {a.key for a in actions} != {a.key for a in default}


def request_as_run(req: SimRequest, inp: SimInputs) -> SimRequest:
    """The request echoed back: ``start`` / ``horizon_hours`` resolved (UTC), the rest as
    sent. Re-sending it gives the same cache key."""
    return req.model_copy(update={"start": to_utc(inp.start), "horizon_hours": inp.T})


def _key(inp: SimInputs, req: SimRequest, actions: Sequence[ActionSpec]) -> str:
    return engine.cache_key(
        [
            inp.campaign_id,
            tuple(inp.forecast_ids),
            inp.iroas.estimate_id,
            tuple(sorted(inp.params.versions.items())),
            inp.goal.goal_id,
            inp.goal.version,
            repr(sorted(inp.goal.params.items())),
            to_utc(inp.data_as_of).isoformat() if inp.data_as_of else None,
            to_utc(inp.start).isoformat(),
            inp.T,
            tuple(a.key for a in actions),
            tuple(sorted((k, repr(v)) for k, v in inp.params.overrides.items())),
            req.samples,
            req.seed,
            req.session_id,  # session runs are what-ifs (never the active recommendation)
        ]
    )


def run_simulation(
    conn: duckdb.DuckDBPyConnection,
    req: SimRequest,
    *,
    requested_by: str = "user",
    forecasts: Mapping[str, ForecastResult] | None = None,
    iroas: IroasPosterior | IroasEstimate | None = None,
    persist: bool = True,
    goal_override: Goal | None = None,
    with_flips: bool = True,
    use_cache: bool = True,
    events: list[RecommendationChanged] | None = None,
) -> SimOutput:
    """Simulate every action of ``req`` and build the recommendation."""
    t0 = time.perf_counter()
    goal = goal_override or get_goal(conn, req.goal_id)
    params = load_param_set(
        conn,
        campaign_id=req.campaign_id,
        session_id=req.session_id,
        overrides=req.param_overrides,
    )
    inp = load_inputs(
        conn,
        campaign_id=req.campaign_id,
        goal=goal,
        params=params,
        start=req.start,
        horizon_hours=req.horizon_hours,
        forecasts=forecasts,
        iroas=iroas,
    )
    t_load = time.perf_counter()
    if inp.blocked_reasons:
        rec = recommend.blocked_recommendation(inp)
        out = SimOutput(
            scenario_id=new_id("scn"),
            results=[],
            recommendation=rec,
            input_hash=engine.cache_key(
                [inp.campaign_id, "blocked", to_utc(inp.start).isoformat()]
            ),
            blocked=True,
        )
        rec.scenario_id = out.scenario_id
        return out
    default = default_action_list(inp)
    actions = list(req.actions) if req.actions else default
    what_if = is_what_if(req, actions, default)
    key = _key(inp, req, actions)
    if use_cache:
        hit = engine.cache_get(key)
        if isinstance(hit, SimOutput):
            return hit.model_copy(update={"cache_hit": True})
    run = engine.simulate(inp, actions, req.samples, req.seed)
    t_sim = time.perf_counter()
    sens_draws: dict[int, engine.Draws] = {}

    def resim(acts: Sequence[ActionSpec], pt: Perturb, samples: int) -> SimRun:
        dr = run.draws if samples == run.samples else sens_draws.get(samples)
        r = engine.simulate(inp, acts, samples, req.seed, perturb=pt, draws=dr)
        sens_draws.setdefault(samples, r.draws)
        return r

    rec, sel = recommend.build_recommendation(run, resim, with_flips=with_flips)
    t_rec = time.perf_counter()
    results = recommend.scenario_results(run, sel)
    out = SimOutput(
        scenario_id=new_id("scn"),
        results=results,
        recommendation=rec,
        input_hash=key,
        what_if=what_if,
        calculation_trace=build_trace(conn, run, key),
        timings_ms={
            "load": (t_load - t0) * 1e3,
            "simulate": (t_sim - t_load) * 1e3,
            "recommend": (t_rec - t_sim) * 1e3,
        },
    )
    rec.scenario_id = out.scenario_id
    if persist:
        conn.execute("BEGIN TRANSACTION")
        try:
            ev = recommend.persist(
                conn,
                out,
                inp,
                overrides=dict(req.param_overrides),
                samples=req.samples,
                seed=req.seed,
                requested_by=requested_by,
                request_json=request_as_run(req, inp).model_dump_json(),
                what_if=what_if,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        if ev is not None and events is not None:
            events.append(ev)
    out.timings_ms["total"] = (time.perf_counter() - t0) * 1e3
    if use_cache:
        engine.cache_put(key, out)
    return out
