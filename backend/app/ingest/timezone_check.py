"""Detect the timezone shift of the ads report (02 §3.2).

The ads report writes ``Thời gian`` without a timezone. We look for the offset
``s`` with ``giờ_VN = giờ_file + s`` (the mock file is UTC → expected ``+7``).

Two kinds of evidence (decision log docs/quyet-dinh/M1-adapter.md §2):

1. **Day boundary** (primary). Ad budgets are *daily in VN time*, so the local
   midnight leaves marks in the file: ``Ngân sách ngày`` changes value at that
   hour, ``Hết ngân sách`` stops at that hour, and night pauses (0–5h VN)
   start at that hour. Each mark gives the file hour ``h0`` of VN 00:00, hence
   ``s = -h0 (mod 24)``.
2. **Hour-of-day rhythm** (02 §3.2, fallback and sanity check). Orders and ad
   traffic share a daily rhythm; the roll of the 24-h spend profile that best
   correlates with the 24-h order profile. Alone it is biased by budget pacing
   (spend runs out in the evening), so it decides only when no boundary mark
   exists, with the plan thresholds ``corr ≥ 0,8`` and margin ``≥ 0,1``.

Only regular days are used (double days have a different rhythm). The result
is a *proposal*: the adapter applies it when ``accepted`` and raises
``ADS_TZ_SHIFT`` so the user confirms it.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app.core.timeutil import VN_TZ, is_double_day

MIN_CORR = 0.8
MIN_MARGIN = 0.1
MIN_CORR_WITH_BOUNDARY = 0.6  # sanity check when the day boundary decided
OFFSETS = range(-11, 13)  # -12 and +12 are the same rotation of a 24-h profile
NIGHT_HOURS = 6  # night pauses 0–5h VN (02 §3.2)
MIN_BUDGET_CHANGES = 3
MIN_BUDGET_SHARE = 0.6
MIN_EXHAUST_DROP = 0.3
MIN_NIGHT_EXCESS = 0.05


class TzCheckResult(BaseModel):
    offset_hours: int  # proposed: giờ_VN = giờ_file + offset
    accepted: bool
    method: str  # day_boundary | rhythm | none
    signals: dict[str, int] = Field(default_factory=dict)  # signal → offset it implies
    corr: float  # rhythm correlation at ``offset_hours``
    best_corr_offset: int
    best_corr: float
    runner_up_offset: int
    runner_up_corr: float  # best offset outside best ± 1 h
    n_days: int
    night_pause_share: float | None = None  # paused rows landing at 0–5h VN, at offset
    correlations: dict[int, float] = Field(default_factory=dict)

    @property
    def suggestion(self) -> str:
        return f"{self.offset_hours:+d}h"


def _wrap(offset: int) -> int:
    """Map to -11..12."""
    o = offset % 24
    return o - 24 if o > 12 else o


def _circular_distance(a: int, b: int) -> int:
    d = abs(a - b) % 24
    return min(d, 24 - d)


def _skip_day(d: date) -> bool:
    return is_double_day(d) or is_double_day(d + timedelta(days=1))


def orders_hour_profile(ordered_at_utc: pd.Series) -> list[float]:
    """24 order counts by VN hour of day, regular days only (falls back to all
    days when the file only has double days, e.g. the 11.11 live file)."""
    ts = pd.to_datetime(ordered_at_utc, utc=True).dropna()
    local = ts.dt.tz_convert(VN_TZ)
    regular = local[~local.dt.date.map(is_double_day).astype(bool)]
    use = regular if len(regular) else local
    counts = use.dt.hour.value_counts().reindex(range(24), fill_value=0)
    return [float(x) for x in counts.to_numpy()]


def ads_hour_profile(
    file_times: pd.Series, spend: pd.Series, mask: pd.Series | None = None
) -> tuple[list[float], int]:
    """24-hour mean spend by *file* hour of day (rows in ``mask`` only) + number
    of days used. Days next to a double day are skipped, since the shift may
    move hours across midnight."""
    t = pd.to_datetime(file_times, errors="coerce")
    s = pd.to_numeric(spend, errors="coerce").fillna(0)
    ok = t.notna() if mask is None else (t.notna() & mask.astype(bool))
    days = t.dt.date
    keep = ok & ~days.map(lambda d: _skip_day(d) if isinstance(d, date) else True).astype(bool)
    if keep.sum() == 0:
        keep = ok
    prof = s[keep].groupby(t[keep].dt.hour).mean().reindex(range(24)).fillna(0)
    return [float(x) for x in prof.to_numpy()], int(days[keep].nunique())


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def detect_offset_hours(
    orders_by_hour: list[float] | np.ndarray, spend_by_hour: list[float] | np.ndarray
) -> tuple[int, float, dict[int, float]]:
    """02 §3.2 rhythm match: ``(offset, corr, all_correlations)``."""
    o = np.asarray(orders_by_hour, dtype=float)
    sp = np.asarray(spend_by_hour, dtype=float)
    corrs = {s: _corr(o, np.roll(sp, s)) for s in OFFSETS}
    best = max(corrs, key=lambda s: (round(corrs[s], 9), -abs(s)))
    return best, corrs[best], corrs


# ------------------------------------------------------------ day-boundary marks
def _budget_change_hour(t: pd.Series, campaign: pd.Series, budget: pd.Series) -> int | None:
    df = pd.DataFrame({"c": campaign, "t": t, "b": pd.to_numeric(budget, errors="coerce")})
    df = df.dropna().sort_values(["c", "t"], kind="stable")
    prev_b = df.groupby("c")["b"].shift()
    prev_t = df.groupby("c")["t"].shift()
    # consecutive hours only, so a gap in the file does not fake a change
    changed = prev_b.notna() & (df["b"] != prev_b) & (df["t"] - prev_t == pd.Timedelta(hours=1))
    hours = df.loc[changed, "t"].dt.hour
    if len(hours) < MIN_BUDGET_CHANGES:
        return None
    hour, n = Counter(hours.tolist()).most_common(1)[0]
    return int(hour) if n / len(hours) >= MIN_BUDGET_SHARE else None


def _exhausted_reset_hour(t: pd.Series, status: pd.Series) -> int | None:
    ex = (status == "budget_exhausted").astype(float)
    share = ex.groupby(t.dt.hour).mean().reindex(range(24)).fillna(0).to_numpy()
    drops = np.array([share[(h - 1) % 24] - share[h] for h in range(24)])
    h = int(drops.argmax())
    return h if drops[h] >= MIN_EXHAUST_DROP else None


def _night_pause_start(t: pd.Series, status: pd.Series) -> int | None:
    paused = (status == "paused").astype(float)
    share = paused.groupby(t.dt.hour).mean().reindex(range(24)).fillna(0).to_numpy()
    if share.sum() == 0:
        return None
    windows = np.array(
        [np.mean([share[(h + k) % 24] for k in range(NIGHT_HOURS)]) for h in range(24)]
    )
    h = int(windows.argmax())
    others = np.delete(share, [(h + k) % 24 for k in range(NIGHT_HOURS)])
    excess = windows[h] - float(np.median(others))
    return h if excess >= MIN_NIGHT_EXCESS else None


def check_ads_timezone(
    orders_profile: list[float],
    file_times: pd.Series,
    spend: pd.Series,
    status: pd.Series | None = None,
    *,
    campaign: pd.Series | None = None,
    budget: pd.Series | None = None,
) -> TzCheckResult:
    """``status`` uses normalised values (running|paused|budget_exhausted)."""
    t = pd.to_datetime(file_times, errors="coerce")
    running = None if status is None else (status == "running")
    spend_profile, n_days = ads_hour_profile(t, spend, running)
    best, best_c, corrs = detect_offset_hours(orders_profile, spend_profile)
    others = {s: c for s, c in corrs.items() if _circular_distance(s, best) > 1}
    runner = max(others, key=lambda s: others[s])

    signals: dict[str, int] = {}
    ok = t.notna()
    if campaign is not None and budget is not None:
        h = _budget_change_hour(t[ok], campaign[ok], budget[ok])
        if h is not None:
            signals["budget_reset"] = _wrap(-h)
    if status is not None:
        h = _exhausted_reset_hour(t[ok], status[ok])
        if h is not None:
            signals["budget_exhausted_reset"] = _wrap(-h)
        h = _night_pause_start(t[ok], status[ok])
        if h is not None:
            signals["night_pause_start"] = _wrap(-h)

    if signals:
        votes = Counter(signals.values())
        offset, n = votes.most_common(1)[0]
        corr = corrs[offset]
        accepted = n == len(signals) and corr >= MIN_CORR_WITH_BOUNDARY
        method = "day_boundary"
    else:
        offset, corr = best, best_c
        accepted = corr >= MIN_CORR and corr - others[runner] >= MIN_MARGIN
        method = "rhythm"

    night_share: float | None = None
    if status is not None:
        paused_hours = t[ok & (status == "paused")].dt.hour
        if len(paused_hours):
            vn_hour = (paused_hours + offset) % 24
            night_share = float((vn_hour < NIGHT_HOURS).mean())

    return TzCheckResult(
        offset_hours=offset,
        accepted=accepted,
        method=method,
        signals=signals,
        corr=round(corr, 4),
        best_corr_offset=best,
        best_corr=round(best_c, 4),
        runner_up_offset=runner,
        runner_up_corr=round(others[runner], 4),
        n_days=n_days,
        night_pause_share=None if night_share is None else round(night_share, 4),
        correlations={k: round(v, 4) for k, v in corrs.items()},
    )
