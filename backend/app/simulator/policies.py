"""Spend schedule of the 5 actions (SIM-01, plan 05 §5.1).

``profile[h]`` = spend in VN hour ``h`` as a **fraction of that day's budget**, averaged over
the 7 most recent days of the same context (double day: the latest double day). Using the
fraction of budget (instead of the share of the day's spend) keeps campaigns that do not
exhaust their budget at their real level (decision log M3).

| action                     | planned spend in hour ``t`` (before the daily cap)                |
|----------------------------|--------------------------------------------------------------------|
| ``continue`` | ``budget_day · profile[h]`` |
| ``pause`` | 0 |
| ``reduce_budget(pct)`` | ``(1 − pct) · budget_day · profile[h]``; cap ``(1 − pct)·budget`` |
| ``schedule_hours(windows)`` | ``budget_day · profile[h]`` if ``h ∈ windows``, else 0 |
| ``pause_until_restock`` | 0 until the planned arrival hour, then as ``continue`` |

Daily cap: ``budget_day − spent so far that day``; it resets every VN day.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from app.simulator.schemas import ActionSpec

FloatArr = npt.NDArray[np.float64]
BoolArr = npt.NDArray[np.bool_]
IntArr = npt.NDArray[np.int64]


@dataclass(frozen=True)
class SpendContext:
    """Deterministic inputs of the spend schedule over the horizon ``T``."""

    hour_local: IntArr  # [T] 0..23
    day_index: IntArr  # [T] 0 = start day
    budget_day: FloatArr  # [D] budget of each horizon day (VND)
    profile: FloatArr  # [D, 24] fraction of the day budget spent in each VN hour
    spent_start_day: float  # spent on the start day before ``start``
    restock_t: int | None  # first horizon index with stock arriving (pause_until_restock)


def on_mask(action: ActionSpec, ctx: SpendContext) -> BoolArr:
    """Hours where the campaign is switched on by the action."""
    t = len(ctx.hour_local)
    if action.kind in ("continue", "reduce_budget"):
        return np.ones(t, dtype=np.bool_)
    if action.kind == "pause":
        return np.zeros(t, dtype=np.bool_)
    if action.kind == "schedule_hours":
        m = np.zeros(t, dtype=np.bool_)
        for a, b in action.windows_local or []:
            m |= (ctx.hour_local >= a) & (ctx.hour_local < b)
        return m
    # pause_until_restock
    m = np.zeros(t, dtype=np.bool_)
    if ctx.restock_t is not None and ctx.restock_t < t:
        m[ctx.restock_t :] = True
    return m


def spend_schedule(action: ActionSpec, ctx: SpendContext) -> FloatArr:
    """Hourly spend ``[T]`` of the action after the daily budget cap."""
    scale = 1.0 - float(action.pct) if action.kind == "reduce_budget" and action.pct else 1.0
    mask = on_mask(action, ctx)
    budget = ctx.budget_day[ctx.day_index]
    planned = np.where(mask, scale * budget * ctx.profile[ctx.day_index, ctx.hour_local], 0.0)
    out = np.zeros_like(planned)
    day = -1
    remaining = 0.0
    for i in range(len(planned)):
        d = int(ctx.day_index[i])
        if d != day:
            day = d
            cap = scale * float(ctx.budget_day[d])
            remaining = cap - (ctx.spent_start_day if d == 0 else 0.0)
        s = min(float(planned[i]), max(remaining, 0.0))
        out[i] = s
        remaining -= s
    return out


def adstock(spend: FloatArr, delta: float, a0: float = 0.0) -> FloatArr:
    """``A_t = spend_t + δ·A_{t−1}`` with ``A_{t0−1} = a0``."""
    out = np.empty_like(spend)
    prev = a0
    for i, s in enumerate(spend):
        prev = float(s) + delta * prev
        out[i] = prev
    return out


def windows_from_hours(hours: Sequence[int]) -> list[tuple[int, int]]:
    """Merge VN hours into ``[start, end)`` windows: ``[0, 1, 12, 13]`` → ``[(0,2),(12,14)]``."""
    hs = sorted(set(int(h) for h in hours))
    out: list[tuple[int, int]] = []
    for h in hs:
        if out and out[-1][1] == h:
            out[-1] = (out[-1][0], h + 1)
        else:
            out.append((h, h + 1))
    return out


def peak_windows(
    hourly_demand: FloatArr, hour_local: IntArr, keep: float = 0.5
) -> list[tuple[int, int]]:
    """Default ``schedule_hours`` without a flash commitment: the VN hours whose mean
    demand is in the top ``keep`` share of hours (decision log M3)."""
    by_h = np.zeros(24)
    cnt = np.zeros(24)
    np.add.at(by_h, hour_local, hourly_demand)
    np.add.at(cnt, hour_local, 1)
    mean = np.where(cnt > 0, by_h / np.maximum(cnt, 1), -1.0)
    seen = np.flatnonzero(cnt > 0)
    if not len(seen):
        return [(8, 23)]
    thr = np.quantile(mean[seen], 1 - keep)
    return windows_from_hours([int(h) for h in seen if mean[h] >= thr]) or [(8, 23)]


def default_actions(
    reduce_options: Sequence[float],
    schedule_windows: list[tuple[int, int]] | None,
    has_restock: bool,
) -> list[ActionSpec]:
    """The 5 action kinds: continue, pause, reduce_budget (each option), schedule_hours,
    pause_until_restock (only when a restock is planned)."""
    out = [ActionSpec(kind="continue"), ActionSpec(kind="pause")]
    out += [ActionSpec(kind="reduce_budget", pct=float(p)) for p in reduce_options]
    if schedule_windows:
        out.append(ActionSpec(kind="schedule_hours", windows_local=schedule_windows))
    if has_restock:
        out.append(ActionSpec(kind="pause_until_restock"))
    return out
