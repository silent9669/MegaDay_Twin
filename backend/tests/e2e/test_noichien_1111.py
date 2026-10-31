"""SIM-06 · decision e2e on ``demo`` (05 §10, §11) + performance (05 §5.5).

The DB holds the ``demo`` history and the 11.11 live stream **up to 01:00** (hour 0 of
``live_stream``) loaded by the real pipeline, as the shop would see it at 01:00.

* Nồi chiên CD002, 11.11, goal ``profit`` → ``schedule_hours`` (flash windows) wins,
  ``pause`` / ``pause_until_restock`` blocked by ``campaign_must_run``.
* Cốc meme CD004 on a normal day, ``profit`` → ``pause`` or ``reduce_budget``.
* Đèn LED Tết CD003, ``clear_stock`` until 31/01 → not ``pause``; a flip condition on V.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pytest

from app.economics.goals import Goal, create_goal
from app.economics.parameters import load_param_set
from app.forecast import service as forecast_service
from app.ingest.pipeline import ingest_live_delta
from app.panel.build import build_hourly_panel
from app.simulator import engine
from app.simulator.inputs import load_inputs
from app.simulator.schemas import SimOutput, SimRequest
from app.simulator.service import default_action_list, run_simulation
from eval.live_replay import write_live_stream
from tests.mockdata import load_mock, require

pytestmark = pytest.mark.e2e

START_1111 = datetime.fromisoformat("2026-11-11T01:00:00+07:00")
END_1111 = datetime.fromisoformat("2026-11-12T00:00:00+07:00")
NORMAL_DAY = datetime.fromisoformat("2026-11-05T09:00:00+07:00")


@pytest.fixture(scope="module")
def demo_0100(tmp_path_factory: pytest.TempPathFactory) -> Iterator[duckdb.DuckDBPyConnection]:
    require("demo")
    root = tmp_path_factory.mktemp("demo_0100")
    conn = duckdb.connect(str(root / "demo.duckdb"))
    load_mock(conn, "demo", build_panel=False)
    write_live_stream("demo", root / "live_stream", 0)  # orders + stock up to 00:59
    delta = ingest_live_delta(conn, root / "live_stream", build_panel=False)
    assert delta.status == "loaded"
    build_hourly_panel(conn)
    # real iROAS (ADS-04) when the ads layer is available; the simulator falls back to the
    # prior otherwise (decision log M3-mo-phong)
    try:
        from app.ads_effect.iroas import estimate_all, write_estimates
    except ImportError:  # pragma: no cover
        pass
    else:
        write_estimates(conn, estimate_all(conn).estimates)
    engine.cache_clear()
    yield conn
    conn.close()


def _goal(conn: duckdb.DuckDBPyConnection, campaign: str, kind: str, deadline: datetime) -> Goal:
    return create_goal(
        conn,
        scope_id=campaign,
        kind=kind,
        deadline=deadline,
        confirmed_by="lead-e2e",  # type: ignore[arg-type]
    )


def _show(text: str) -> None:
    """Print UTF-8 even on a cp1252 Windows console (run with ``-s`` to see the tables)."""
    try:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.flush()
    except (AttributeError, ValueError):
        print(text.encode("ascii", "backslashreplace").decode())


def _table(out: SimOutput) -> str:
    lines = []
    for r in out.results:
        lines.append(
            f"{r.action.key:28s} feasible={r.feasible!s:5s} J={r.objective.mean:>13,.0f}"
            f" contrib={r.contribution.mean:>11,.0f} spend={r.spend.mean:>11,.0f}"
            f" sold={r.units_sold.mean:6.1f} viol={r.violations}"
        )
    rec = out.recommendation
    lines.append(
        f"→ {rec.status} best={rec.best.key if rec.best else None}"
        f" runner_up={rec.runner_up.key if rec.runner_up else None}"
        f" p={rec.p_best_beats_runner_up} gain={rec.expected_gain_vnd} conf={rec.confidence}"
    )
    lines += [f"   flip: {f.text}" for f in rec.flip_conditions]
    lines += [f"   - {r}" for r in rec.reasons]
    return "\n".join(lines)


@pytest.fixture(scope="module")
def noichien(demo_0100: duckdb.DuckDBPyConnection) -> SimOutput:
    g = _goal(demo_0100, "CD002", "profit", END_1111)
    out = run_simulation(demo_0100, SimRequest(campaign_id="CD002", goal_id=g.goal_id))
    _show("\nCD002 11.11 01:00\n" + _table(out))
    return out


def test_noichien_1111_schedule_hours(noichien: SimOutput) -> None:
    rec = noichien.recommendation
    by = {r.action.key: r for r in noichien.results}
    assert rec.best is not None and rec.best.kind == "schedule_hours"
    assert rec.best.windows_local == [(0, 2), (12, 14)]
    assert by["pause"].violations == ["campaign_must_run"] and not by["pause"].feasible
    assert not by["pause_until_restock"].feasible
    assert rec.status == "recommend"
    assert rec.p_best_beats_runner_up is not None and rec.p_best_beats_runner_up > 0.95
    # continue spends most of the double-day budget, schedule_hours ~20–25 % (05 §10)
    spend_c = by["continue"].spend.mean
    spend_s = by[rec.best.key].spend.mean
    assert spend_c > 4_500_000 and 0.15 < spend_s / 5_592_000 < 0.3
    assert by["continue"].objective.mean < by[rec.best.key].objective.mean - 3_000_000
    # sales hardly depend on ads: stock (76 at 01:00) is far below demand
    noi = {s.sku_id: s for s in by["continue"].per_sku}["NOICHIEN2L"]
    assert noi.contribution_per_unit == pytest.approx(9_280)
    assert noi.demand.p50 > 100 and noi.stockout_hour_p50 is not None
    assert any("9.280đ" in r for r in rec.reasons)
    assert any("cam kết với sàn" in r for r in rec.reasons)
    ref = rec.inputs_ref
    assert ref.iroas_source == "estimate" and ref.estimate_id
    assert len(ref.forecast_ids) == 3 and ref.goal_id and ref.data_as_of is not None
    assert ref.param_version


def test_noichien_reproducible_and_cached(
    demo_0100: duckdb.DuckDBPyConnection, noichien: SimOutput
) -> None:
    rec = noichien.recommendation
    req = SimRequest(campaign_id="CD002", goal_id=rec.inputs_ref.goal_id)
    hit = run_simulation(demo_0100, req)
    assert hit.cache_hit and hit.scenario_id == noichien.scenario_id
    again = run_simulation(demo_0100, req, use_cache=False, persist=False)
    assert [r.objective for r in again.results] == [r.objective for r in noichien.results]
    assert again.recommendation.best == rec.best
    row = demo_0100.execute(
        "SELECT count(*) FROM recommendations WHERE campaign_id = 'CD002'"
    ).fetchone()
    assert row == (1,)


def test_meme_normal_day_profit(demo_0100: duckdb.DuckDBPyConnection) -> None:
    g = _goal(demo_0100, "CD004", "profit", datetime.fromisoformat("2026-11-06T00:00:00+07:00"))
    out = run_simulation(
        demo_0100, SimRequest(campaign_id="CD004", goal_id=g.goal_id, start=NORMAL_DAY)
    )
    _show("\nCD004 05/11 09:00\n" + _table(out))
    best = out.recommendation.best
    assert best is not None and best.kind in ("pause", "reduce_budget")


def test_led_tet_clear_stock(demo_0100: duckdb.DuckDBPyConnection) -> None:
    g = _goal(
        demo_0100, "CD003", "clear_stock", datetime.fromisoformat("2027-01-31T23:00:00+07:00")
    )
    out = run_simulation(
        demo_0100, SimRequest(campaign_id="CD003", goal_id=g.goal_id, start=NORMAL_DAY)
    )
    _show("\nCD003 05/11 09:00 clear_stock\n" + _table(out))
    rec = out.recommendation
    assert len(out.results[0].per_sku) == 2
    assert rec.best is not None and rec.best.kind != "pause"
    assert any(f.factor == "leftover_value_per_unit" for f in rec.flip_conditions)


@pytest.mark.perf
def test_performance(demo_0100: duckdb.DuckDBPyConnection) -> None:
    """05 §5.5: 1 campaign (all actions, 1.000 samples) < 300 ms; full run < 1 s;
    50 campaigns < 2 s. Forecasts are computed once beforehand (cached by the
    orchestrator in production)."""
    conn = demo_0100
    camps = [r[0] for r in conn.execute("SELECT campaign_id FROM campaigns ORDER BY 1").fetchall()]
    skus = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT s.sku_id FROM campaign_skus s UNION SELECT component_sku_id"
            " FROM bundle_components"
        ).fetchall()
    ]
    fc = {
        r.sku_id: r
        for r in forecast_service.forecast_skus(conn, skus, START_1111, 23, persist=False)
    }
    jobs = []
    for c in camps:
        g = _goal(conn, c, "profit", END_1111)
        inp = load_inputs(
            conn,
            campaign_id=c,
            goal=g,
            params=load_param_set(conn, campaign_id=c),
            start=START_1111,
            forecasts=fc,
        )
        jobs.append((inp, default_action_list(inp)))
    cd002 = next(j for j in jobs if j[0].campaign_id == "CD002")
    one = []
    for _ in range(3):
        t0 = time.perf_counter()
        engine.simulate(cd002[0], cd002[1], 1000)
        one.append(time.perf_counter() - t0)
    many = []
    for _ in range(2):
        t0 = time.perf_counter()
        engine.simulate_many(jobs, 1000)
        many.append(time.perf_counter() - t0)
    g = _goal(conn, "CD002", "profit", END_1111)
    t0 = time.perf_counter()
    run_simulation(
        conn,
        SimRequest(campaign_id="CD002", goal_id=g.goal_id),
        forecasts=fc,
        persist=False,
        use_cache=False,
    )
    full = time.perf_counter() - t0
    _show(
        f"\nperf: 1 campaign {len(cd002[1])} actions {min(one) * 1e3:.0f} ms;"
        f" {len(jobs)} campaigns {min(many):.2f} s; full run_simulation {full * 1e3:.0f} ms"
    )
    assert len(jobs) >= 45
    assert min(one) < 0.3
    assert min(many) < 2.0
    assert full < 1.0
    assert np.isfinite(min(one))


def test_campaign_not_found(demo_0100: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    from app.core.errors import NotFound

    g = _goal(demo_0100, "CD999", "profit", END_1111)
    with pytest.raises(NotFound):
        run_simulation(demo_0100, SimRequest(campaign_id="CD999", goal_id=g.goal_id))
    _ = tmp_path
