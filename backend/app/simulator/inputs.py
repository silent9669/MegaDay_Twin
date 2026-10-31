"""Everything the simulator needs for one campaign at ``start``, read from DuckDB once.

Only data **before** ``start`` is used (stock, ads, restock plans seen before ``start``),
so a run at a past instant behaves as if it happened then. ``SimInputs`` is plain numpy
data: the engine never touches the DB, which keeps it fast and testable.

Sources (05 §2, §5): ``campaigns``/``campaign_skus``/``bundle_components``, ``forecasts``
(champion forecast at ``start``; computed with ``app.forecast.service`` when missing),
``cost_items`` + ``price_events`` + ``sale_commitments`` (contribution, flash windows,
reserve, ``campaign_must_run``), ``hourly_panel`` (stock at ``start``, flash sales already
done), ``restock_plans`` ("Đang về"), ``ad_hourly`` (budget, spend profile, spend so far,
adstock), ``iroas_estimates`` (posterior grid; prior when absent).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import duckdb
import numpy as np
import numpy.typing as npt

from app.ads_effect.schemas import IroasEstimate
from app.core.errors import NotFound, ValidationFailed
from app.core.timeutil import (
    floor_hour,
    is_double_day,
    is_post_double_day,
    is_pre_double_day,
    local_date,
    local_day_bounds_utc,
    to_local,
)
from app.economics.contribution import ContributionTable, load_contributions
from app.economics.goals import Goal, validate_focus_sku
from app.economics.parameters import ParamSet
from app.forecast.base import ForecastResult
from app.simulator.constraints import UNLIMITED
from app.simulator.policies import SpendContext
from app.simulator.schemas import (
    EstimatePosterior,
    IroasPosterior,
    Label,
    PriorIroas,
    as_posterior,
    min_label,
)

FloatArr = npt.NDArray[np.float64]
BoolArr = npt.NDArray[np.bool_]
IntArr = npt.NDArray[np.int64]

MAX_HORIZON = 72
STALE_HOURS = 24  # orders/stock older than this at ``start`` → data label scenario
PROFILE_DAYS = 7
SPEND_SCALE_DAYS = 28
ADSTOCK_LOOKBACK_H = 48


@dataclass
class SkuInput:
    sku_id: str
    is_bundle: bool
    in_campaign: bool  # False: combo component outside the campaign (no ads effect)
    components: list[tuple[str, int]]
    lam_sq: FloatArr  # [T] status-quo demand (forecast mean)
    k: float
    sigma: float
    forecast_id: str | None
    label: Label
    m: FloatArr  # [T] contribution per unit
    price: FloatArr  # [T] effective price
    leftover_value: float  # V, net of cost like m (0 for combos)
    stock0: float  # stock at start (UNLIMITED when unknown; unused for combos)
    safety: float
    reserve: float  # own flash reserve (units of this SKU / combo)
    restock_plans: list[tuple[date, int]]  # (planned VN date, qty)
    restock_delay: float  # scenario delay in days (per SKU)
    revenue_share: float = 0.0  # share of expected campaign revenue (λ_sq · price)
    unit_cost: float = 0.0  # lower bound of V (V = −cost: the leftover is worthless)


@dataclass
class SimInputs:
    campaign_id: str
    campaign_name: str
    start: datetime
    hours: list[datetime]  # [T] UTC
    hour_local: IntArr  # [T]
    day_index: IntArr  # [T]
    days: list[date]
    skus: list[SkuInput]
    spend: SpendContext
    adstock0: float
    iroas: IroasPosterior
    iroas_source: str  # estimate|prior
    flash: BoolArr  # [T]
    must_run: BoolArr  # [T]
    must_run_windows: list[tuple[int, int]]  # VN hours [start, end) of the commitments
    reserve_active: BoolArr  # [T]
    flash_sold_before: dict[str, float]  # stock units of each SKU sold in flash before start
    restock_hour: int
    rank_loss: float
    penalty: float
    goal: Goal
    params: ParamSet
    data_as_of: datetime | None
    panel_version: str | None
    data_label: Label
    data_reasons: list[str] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)
    economics: ContributionTable | None = None
    main_sku: str | None = None
    budget_source: str = ""

    @property
    def T(self) -> int:  # noqa: N802 - horizon length, plan notation
        return len(self.hours)

    @property
    def forecast_ids(self) -> list[str]:
        return sorted(s.forecast_id for s in self.skus if s.forecast_id)

    @property
    def stock_skus(self) -> list[SkuInput]:
        return [s for s in self.skus if not s.is_bundle]

    @property
    def combos(self) -> list[SkuInput]:
        return [s for s in self.skus if s.is_bundle]

    def sku(self, sku_id: str) -> SkuInput:
        for s in self.skus:
            if s.sku_id == sku_id:
                return s
        raise KeyError(sku_id)

    def restock_t(self, delay_days: float | None = None) -> int | None:
        """First horizon hour with a campaign SKU arriving (for ``pause_until_restock``)."""
        best: int | None = None
        for s in self.stock_skus:
            if not s.in_campaign:
                continue
            for t, _q in self.arrivals(s, delay_days):
                best = t if best is None else min(best, t)
        return best

    def arrivals(self, s: SkuInput, delay_days: float | None = None) -> list[tuple[int, int]]:
        """``[(horizon index, qty)]`` of the planned restocks of ``s`` inside the horizon;
        index ``T`` (or more) means after the horizon."""
        delay = s.restock_delay if delay_days is None else delay_days
        out = []
        for planned, qty in s.restock_plans:
            local = datetime(
                planned.year, planned.month, planned.day, tzinfo=to_local(self.start).tzinfo
            )
            at = local + timedelta(hours=self.restock_hour) + timedelta(days=delay)
            t = math.ceil((at - self.start).total_seconds() / 3600)
            out.append((max(t, 0), qty))
        return out

    def restock_matrix(self, delay_days: float | None = None) -> FloatArr:
        """``[J, T]`` arrivals for the stock-holding SKUs."""
        js = self.stock_skus
        out = np.zeros((len(js), self.T))
        for j, s in enumerate(js):
            for t, qty in self.arrivals(s, delay_days):
                if t < self.T:
                    out[j, t] += qty
        return out


# ---------------------------------------------------------------------- helpers
def _scalar(conn: duckdb.DuckDBPyConnection, sql: str, args: Sequence[object] = ()) -> object:
    rows = conn.execute(sql, list(args)).fetchall()
    return rows[0][0] if rows else None


def data_as_of(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    v = _scalar(
        conn,
        "SELECT max(as_of) FROM ingest_batches WHERE source_type IN ('orders', 'inventory')"
        " AND status <> 'failed'",
    )
    return v if isinstance(v, datetime) else None


def default_start(conn: duckdb.DuckDBPyConnection) -> datetime:
    """Next whole hour after the data mark (``as_of`` 01:00 = data up to 00:59 → 01:00)."""
    as_of = data_as_of(conn)
    if as_of is None:
        raise ValidationFailed("Chưa có dữ liệu đơn hàng / tồn kho để mô phỏng.")
    fl = floor_hour(as_of)
    return fl if fl == as_of else fl + timedelta(hours=1)


def day_kind(d: date) -> str:
    if is_double_day(d):
        return "double_day"
    if is_pre_double_day(d) or is_post_double_day(d):
        return "near_double_day"
    return "normal"


def load_forecasts(
    conn: duckdb.DuckDBPyConnection, sku_ids: Sequence[str], start: datetime, horizon: int
) -> dict[str, ForecastResult]:
    """Latest stored forecast with ``origin = start`` covering the horizon; missing ones are
    computed with the champion models (``forecast_skus``, persisted)."""
    from app.forecast.demand import forecast_signature

    signature = forecast_signature(conn, start)
    out: dict[str, ForecastResult] = {}
    rows = conn.execute(
        "SELECT sku_id, payload FROM forecasts WHERE list_contains(?, sku_id) AND origin = ?"
        " AND horizon_hours >= ? QUALIFY row_number() OVER (PARTITION BY sku_id"
        " ORDER BY created_at DESC) = 1",
        [list(sku_ids), start, horizon],
    ).fetchall()
    for sku, payload in rows:
        result = ForecastResult.model_validate_json(payload)
        if result.selection_signature == signature:
            out[str(sku)] = result
    missing = [s for s in sku_ids if s not in out]
    if missing:
        from app.forecast import service

        for r in service.forecast_skus(conn, missing, start, max(horizon, 24)):
            out[r.sku_id] = r
    return out


def _ad_rows(
    conn: duckdb.DuckDBPyConnection, campaign_id: str, since: datetime, until: datetime
) -> list[tuple[datetime, str, int | None, int | None]]:
    rows = conn.execute(
        "SELECT hour_utc, status, daily_budget, spend FROM ad_hourly WHERE campaign_id = ?"
        " AND hour_utc >= ? AND hour_utc < ? ORDER BY hour_utc",
        [campaign_id, since, until],
    ).fetchall()
    return [(r[0], str(r[1]), r[2], r[3]) for r in rows]


@dataclass
class AdHistory:
    """Campaign ads before ``start``: per VN day ``{hour: spend}`` (known hours only) and
    the budget shown that day."""

    spend: dict[date, dict[int, float]]
    budget: dict[date, float]
    running_spend: list[float]
    base_budget: float

    def profile(self, d: date, before: date) -> tuple[FloatArr, str]:
        """``[24]`` fraction of budget per VN hour for a day like ``d``."""
        kind = day_kind(d)
        if kind == "double_day":
            cands = [x for x in sorted(self.spend, reverse=True) if x < before and is_double_day(x)]
            cands = [x for x in cands if len(self.spend[x]) >= 20][:1]
            src = f"ngày đôi {cands[0]:%d/%m}" if cands else ""
        else:
            cands = [
                x
                for x in sorted(self.spend, reverse=True)
                if x < before and day_kind(x) == "normal" and len(self.spend[x]) == 24
            ][:PROFILE_DAYS]
            src = f"{len(cands)} ngày thường gần nhất" if cands else ""
        if not cands:
            return np.full(24, 1 / 24), "chia đều (chưa có lịch sử chi)"
        prof = np.zeros(24)
        for x in cands:
            b = self.budget.get(x) or self.base_budget
            for h, s in self.spend[x].items():
                prof[h] += s / b / len(cands)
        return prof, src

    def budget_for(self, d: date, before: date) -> float:
        if d in self.budget:
            return self.budget[d]
        kind = day_kind(d)
        if kind == "double_day":
            prev = [x for x in sorted(self.budget, reverse=True) if x < before and is_double_day(x)]
            if prev:
                return self.budget[prev[0]]
        prev = [
            x for x in sorted(self.budget, reverse=True) if x < before and day_kind(x) == "normal"
        ]
        return self.budget[prev[0]] if prev else self.base_budget


def ad_history(
    conn: duckdb.DuckDBPyConnection, campaign_id: str, start: datetime, base_budget: float
) -> AdHistory:
    since = start - timedelta(days=70)
    spend: dict[date, dict[int, float]] = {}
    budget: dict[date, float] = {}
    running: list[float] = []
    lim = start - timedelta(days=SPEND_SCALE_DAYS)
    for hour, status, bud, sp in _ad_rows(conn, campaign_id, since, start):
        loc = to_local(hour)
        d = loc.date()
        if bud is not None:
            budget[d] = max(budget.get(d, 0.0), float(bud))
        if sp is None or status == "unknown":
            continue
        spend.setdefault(d, {})[loc.hour] = float(sp)
        if status == "running" and sp > 0 and hour >= lim:
            running.append(float(sp))
    return AdHistory(spend, budget, running, base_budget)


def load_iroas(
    conn: duckdb.DuckDBPyConnection, campaign_id: str, spend_scale: float
) -> tuple[IroasPosterior, str]:
    """Latest ``IroasEstimate`` of the campaign (``app.ads_effect.iroas.load_estimate``)
    wrapped as an :class:`EstimatePosterior`; the prior (label ``scenario``) when the
    campaign has no estimate yet."""
    try:
        from app.ads_effect.iroas import load_estimate

        est = load_estimate(conn, campaign_id)
    except (duckdb.Error, ImportError, ValueError, KeyError):
        est = None
    if est is not None and est.posterior:
        return EstimatePosterior(est), "estimate"
    return PriorIroas(campaign_id=campaign_id, spend_scale=spend_scale), "prior"


# ---------------------------------------------------------------------- main loader
def resolve_horizon(goal: Goal, start: datetime, horizon_hours: int | None) -> int:
    if horizon_hours is not None:
        return min(int(horizon_hours), MAX_HORIZON)
    h = math.ceil((goal.deadline - start).total_seconds() / 3600)
    if h <= 0:
        raise ValidationFailed("Hạn của mục tiêu đã qua so với thời điểm mô phỏng.")
    return min(h, MAX_HORIZON)


def load_inputs(
    conn: duckdb.DuckDBPyConnection,
    *,
    campaign_id: str,
    goal: Goal,
    params: ParamSet,
    start: datetime | None = None,
    horizon_hours: int | None = None,
    forecasts: Mapping[str, ForecastResult] | None = None,
    iroas: IroasPosterior | IroasEstimate | None = None,
) -> SimInputs:
    validate_focus_sku(conn, "campaign", campaign_id, goal.params)
    camp = conn.execute(
        "SELECT name, daily_budget FROM campaigns WHERE campaign_id = ?", [campaign_id]
    ).fetchone()
    if camp is None:
        raise NotFound(f"Không tìm thấy chiến dịch {campaign_id}.", campaign_id=campaign_id)
    camp_skus = [
        str(r[0])
        for r in conn.execute(
            "SELECT sku_id FROM campaign_skus WHERE campaign_id = ? ORDER BY sku_id", [campaign_id]
        ).fetchall()
    ]
    if not camp_skus:
        raise ValidationFailed(f"Chiến dịch {campaign_id} chưa có SKU nào.")
    bundles: dict[str, list[tuple[str, int]]] = {}
    for b, c, q in conn.execute(
        "SELECT bundle_sku_id, component_sku_id, qty FROM bundle_components"
        " WHERE list_contains(?, bundle_sku_id) ORDER BY 1, 2",
        [camp_skus],
    ).fetchall():
        bundles.setdefault(str(b), []).append((str(c), int(q)))
    flagged_bundle = {
        str(r[0])
        for r in conn.execute(
            "SELECT sku_id FROM skus WHERE is_bundle AND list_contains(?, sku_id)", [camp_skus]
        ).fetchall()
    }
    external = sorted({c for comps in bundles.values() for c, _ in comps} - set(camp_skus))
    all_skus = camp_skus + external

    start = start or default_start(conn)
    if start.tzinfo is None:
        raise ValidationFailed("start phải có múi giờ")
    start = floor_hour(start)
    T = resolve_horizon(goal, start, horizon_hours)  # noqa: N806
    hours = [start + timedelta(hours=i) for i in range(T)]
    hour_local = np.array([to_local(h).hour for h in hours], dtype=np.int64)
    local_days = [local_date(h) for h in hours]
    days = sorted(set(local_days))
    day_index = np.array([days.index(d) for d in local_days], dtype=np.int64)
    day0 = days[0]

    reasons: list[str] = []
    blocked: list[str] = []
    label: Label = "verified"
    as_of = data_as_of(conn)
    if as_of is None or as_of < start - timedelta(hours=STALE_HOURS):
        label = "scenario"
        reasons.append("Dữ liệu đơn/tồn cũ hơn 24 giờ so với thời điểm mô phỏng")
    panel_version = _scalar(conn, "SELECT max(panel_version) FROM hourly_panel")

    fee = params.num("platform_fee_rate")
    econ = load_contributions(conn, all_skus, days, start, fee_rate=fee)
    if econ.blocked:
        blocked.extend(econ.reasons())

    fc = dict(forecasts) if forecasts is not None else {}
    missing_fc = [s for s in all_skus if s not in fc]
    if missing_fc and not blocked:
        fc.update(load_forecasts(conn, missing_fc, start, T))

    # stock at start (panel), flash sales already done today
    stock_rows = conn.execute(
        "SELECT sku_id, hour_utc, stock_begin, stock_end FROM hourly_panel"
        " WHERE list_contains(?, sku_id) AND hour_utc IN (?, ?)",
        [all_skus, start, start - timedelta(hours=1)],
    ).fetchall()
    stock_at: dict[str, float] = {}
    for sku, hour, sb, se in sorted(stock_rows, key=lambda r: r[1]):
        if hour == start and sb is not None:
            stock_at[str(sku)] = float(sb)
        elif hour < start and se is not None:
            stock_at[str(sku)] = float(se)
    d0s, _ = local_day_bounds_utc(day0)

    # sale commitments over the horizon days
    com = conn.execute(
        "SELECT kind, sku_id, campaign_id, value_int, window_start, window_end, day_local"
        " FROM sale_commitments WHERE day_local >= ? AND day_local <= ?",
        [days[0], days[-1]],
    ).fetchall()
    hours_arr = np.array([h.timestamp() for h in hours])
    flash = np.zeros(T, dtype=np.bool_)
    must_run = np.zeros(T, dtype=np.bool_)
    must_run_windows: set[tuple[int, int]] = set()
    reserve: dict[str, float] = {}
    reserve_day: date | None = None
    last_flash_end: dict[date, float] = {}
    for kind, sku, cid, val, ws, we, dl in com:
        if kind in ("flash_window", "campaign_must_run") and ws is not None and we is not None:
            inside = (hours_arr >= ws.timestamp()) & (hours_arr < we.timestamp())
            if kind == "flash_window" and (sku is None or sku in all_skus):
                flash |= inside
                last_flash_end[dl] = max(last_flash_end.get(dl, 0.0), we.timestamp())
            if kind == "campaign_must_run" and cid == campaign_id:
                must_run |= inside
                if inside.any():
                    a, b = to_local(ws), to_local(we)
                    end = 24 if b.date() > a.date() and b.hour == 0 else b.hour
                    must_run_windows.add((a.hour, end))
        if (
            kind == "flash_reserve"
            and sku in all_skus
            and val is not None
            and (reserve_day is None or dl == reserve_day)
        ):
            reserve_day = dl
            reserve[str(sku)] = float(val)
    reserve_active = np.zeros(T, dtype=np.bool_)
    flash_sold: dict[str, float] = {}
    if reserve_day is not None and reserve_day in last_flash_end:
        on_day = np.array([d == reserve_day for d in local_days])
        reserve_active = on_day & (hours_arr < last_flash_end[reserve_day])
        if reserve_day == day0:
            rows = conn.execute(
                "SELECT sku_id, sum(units_gross) FROM hourly_panel WHERE list_contains(?, sku_id)"
                " AND in_flash_window AND hour_utc >= ? AND hour_utc < ? GROUP BY sku_id",
                [all_skus, d0s, start],
            ).fetchall()
            for sku, u in rows:
                comps = bundles.get(str(sku))
                for c, q in comps or [(str(sku), 1)]:
                    flash_sold[c] = flash_sold.get(c, 0.0) + float(u or 0) * q

    # restock plans seen before start
    plan_rows = conn.execute(
        "SELECT sku_id, qty_incoming, planned_date FROM restock_plans"
        " WHERE list_contains(?, sku_id) AND seen_at < ?"
        " QUALIFY row_number() OVER (PARTITION BY sku_id ORDER BY seen_at DESC) = 1",
        [all_skus, start],
    ).fetchall()
    arrived_today = {
        str(r[0])
        for r in conn.execute(
            "SELECT sku_id FROM hourly_panel WHERE list_contains(?, sku_id) AND hour_utc >= ?"
            " AND hour_utc < ? AND restock_arrived > 0",
            [all_skus, d0s, start],
        ).fetchall()
    }
    plans: dict[str, list[tuple[date, int]]] = {}
    for sku, qty, planned in plan_rows:
        if planned is None or planned < day0 or int(qty or 0) <= 0:
            continue
        if planned == day0 and str(sku) in arrived_today:
            continue
        plans.setdefault(str(sku), []).append((planned, int(qty)))

    # ads
    base_budget = float(camp[1])
    hist = ad_history(conn, campaign_id, start, base_budget)
    profiles = []
    srcs = []
    for d in days:
        p, src = hist.profile(d, day0 if d == day0 else d)
        profiles.append(p)
        srcs.append(src)
    budgets = np.array([hist.budget_for(d, day0) for d in days])
    known_today = hist.spend.get(day0, {})
    spent = 0.0
    for h in range(to_local(start).hour):
        spent += known_today.get(h, float(budgets[0] * profiles[0][h]))
    a0 = 0.0
    spend_scale = (
        float(np.mean(hist.running_spend)) if hist.running_spend else max(base_budget / 24, 1.0)
    )
    if iroas is None:
        post, iroas_src = load_iroas(conn, campaign_id, spend_scale)
    else:
        post = as_posterior(iroas)
        iroas_src = "prior" if post.estimate_id is None else "estimate"
    iroas_post = post
    for k in range(ADSTOCK_LOOKBACK_H, 0, -1):
        ht = to_local(start - timedelta(hours=k))
        d = ht.date()
        known = hist.spend.get(d, {})
        if ht.hour in known:
            s = known[ht.hour]
        else:
            prof, _ = hist.profile(d, d)
            s = float(hist.budget_for(d, d) * prof[ht.hour])
        a0 = s + iroas_post.delta * a0
    if not hist.spend or max(hist.spend) < day0 - timedelta(days=1):
        label = "scenario"
        reasons.append("Chưa có số liệu ads trong 24 giờ gần nhất")
    spend_ctx = SpendContext(
        hour_local=hour_local,
        day_index=day_index,
        budget_day=budgets,
        profile=np.vstack(profiles),
        spent_start_day=spent,
        restock_t=None,
    )

    # SKUs
    restock_hour = int(params.num("restock_assumed_hour"))
    sku_inputs: list[SkuInput] = []
    for sku in all_skus:
        is_b = sku in bundles or sku in flagged_bundle
        f = fc.get(sku)
        if f is None:
            lam = np.zeros(T)
            k_, sig, fid, flab = 3.0, 0.5, None, "scenario"
            if not blocked:
                reasons.append(f"Không có dự báo cho {sku}")
        else:
            lam = np.asarray(f.mean[:T], dtype=np.float64)
            if len(lam) < T:
                lam = np.concatenate([lam, np.full(T - len(lam), lam[-1] if len(lam) else 0.0)])
            k_, sig, fid, flab = f.dispersion_k, f.level_sigma, f.forecast_id, f.confidence
        m = np.zeros(T)
        price = np.zeros(T)
        if not blocked:
            for t, d in enumerate(local_days):
                e = econ.get(sku, d)
                m[t], price[t] = e.m, e.eff_price
        # V is net of cost like m (05 §3/§4): default 0 = sold later at break-even
        v = 0.0 if is_b else params.num("leftover_value_per_unit", sku, default=0.0)
        st = stock_at.get(sku)
        if st is None and not is_b:
            st = UNLIMITED
            reasons.append(f"Không có số tồn của {sku}: coi như không giới hạn")
            label = min_label(label, "reference")
        sku_inputs.append(
            SkuInput(
                sku_id=sku,
                is_bundle=is_b,
                in_campaign=sku in camp_skus,
                components=bundles.get(sku, []),
                lam_sq=lam,
                k=float(k_),
                sigma=float(sig),
                forecast_id=fid,
                label=flab,  # type: ignore[arg-type]
                m=m,
                price=price,
                leftover_value=float(v),
                unit_cost=float(econ.unit_cost.get(sku, 0) or 0),
                stock0=float(st) if st is not None else 0.0,
                safety=0.0 if is_b else params.num("safety_stock", sku),
                reserve=reserve.get(sku, 0.0),
                restock_plans=plans.get(sku, []),
                restock_delay=params.num("restock_delay_days", sku),
            )
        )
    for si in sku_inputs:
        if si.is_bundle and not si.components:
            reasons.append(f"Combo {si.sku_id} chưa khai báo thành phần: không trừ tồn")
            label = min_label(label, "reference")
    rev = {x.sku_id: float(np.sum(x.lam_sq * x.price)) for x in sku_inputs if x.in_campaign}
    tot = sum(rev.values()) or 1.0
    for si in sku_inputs:
        si.revenue_share = rev.get(si.sku_id, 0.0) / tot
    singles = [x for x in sku_inputs if x.in_campaign and not x.is_bundle]
    main = max(singles or sku_inputs, key=lambda x: rev.get(x.sku_id, 0.0)).sku_id

    inp = SimInputs(
        campaign_id=campaign_id,
        campaign_name=str(camp[0]),
        start=start,
        hours=hours,
        hour_local=hour_local,
        day_index=day_index,
        days=days,
        skus=sku_inputs,
        spend=spend_ctx,
        adstock0=a0,
        iroas=iroas_post,
        iroas_source=iroas_src,
        flash=flash,
        must_run=must_run,
        must_run_windows=sorted(must_run_windows),
        reserve_active=reserve_active,
        flash_sold_before=flash_sold,
        restock_hour=restock_hour,
        rank_loss=params.num("rank_loss_after_pause"),
        penalty=params.num("stockout_penalty_per_unit"),
        goal=goal,
        params=params,
        data_as_of=as_of,
        panel_version=str(panel_version) if panel_version is not None else None,
        data_label=label,
        data_reasons=reasons,
        blocked_reasons=blocked,
        economics=econ,
        main_sku=main,
        budget_source="; ".join(sorted(set(x for x in srcs if x))),
    )
    inp.spend = SpendContext(
        hour_local=hour_local,
        day_index=day_index,
        budget_day=budgets,
        profile=np.vstack(profiles),
        spent_start_day=spent,
        restock_t=inp.restock_t(),
    )
    return inp
