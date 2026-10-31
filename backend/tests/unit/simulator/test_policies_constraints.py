"""SIM-01 / SIM-03 · spend schedules, stock loop (combo, flash reserve), constraints."""

from __future__ import annotations

import numpy as np
import pytest

from app.core.errors import ConstraintViolation
from app.simulator.constraints import (
    MUST_RUN,
    StockSetup,
    check_action,
    check_price_change,
    check_sku_toggle,
    simulate_stock,
)
from app.simulator.policies import (
    SpendContext,
    adstock,
    default_actions,
    on_mask,
    spend_schedule,
    windows_from_hours,
)
from app.simulator.schemas import ActionSpec


def _ctx(T: int = 48, spent: float = 0.0, restock_t: int | None = None) -> SpendContext:  # noqa: N803
    hours = np.arange(T, dtype=np.int64) % 24
    days = np.arange(T, dtype=np.int64) // 24
    prof = np.full((2, 24), 1 / 20)  # profile sums to 1,2 → the cap binds at 20:00
    return SpendContext(hours, days, np.array([2_400_000.0, 1_200_000.0]), prof, spent, restock_t)


def test_spend_schedules() -> None:
    ctx = _ctx()
    cont = spend_schedule(ActionSpec(kind="continue"), ctx)
    assert cont[:24].sum() == pytest.approx(2_400_000)  # daily cap
    assert cont[20:24].sum() == 0  # budget exhausted
    assert cont[24:].sum() == pytest.approx(1_200_000)  # resets the next day
    assert spend_schedule(ActionSpec(kind="pause"), ctx).sum() == 0
    red = spend_schedule(ActionSpec(kind="reduce_budget", pct=0.5), ctx)
    assert red[0] == pytest.approx(0.5 * cont[0]) and red[:24].sum() == pytest.approx(1_200_000)
    sch = spend_schedule(ActionSpec(kind="schedule_hours", windows_local=[(0, 2), (12, 14)]), ctx)
    assert np.flatnonzero(sch[:24]).tolist() == [0, 1, 12, 13]
    assert sch[0] == cont[0]  # no shifting of budget
    pur = spend_schedule(ActionSpec(kind="pause_until_restock"), _ctx(restock_t=34))
    assert pur[:34].sum() == 0 and pur[34] > 0
    assert spend_schedule(ActionSpec(kind="continue"), _ctx(spent=2_300_000))[:24].sum() == (
        pytest.approx(100_000)
    )


def test_adstock_and_windows() -> None:
    a = adstock(np.array([100.0, 0.0, 0.0]), 0.5, a0=40.0)
    assert a.tolist() == [120.0, 60.0, 30.0]
    assert windows_from_hours([0, 1, 12, 13]) == [(0, 2), (12, 14)]
    acts = default_actions([0.3, 0.5], [(0, 2)], has_restock=True)
    assert [a.key for a in acts] == [
        "continue",
        "pause",
        "reduce_budget:0.3",
        "reduce_budget:0.5",
        "schedule_hours:0-2",
        "pause_until_restock",
    ]


def test_action_spec_validation() -> None:
    with pytest.raises(ValueError):
        ActionSpec(kind="reduce_budget")
    with pytest.raises(ValueError):
        ActionSpec(kind="schedule_hours", windows_local=[(5, 3)])
    with pytest.raises(ValueError):
        ActionSpec(kind="pause", pct=0.3)


def test_must_run_blocks_pause() -> None:
    ctx = _ctx(24)
    must = np.isin(ctx.hour_local, [0, 1, 12, 13])
    labels = [str(h) for h in range(24)]
    for kind in ("pause", "pause_until_restock"):
        a = ActionSpec(kind=kind)  # type: ignore[arg-type]
        v = check_action(a, on_mask(a, ctx), must, labels)
        assert [c for c, _ in v] == [MUST_RUN] and "flash sale" in v[0][1]
    ok = ActionSpec(kind="schedule_hours", windows_local=[(0, 2), (12, 14)])
    assert check_action(ok, on_mask(ok, ctx), must, labels) == []
    part = ActionSpec(kind="schedule_hours", windows_local=[(0, 2)])
    assert check_action(part, on_mask(part, ctx), must, labels)
    assert check_action(ActionSpec(kind="reduce_budget", pct=1.0), np.ones(24, bool), must, labels)
    assert check_action(ActionSpec(kind="continue"), np.ones(24, bool), must, labels) == []


def test_what_if_constraints() -> None:
    with pytest.raises(ConstraintViolation) as e:
        check_sku_toggle("CD002", False, ["NOICHIEN2L", "GIAYLOT50"])
    assert e.value.code == "CONSTRAINT_VIOLATION" and "tách" in e.value.message
    check_sku_toggle("CD009", True, ["X"])
    with pytest.raises(ConstraintViolation):
        check_price_change("NOICHIEN2L", 600_000, 531_000)
    check_price_change("NOICHIEN2L", 500_000, 531_000)


def _setup(
    stock: list[float],
    T: int,  # noqa: N803
    *,
    reserve: list[float] | None = None,
    flash: list[int] | None = None,
    q: list[list[float]] | None = None,
) -> StockSetup:
    n = len(stock)
    fl = np.zeros(T, bool)
    fl[flash or []] = True
    return StockSetup(
        stock0=np.array(stock, float),
        restock=np.zeros((n, T)),
        reserve0=np.array(reserve or [0.0] * n, float),
        reserve_active=np.ones(T, bool),
        flash=fl,
        safety=np.zeros(n),
        combo_qty=np.array(q or np.zeros((0, n)), float).reshape(-1, n),
    )


def test_combo_deducts_components() -> None:
    T = 3  # noqa: N806
    setup = _setup([5, 100], T, q=[[1, 2]])
    d = np.zeros((2, 1, 1, T))
    d[0, 0, 0] = [1, 1, 0]
    dc = np.ones((1, 1, 1, T)) * 2
    r = simulate_stock(setup, d, dc, np.ones((2, T)), np.full((1, T), 10.0))
    # hour 0: 1 single, 2 combos (5-1-2=2 left); hour 1: 1 single, 1 combo; hour 2: nothing
    assert r.sold[:, 0, 0].tolist() == [2, 0]
    assert r.combo_sold[0, 0, 0] == 3
    assert r.stock_end[:, 0, 0].tolist() == [0, 94]  # 100 − 2·3
    assert r.contribution[0, 0] == 2 + 30
    assert r.combo_first_lost_t[0, 0, 0] == 1


def test_flash_reserve() -> None:
    T = 4  # noqa: N806
    setup = _setup([10], T, reserve=[6], flash=[2])
    d = np.full((1, 1, 1, T), 5.0)
    r = simulate_stock(setup, d, np.zeros((0, 1, 1, T)), np.ones((1, T)), np.zeros((0, T)))
    # hours 0–1: only 4 sellable outside the reserve; hour 2 (flash): sells 5 of the 6 held
    assert r.sold[0, 0, 0] == 9 and r.stock_end[0, 0, 0] == 1


def test_never_sells_negative_stock_property() -> None:
    """1000 random cases: stock never < 0, sold ≤ demand, conservation holds."""
    rng = np.random.default_rng(7)
    for _ in range(1000):
        T = int(rng.integers(1, 8))  # noqa: N806
        n = int(rng.integers(1, 4))
        c = int(rng.integers(0, 3))
        stock = rng.integers(0, 30, n).astype(float)
        q = rng.integers(0, 3, (c, n)).astype(float)
        setup = StockSetup(
            stock0=stock,
            restock=rng.integers(0, 5, (n, T)) * (rng.random((n, T)) < 0.2),
            reserve0=rng.integers(0, 10, n).astype(float),
            reserve_active=rng.random(T) < 0.7,
            flash=rng.random(T) < 0.3,
            safety=rng.integers(0, 3, n).astype(float),
            combo_qty=q,
        )
        d = rng.poisson(rng.uniform(0, 8), (n, 2, 3, T)).astype(float)
        dc = rng.poisson(rng.uniform(0, 4), (c, 2, 3, T)).astype(float)
        r = simulate_stock(setup, d, dc, np.ones((n, T)), np.ones((c, T)))
        assert (r.stock_end >= -1e-9).all()
        assert (r.sold <= d.sum(axis=3) + 1e-9).all()
        assert (r.combo_sold <= dc.sum(axis=3) + 1e-9).all()
        used = r.sold + np.einsum("cj,cas->jas", q, r.combo_sold)
        total_in = stock[:, None, None] + setup.restock.sum(axis=1)[:, None, None]
        assert np.allclose(r.stock_end, total_in - used)
