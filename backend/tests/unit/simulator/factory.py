"""Synthetic ``SimInputs`` (no DB) for the simulator unit tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np

from app.core.timeutil import to_local
from app.economics.goals import Goal, GoalKind
from app.economics.parameters import ParamSet
from app.simulator.inputs import SimInputs, SkuInput
from app.simulator.policies import SpendContext
from app.simulator.schemas import PriorIroas

START = datetime(2026, 11, 10, 18, tzinfo=UTC)  # 11/11 01:00 VN


def sku(
    sku_id: str,
    T: int,  # noqa: N803
    *,
    lam: float | np.ndarray = 2.0,
    m: float = 10_000.0,
    price: float = 100_000.0,
    stock: float = 50.0,
    v: float = 0.0,
    reserve: float = 0.0,
    bundle: list[tuple[str, int]] | None = None,
    in_campaign: bool = True,
    k: float = 3.0,
    sigma: float = 0.2,
    plans: list[tuple[date, int]] | None = None,
) -> SkuInput:
    lam_arr = np.full(T, lam, dtype=np.float64) if np.isscalar(lam) else np.asarray(lam, float)
    return SkuInput(
        sku_id=sku_id,
        is_bundle=bundle is not None,
        in_campaign=in_campaign,
        components=bundle or [],
        lam_sq=lam_arr,
        k=k,
        sigma=sigma,
        forecast_id=f"fc-{sku_id}",
        label="reference",
        m=np.full(T, m),
        price=np.full(T, price),
        leftover_value=0.0 if bundle is not None else v,
        stock0=0.0 if bundle is not None else stock,
        safety=0.0,
        reserve=reserve,
        restock_plans=plans or [],
        restock_delay=0.0,
    )


def make_inputs(
    skus: list[SkuInput],
    T: int = 24,  # noqa: N803
    *,
    budget: float = 2_400_000.0,
    goal_kind: GoalKind = "profit",
    target_units: int | None = None,
    must_run_hours: list[int] | None = None,
    flash_hours: list[int] | None = None,
    start: datetime = START,
    params: ParamSet | None = None,
    spend_scale: float = 100_000.0,
) -> SimInputs:
    hours = [start + timedelta(hours=i) for i in range(T)]
    hour_local = np.array([to_local(h).hour for h in hours], dtype=np.int64)
    days = sorted({to_local(h).date() for h in hours})
    day_index = np.array([days.index(to_local(h).date()) for h in hours], dtype=np.int64)
    profile = np.full((len(days), 24), 1 / 24)
    flash = np.isin(hour_local, flash_hours or []) & (day_index == 0)
    must = np.isin(hour_local, must_run_hours or []) & (day_index == 0)
    reserve_active = (day_index == 0) & (hours_before_last(hour_local, flash_hours or []))
    goal = Goal(
        goal_id="goal-test",
        scope_id="CDX",
        kind=goal_kind,
        deadline=start + timedelta(hours=T),
        target_units=target_units,
        confirmed_by="tester",
        created_at=start,
    )
    inp = SimInputs(
        campaign_id="CDX",
        campaign_name="test",
        start=start,
        hours=hours,
        hour_local=hour_local,
        day_index=day_index,
        days=days,
        skus=skus,
        spend=SpendContext(
            hour_local=hour_local,
            day_index=day_index,
            budget_day=np.full(len(days), budget),
            profile=profile,
            spent_start_day=0.0,
            restock_t=None,
        ),
        adstock0=0.0,
        iroas=PriorIroas(campaign_id="CDX", spend_scale=spend_scale),
        iroas_source="prior",
        flash=flash,
        must_run=must,
        must_run_windows=[],
        reserve_active=reserve_active,
        flash_sold_before={},
        restock_hour=10,
        rank_loss=0.0,
        penalty=0.0,
        goal=goal,
        params=params or ParamSet(stored={}),
        data_as_of=start,
        panel_version="pv",
        data_label="verified",
        main_sku=skus[0].sku_id,
    )
    inp.spend = SpendContext(
        hour_local=hour_local,
        day_index=day_index,
        budget_day=np.full(len(days), budget),
        profile=profile,
        spent_start_day=0.0,
        restock_t=inp.restock_t(),
    )
    return inp


def hours_before_last(hour_local: np.ndarray, flash_hours: list[int]) -> np.ndarray:
    if not flash_hours:
        return np.zeros(len(hour_local), dtype=bool)
    return np.asarray(hour_local <= max(flash_hours), dtype=bool)
