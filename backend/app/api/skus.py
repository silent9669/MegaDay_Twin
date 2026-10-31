"""SKU router (API-04, 08 §3.4–3.5): list for "Tổng quan ca", detail, hourly panel,
forecast, demand group, model scorecard."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

import duckdb
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.api.common import MAX_LIMIT, compute_state, data_as_of, paginate
from app.api.deps import Conn, Operator, Viewer, audit
from app.api.schemas import Envelope, LocalDatetime, Meta, Page, SourceRef
from app.api.snapshot import (
    AD_STATUS_VI,
    ForecastView,
    SkuRow,
    campaign_status,
    detail,
    latest_forecasts,
    sku_rows,
)
from app.core.errors import NotFound, ValidationFailed
from app.core.timeutil import floor_hour, iso_local, to_utc, utcnow
from app.forecast.base import DEMAND_GROUPS
from app.forecast.demand import demand_summary
from app.forecast.service import set_demand_group
from app.warehouse.repo import fetch_all

router = APIRouter(tags=["skus"])


# ---------------------------------------------------------------------- models
class Interval(BaseModel):
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None


class CoverHours(Interval):
    horizon_hours: float | None = None  # forecast hours available (None values = beyond)


class CampaignRef(BaseModel):
    id: str
    status: str | None = None  # Vietnamese label
    status_code: str | None = None  # running|paused|budget_exhausted|unknown


class RecRef(BaseModel):
    recommendation_id: str
    status: str
    best: dict[str, Any] | None
    confidence: str
    scenario_id: str | None = None
    created_at: LocalDatetime | None = None


class SkuSummary(BaseModel):
    sku: str
    name: str
    variant: str
    product_id: str
    is_bundle: bool
    demand_group: str | None
    stock_available: int | None
    hours_of_cover: CoverHours | None
    sold_today_units: int
    sold_3h_units: int
    deviation: str | None  # on_track|below_forecast|above_forecast|None (no forecast today)
    campaign: CampaignRef | None
    recommendation: RecRef | None
    open_alerts: int
    priority_score: float
    confidence: str | None
    forecast_id: str | None
    compute_state: str | None
    computed_at: LocalDatetime | None


class SkuDetail(SkuSummary):
    demand: dict[str, Any] = Field(default_factory=dict)
    incoming: list[dict[str, Any]]
    price_list_vnd: int | None
    price_effective_vnd: int | None
    sold_last_hour_units: int
    demand_group_info: dict[str, Any]
    data_labels: list[dict[str, Any]]
    campaign_detail: dict[str, Any] | None
    components_of: list[dict[str, Any]]
    used_in_combos: list[str]
    commitments: list[dict[str, Any]]
    cost: dict[str, Any] | None
    contribution_per_unit_vnd: int | None
    as_of: LocalDatetime | None
    source: SourceRef


def _summary(row: SkuRow, status: dict[str, dict[str, Any]]) -> SkuSummary:
    camp = None
    if row.campaign_id:
        st = status.get(row.campaign_id)
        code = str(st["status"]) if st else None
        camp = CampaignRef(
            id=row.campaign_id,
            status=AD_STATUS_VI.get(code, code) if code else None,
            status_code=code,
        )
    rec = row.recommendation
    return SkuSummary(
        sku=row.sku,
        name=row.name,
        variant=row.variant,
        product_id=row.product_id,
        is_bundle=row.is_bundle,
        demand_group=row.demand_group,
        stock_available=row.stock_available,
        hours_of_cover=CoverHours(**row.hours_of_cover) if row.hours_of_cover else None,
        sold_today_units=row.sold_today_units,
        sold_3h_units=row.sold_3h_units,
        deviation=row.deviation,
        campaign=camp,
        recommendation=RecRef(**{k: rec[k] for k in RecRef.model_fields if k in rec})
        if rec
        else None,
        open_alerts=row.open_alerts,
        priority_score=row.priority_score,
        confidence=row.confidence,
        forecast_id=row.forecast.forecast_id if row.forecast else None,
        compute_state=row.compute_state,
        computed_at=row.computed_at,
    )


SortKey = Literal["priority", "sku", "cover", "sold_today"]


@router.get("/skus", response_model=Page[SkuSummary], summary="Danh sách SKU (Tổng quan ca)")
def list_skus(
    conn: Conn,
    _user: Viewer,
    sort: SortKey = "priority",
    group: str | None = None,
    campaign_id: str | None = None,
    has_alert: bool | None = None,
    has_recommendation: bool | None = None,
    q: Annotated[str | None, Query(description="Tìm theo mã hoặc tên")] = None,
    include_bundles: bool = True,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    cursor: str | None = None,
) -> Page[SkuSummary]:
    rows, ck = sku_rows(conn)
    if group:
        rows = [r for r in rows if r.demand_group == group]
    if campaign_id:
        rows = [r for r in rows if r.campaign_id == campaign_id]
    if has_alert is not None:
        rows = [r for r in rows if (r.open_alerts > 0) == has_alert]
    if has_recommendation is not None:
        rows = [r for r in rows if (r.recommendation is not None) == has_recommendation]
    if not include_bundles:
        rows = [r for r in rows if not r.is_bundle]
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in r.sku.lower() or needle in r.name.lower()]
    if sort == "priority":
        rows.sort(key=lambda r: (-r.priority_score, r.sku))
    elif sort == "cover":
        rows.sort(
            key=lambda r: (
                ((r.hours_of_cover or {}).get("p50") is None),
                (r.hours_of_cover or {}).get("p50") or 0.0,
                r.sku,
            )
        )
    elif sort == "sold_today":
        rows.sort(key=lambda r: (-r.sold_today_units, r.sku))
    page, nxt = paginate(rows, limit, cursor)
    status = campaign_status(conn, ck.as_of)
    return Page[SkuSummary](
        data=[_summary(r, status) for r in page],
        meta=Meta(data_as_of=ck.as_of, computed_at=utcnow(), total=len(rows)),
        next_cursor=nxt,
    )


def _one(conn: duckdb.DuckDBPyConnection, sku: str) -> tuple[SkuRow, Any]:
    rows, ck = sku_rows(conn, [sku])
    if not rows:
        raise NotFound(f"Không có SKU {sku}.", sku=sku)
    return rows[0], ck


@router.get("/skus/{sku}", response_model=Envelope[SkuDetail], summary="Chi tiết một SKU")
def get_sku(sku: str, conn: Conn, _user: Viewer) -> Envelope[SkuDetail]:
    row, ck = _one(conn, sku)
    base = _summary(row, campaign_status(conn, ck.as_of)).model_dump()
    extra = detail(conn, row, ck)
    extra["demand"] = demand_summary(conn, sku)
    extra["demand_group_info"] = extra.pop("demand_group")
    src = extra.pop("source")
    data = SkuDetail(**base, **extra, as_of=ck.as_of, source=SourceRef(**src))
    sources = [SourceRef(**src, data_as_of=ck.as_of, tool="get_sku_snapshot")]
    if row.forecast is not None:
        sources.append(_forecast_source(row.forecast))
    return Envelope[SkuDetail](
        data=data,
        meta=Meta(
            data_as_of=ck.as_of,
            computed_at=row.computed_at or utcnow(),
            compute_state=row.compute_state,
            confidence=row.confidence,
            sources=sources,
        ),
    )


# ---------------------------------------------------------------------- panel
class PanelPoint(BaseModel):
    t: LocalDatetime
    units: int
    units_provisional: int
    revenue_net_vnd: int
    stock_end: int | None
    stockout: bool
    low_stock: bool
    ad_status: str | None
    ad_spend_vnd: int | None
    ad_spend_estimated: bool
    price_effective_vnd: int | None
    price_list_vnd: int | None
    event: str | None  # double_day|pre_double_day|post_double_day|payday|flash
    flags: list[str]


class PanelOut(BaseModel):
    sku: str
    grain: Literal["hour", "day"]
    start: LocalDatetime
    end: LocalDatetime
    points: list[PanelPoint]


MAX_PANEL_DAYS = 130


def _event(r: dict[str, Any]) -> str | None:
    if r["in_flash_window"]:
        return "flash"
    for k in ("is_double_day", "is_pre_double_day", "is_post_double_day", "is_payday"):
        if r[k]:
            return k.removeprefix("is_")
    return None


@router.get("/skus/{sku}/panel", response_model=Envelope[PanelOut], summary="Chuỗi theo giờ")
def get_panel(
    sku: str,
    conn: Conn,
    _user: Viewer,
    start: Annotated[datetime | None, Query(alias="start")] = None,
    end: Annotated[datetime | None, Query(alias="end")] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query(alias="to")] = None,
    grain: Literal["hour", "day"] = "hour",
) -> Envelope[PanelOut]:
    if conn.execute("SELECT 1 FROM skus WHERE sku_id = ?", [sku]).fetchone() is None:
        raise NotFound(f"Không có SKU {sku}.", sku=sku)
    as_of = data_as_of(conn)
    t1 = start or from_
    t2 = end or to
    hi = to_utc(t2) if t2 else (as_of or utcnow())
    lo = to_utc(t1) if t1 else hi - timedelta(days=7)
    if lo >= hi:
        raise ValidationFailed("start phải trước end.", start=iso_local(lo), end=iso_local(hi))
    if hi - lo > timedelta(days=MAX_PANEL_DAYS):
        raise ValidationFailed(
            f"Khoảng thời gian tối đa {MAX_PANEL_DAYS} ngày.", max_days=MAX_PANEL_DAYS
        )
    rows = fetch_all(
        conn,
        "SELECT hour_utc, day_local, units_gross, units_provisional, revenue_net, stock_end,"
        " stockout, low_stock, ad_status, ad_spend_alloc, eff_price, list_price, in_flash_window,"
        " is_double_day, is_pre_double_day, is_post_double_day, is_payday, data_flags"
        " FROM hourly_panel WHERE sku_id = ? AND hour_utc >= ? AND hour_utc < ?"
        " ORDER BY hour_utc",
        [sku, lo, hi],
    )
    points: list[PanelPoint] = []
    if grain == "hour":
        for r in rows:
            spend = r["ad_spend_alloc"]
            points.append(
                PanelPoint(
                    t=r["hour_utc"],
                    units=int(r["units_gross"]),
                    units_provisional=int(r["units_provisional"]),
                    revenue_net_vnd=int(r["revenue_net"]),
                    stock_end=r["stock_end"],
                    stockout=bool(r["stockout"]),
                    low_stock=bool(r["low_stock"]),
                    ad_status=r["ad_status"],
                    ad_spend_vnd=None if spend is None else round(float(spend)),
                    ad_spend_estimated=spend is None or r["ad_status"] == "unknown",
                    price_effective_vnd=r["eff_price"],
                    price_list_vnd=r["list_price"],
                    event=_event(r),
                    flags=list(r["data_flags"] or []),
                )
            )
    else:
        by_day: dict[date, list[dict[str, Any]]] = {}
        for r in rows:
            by_day.setdefault(r["day_local"], []).append(r)
        for _d, rs in sorted(by_day.items()):
            spends = [r["ad_spend_alloc"] for r in rs]
            known = [float(s) for s in spends if s is not None]
            last = rs[-1]
            points.append(
                PanelPoint(
                    t=rs[0]["hour_utc"],
                    units=sum(int(r["units_gross"]) for r in rs),
                    units_provisional=sum(int(r["units_provisional"]) for r in rs),
                    revenue_net_vnd=sum(int(r["revenue_net"]) for r in rs),
                    stock_end=last["stock_end"],
                    stockout=any(bool(r["stockout"]) for r in rs),
                    low_stock=bool(last["low_stock"]),
                    ad_status=last["ad_status"],
                    ad_spend_vnd=round(sum(known)) if known else None,
                    ad_spend_estimated=len(known) < len(spends),
                    price_effective_vnd=last["eff_price"],
                    price_list_vnd=last["list_price"],
                    event=next((e for e in (_event(r) for r in rs) if e), None),
                    flags=sorted({f for r in rs for f in (r["data_flags"] or [])}),
                )
            )
    return Envelope[PanelOut](
        data=PanelOut(sku=sku, grain=grain, start=lo, end=hi, points=points),
        meta=Meta(
            data_as_of=as_of,
            computed_at=utcnow(),
            compute_state="fresh",
            confidence="verified",
            sources=[
                SourceRef(
                    id=f"panel-{sku}",
                    kind="panel",
                    label=f"Bảng giờ {sku}",
                    data_as_of=as_of,
                    tool="get_sales_series",
                    href=f"/api/v1/skus/{sku}/panel",
                )
            ],
        ),
    )


# ---------------------------------------------------------------------- forecast
class HourQ(BaseModel):
    t: LocalDatetime
    mean: float
    p10: float
    p50: float
    p90: float


class ModelRef(BaseModel):
    id: str
    name: str
    version: str


class ScoreRef(BaseModel):
    wape: float | None = None
    coverage_80: float | None = None
    bias: float | None = None
    window: str | None = None  # backtest id (dd_1010, normal_roll…)
    n_points: int | None = None  # SKU-days scored
    demand_group: str | None = None
    context: str | None = None


class ForecastOut(BaseModel):
    forecast_id: str
    sku: str
    action: str
    origin: LocalDatetime
    horizon_hours: int
    hourly: list[HourQ]
    total: Interval | None  # cumulative quantiles over the returned hours (origin-aligned)
    model: ModelRef
    score: ScoreRef
    demand_group: str | None
    context: str | None
    reasons: list[str]
    confidence: str


def _forecast_source(fc: ForecastView) -> SourceRef:
    return SourceRef(
        id=fc.forecast_id,
        kind="forecast",
        label=f"Dự báo {fc.model_id} lúc {iso_local(fc.origin)}",
        data_as_of=fc.data_as_of,
        tool="get_forecast",
        href=f"/api/v1/skus/{fc.sku_id}/forecast",
    )


@router.get("/skus/{sku}/forecast", response_model=Envelope[ForecastOut], summary="Dự báo nhu cầu")
def get_forecast(
    sku: str,
    conn: Conn,
    _user: Viewer,
    horizon_hours: Annotated[int, Query(ge=1, le=336)] = 24,
    action: Literal["continue"] = "continue",
    compute: Annotated[
        bool, Query(description="Chưa có dự báo thì tính ngay (chậm ~10–25 giây)")
    ] = True,
) -> Envelope[ForecastOut]:
    if conn.execute("SELECT 1 FROM skus WHERE sku_id = ?", [sku]).fetchone() is None:
        raise NotFound(f"Không có SKU {sku}.", sku=sku)
    fc = latest_forecasts(conn, [sku]).get(sku)
    if fc is None and compute:
        from app.forecast.service import forecast_sku

        forecast_sku(conn, sku, None, max(horizon_hours, 72))
        fc = latest_forecasts(conn, [sku]).get(sku)
    if fc is None:
        raise NotFound(f"Chưa có dự báo cho {sku}.", sku=sku)
    as_of = data_as_of(conn)
    p = fc.payload
    hours = fc.hours
    start = floor_hour(as_of) if as_of and as_of > fc.origin else fc.origin
    idx0 = next((i for i, h in enumerate(hours) if h >= start), len(hours))
    sel = range(idx0, min(len(hours), idx0 + horizon_hours))
    hourly = [
        HourQ(
            t=hours[i],
            mean=float(p["mean"][i]),
            p10=float(p["p10"][i]),
            p50=float(p["p50"][i]),
            p90=float(p["p90"][i]),
        )
        for i in sel
    ]
    total = None
    if idx0 == 0 and hourly:
        k = len(hourly) - 1
        total = Interval(p10=p["cum_p10"][k], p50=p["cum_p50"][k], p90=p["cum_p90"][k])
    vs = p.get("validated_scope") or {}
    name, _, version = fc.model_id.partition("@")
    return Envelope[ForecastOut](
        data=ForecastOut(
            forecast_id=fc.forecast_id,
            sku=sku,
            action=action,
            origin=fc.origin,
            horizon_hours=len(hourly),
            hourly=hourly,
            total=total,
            model=ModelRef(id=fc.model_id, name=name, version=version),
            score=ScoreRef(
                wape=vs.get("wape"),
                coverage_80=vs.get("coverage"),
                bias=vs.get("bias"),
                window=vs.get("backtest"),
                n_points=vs.get("n_sku_days"),
                demand_group=vs.get("demand_group"),
                context=vs.get("context"),
            ),
            demand_group=p.get("demand_group"),
            context=p.get("context"),
            reasons=list(p.get("reasons") or []),
            confidence=fc.confidence,
        ),
        meta=Meta(
            data_as_of=fc.data_as_of,
            computed_at=fc.created_at,
            compute_state=compute_state(fc.data_as_of, as_of),
            confidence=fc.confidence,
            sources=[_forecast_source(fc)],
        ),
    )


# ---------------------------------------------------------------------- demand group
class DemandGroupIn(BaseModel):
    value: str | None = Field(description="Nhóm nhu cầu; null = trả về phân nhóm tự động")
    reason: str | None = None


class DemandGroupOut(BaseModel):
    sku: str
    value: str | None
    source: str
    set_by: str
    set_at: LocalDatetime


def _set_group(
    sku: str, body: DemandGroupIn, conn: duckdb.DuckDBPyConnection, user: Any, request: Request
) -> Envelope[DemandGroupOut]:
    before = conn.execute(
        "SELECT demand_group, demand_group_source FROM skus WHERE sku_id = ?", [sku]
    ).fetchone()
    if before is None:
        raise NotFound(f"Không có SKU {sku}.", sku=sku)
    if body.value is not None and body.value not in DEMAND_GROUPS:
        raise ValidationFailed(
            f"Nhóm nhu cầu không hợp lệ: {body.value}.", allowed=list(DEMAND_GROUPS)
        )
    set_demand_group(conn, sku, body.value)
    audit(
        conn,
        request,
        user,
        "sku.demand_group",
        "sku",
        sku,
        before={"value": before[0], "source": before[1]},
        after={"value": body.value, "reason": body.reason},
    )
    now = utcnow()
    return Envelope[DemandGroupOut](
        data=DemandGroupOut(
            sku=sku,
            value=body.value,
            source="user" if body.value is not None else "auto",
            set_by=user.id,
            set_at=now,
        ),
        meta=Meta(computed_at=now),
    )


@router.put(
    "/skus/{sku}/demand-group", response_model=Envelope[DemandGroupOut], summary="Sửa nhóm nhu cầu"
)
def put_demand_group(
    sku: str, body: DemandGroupIn, conn: Conn, user: Operator, request: Request
) -> Envelope[DemandGroupOut]:
    return _set_group(sku, body, conn, user, request)


@router.patch(
    "/skus/{sku}/demand-group",
    response_model=Envelope[DemandGroupOut],
    summary="Sửa nhóm nhu cầu (alias PATCH của 08)",
)
def patch_demand_group(
    sku: str, body: DemandGroupIn, conn: Conn, user: Operator, request: Request
) -> Envelope[DemandGroupOut]:
    return _set_group(sku, body, conn, user, request)


# ---------------------------------------------------------------------- scorecard
class ScoreRow(BaseModel):
    model_id: str
    backtest: str
    demand_group: str
    context: str
    n_skus: int | None
    n_sku_days: int | None
    wape: float | None
    wape_floor: float | None
    bias: float | None
    coverage_80: float | None
    pinball_50: float | None
    is_champion: bool
    created_at: LocalDatetime


def _scores(
    conn: duckdb.DuckDBPyConnection,
    group: str | None,
    window: str | None,
    champion_only: bool,
) -> Page[ScoreRow]:
    cond: list[str] = ["TRUE"]
    params: list[Any] = []
    if group:
        cond.append("demand_group = ?")
        params.append(group)
    if window:
        cond.append("backtest = ?")
        params.append(window)
    if champion_only:
        cond.append("is_champion")
    rows = fetch_all(
        conn,
        "SELECT model_id, backtest, demand_group, context, n_skus, n_sku_days, wape, wape_floor,"
        " bias, coverage, pinball_50, is_champion, created_at FROM model_scores"
        f" WHERE {' AND '.join(cond)}"
        " QUALIFY row_number() OVER (PARTITION BY model_id, backtest, demand_group, context"
        " ORDER BY created_at DESC) = 1 ORDER BY backtest, demand_group, context, wape",
        params,
    )
    out = [
        ScoreRow(**{**r, "coverage_80": r["coverage"], "is_champion": bool(r["is_champion"])})
        for r in rows
    ]
    latest = max((r.created_at for r in out), default=None)
    return Page[ScoreRow](
        data=out, meta=Meta(computed_at=latest, compute_state="fresh" if out else None)
    )


@router.get("/forecast/scores", response_model=Page[ScoreRow], summary="Phiếu điểm dự báo")
def forecast_scores(
    conn: Conn,
    _user: Viewer,
    group: str | None = None,
    window: str | None = None,
    champion_only: bool = False,
) -> Page[ScoreRow]:
    return _scores(conn, group, window, champion_only)


@router.get(
    "/models/scores", response_model=Page[ScoreRow], summary="Phiếu điểm dự báo (tên của 08)"
)
def model_scores(
    conn: Conn,
    _user: Viewer,
    group: str | None = None,
    window: str | None = None,
    champion_only: bool = False,
) -> Page[ScoreRow]:
    return _scores(conn, group, window, champion_only)
