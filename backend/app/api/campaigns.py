"""Campaign router (API-04, 08 §3.6): list, detail (names, SKUs, selling price for the
leftover-value slider, budget, commitments, active goal), iROAS estimate."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

import duckdb
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.ads_effect.iroas import load_estimate
from app.ads_effect.schemas import IroasEstimate
from app.api.common import MAX_LIMIT, compute_state, data_as_of, paginate, today_bounds
from app.api.deps import Conn, Viewer
from app.api.economics import goal_label
from app.api.schemas import Envelope, LocalDatetime, Meta, Page, SourceRef
from app.api.snapshot import (
    AD_STATUS_VI,
    active_recommendations,
    campaign_status,
    commitments,
)
from app.core.errors import NotFound
from app.core.timeutil import local_date, utcnow
from app.economics.goals import active_goal
from app.warehouse.repo import fetch_all

router = APIRouter(tags=["campaigns"])


class IroasBrief(BaseModel):
    estimate_id: str
    mean: float
    p10: float
    p90: float
    platform_roas: float | None
    breakeven_iroas: float | None
    confidence: str


class CampaignRecRef(BaseModel):
    """Active recommendation of the campaign (times with ``+07:00``)."""

    recommendation_id: str
    status: str
    best: dict[str, Any] | None
    confidence: str
    scenario_id: str | None = None
    created_at: LocalDatetime | None = None
    data_as_of: LocalDatetime | None = None


class CampaignSummary(BaseModel):
    campaign_id: str
    name: str
    type: str | None
    daily_budget_vnd: int
    sku_level_control: bool
    skus: list[str]
    status: str | None
    status_code: str | None
    status_at: LocalDatetime | None
    spend_today_vnd: int | None
    budget_used_pct: float | None
    iroas: IroasBrief | None
    recommendation: CampaignRecRef | None


class CampaignSku(BaseModel):
    sku: str
    name: str
    variant: str
    is_bundle: bool
    list_price_vnd: int | None
    price_effective_vnd: int | None
    stock_available: int | None
    unit_cost_vnd: int | None


class GoalBrief(BaseModel):
    goal_id: str
    kind: str
    label: str  # "Lợi nhuận tới 00:00 12/11", same as GET /goals
    deadline: LocalDatetime
    target_units: int | None
    confirmed_by: str
    version: int


class CampaignDetail(CampaignSummary):
    started_at: LocalDatetime | None
    sku_detail: list[CampaignSku]
    price_max_vnd: int | None  # max of the V slider (05 §3: V ≤ selling price)
    commitments: list[dict[str, Any]]
    goal: GoalBrief | None


def _iroas_brief(est: IroasEstimate | None) -> IroasBrief | None:
    if est is None:
        return None
    return IroasBrief(
        estimate_id=est.estimate_id,
        mean=est.iroas_mean,
        p10=est.iroas_p10,
        p90=est.iroas_p90,
        platform_roas=est.platform_roas,
        breakeven_iroas=est.breakeven_iroas,
        confidence=est.confidence,
    )


def _campaign_rows(
    conn: duckdb.DuckDBPyConnection, campaign_id: str | None = None
) -> list[CampaignSummary]:
    where = "" if campaign_id is None else " WHERE c.campaign_id = ?"
    rows = fetch_all(
        conn,
        "SELECT c.campaign_id, c.name, c.type, c.daily_budget, c.sku_level_control,"
        " list(cs.sku_id ORDER BY cs.sku_id) FILTER (WHERE cs.sku_id IS NOT NULL) AS skus"
        " FROM campaigns c LEFT JOIN campaign_skus cs USING (campaign_id)"
        + where
        + " GROUP BY ALL ORDER BY c.campaign_id",
        [] if campaign_id is None else [campaign_id],
    )
    as_of = data_as_of(conn)
    status = campaign_status(conn, as_of)
    spend: dict[str, int] = {}
    if as_of is not None:
        start, _ = today_bounds(as_of)
        for c, s in conn.execute(
            "SELECT campaign_id, sum(spend) FROM ad_hourly WHERE hour_utc >= ? AND hour_utc < ?"
            " GROUP BY 1",
            [start, as_of],
        ).fetchall():
            spend[str(c)] = int(s or 0)
    recs = active_recommendations(conn)
    out = []
    for r in rows:
        cid = str(r["campaign_id"])
        st = status.get(cid)
        code = str(st["status"]) if st else None
        budget = int((st or {}).get("daily_budget") or r["daily_budget"])
        sp = spend.get(cid)
        rec = recs.get(cid)
        out.append(
            CampaignSummary(
                campaign_id=cid,
                name=str(r["name"]),
                type=r["type"],
                daily_budget_vnd=budget,
                sku_level_control=bool(r["sku_level_control"]),
                skus=list(r["skus"] or []),
                status=AD_STATUS_VI.get(code, code) if code else None,
                status_code=code,
                status_at=st["hour_utc"] if st else None,
                spend_today_vnd=sp,
                budget_used_pct=round(100 * sp / budget, 1)
                if (sp is not None and budget)
                else None,
                iroas=_iroas_brief(load_estimate(conn, cid)),
                recommendation=CampaignRecRef.model_validate(rec) if rec else None,
            )
        )
    return out


@router.get("/campaigns", response_model=Page[CampaignSummary], summary="Danh sách chiến dịch")
def list_campaigns(
    conn: Conn,
    _user: Viewer,
    sku: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    cursor: str | None = None,
) -> Page[CampaignSummary]:
    rows = _campaign_rows(conn)
    if sku:
        rows = [r for r in rows if sku in r.skus]
    page, nxt = paginate(rows, limit, cursor)
    return Page[CampaignSummary](
        data=page, meta=Meta(data_as_of=data_as_of(conn), computed_at=utcnow()), next_cursor=nxt
    )


@router.get("/campaigns/{campaign_id}", response_model=Envelope[CampaignDetail], summary="Chi tiết")
def get_campaign(campaign_id: str, conn: Conn, _user: Viewer) -> Envelope[CampaignDetail]:
    rows = _campaign_rows(conn, campaign_id)
    if not rows:
        raise NotFound(f"Không có chiến dịch {campaign_id}.", campaign_id=campaign_id)
    base = rows[0]
    as_of = data_as_of(conn)
    started = conn.execute(
        "SELECT started_at FROM campaigns WHERE campaign_id = ?", [campaign_id]
    ).fetchone()
    lh = None
    if as_of is not None:
        lh = conn.execute(
            "SELECT max(hour_utc) FROM hourly_panel WHERE hour_utc < ?", [as_of]
        ).fetchone()
    last_hour = lh[0] if lh else None
    skus: list[CampaignSku] = []
    for r in fetch_all(
        conn,
        "SELECT s.sku_id, coalesce(p.name, s.product_id) AS name, s.variant_name, s.is_bundle,"
        " s.list_price, hp.eff_price, hp.stock_end,"
        " (SELECT unit_cost FROM cost_items ci WHERE ci.sku_id = s.sku_id"
        "   ORDER BY valid_from DESC LIMIT 1) AS unit_cost"
        " FROM campaign_skus cs JOIN skus s USING (sku_id) LEFT JOIN products p USING (product_id)"
        " LEFT JOIN hourly_panel hp ON hp.sku_id = s.sku_id AND hp.hour_utc = ?"
        " WHERE cs.campaign_id = ? ORDER BY s.sku_id",
        [last_hour, campaign_id],
    ):
        skus.append(
            CampaignSku(
                sku=r["sku_id"],
                name=r["name"],
                variant=r["variant_name"] or "",
                is_bundle=bool(r["is_bundle"]),
                list_price_vnd=r["list_price"],
                price_effective_vnd=r["eff_price"],
                stock_available=r["stock_end"],
                unit_cost_vnd=r["unit_cost"],
            )
        )
    prices = [
        p
        for s in skus
        if not s.is_bundle
        for p in (s.price_effective_vnd, s.list_price_vnd)
        if p is not None
    ]
    since = local_date(as_of - timedelta(microseconds=1)) if as_of else None
    g = active_goal(conn, campaign_id)
    goal = (
        GoalBrief(
            goal_id=g.goal_id,
            kind=g.kind,
            label=goal_label(g),
            deadline=g.deadline,
            target_units=g.target_units,
            confirmed_by=g.confirmed_by,
            version=g.version,
        )
        if g
        else None
    )
    data = CampaignDetail(
        **base.model_dump(),
        started_at=started[0] if started else None,
        sku_detail=skus,
        price_max_vnd=max(prices) if prices else None,
        commitments=commitments(conn, campaign_id=campaign_id, since=since),
        goal=goal,
    )
    return Envelope[CampaignDetail](
        data=data, meta=Meta(data_as_of=as_of, computed_at=utcnow(), compute_state="fresh")
    )


class IroasOut(BaseModel):
    campaign_id: str
    estimate_id: str
    iroas: dict[str, float]  # {"p10","p50","p90","mean"}; p50 = posterior mean (see M3-api)
    marginal_iroas: float | None
    platform_roas: float | None
    inflation: float | None  # platform ROAS / iROAS
    breakeven_iroas: float | None
    method: str
    n_hours_used: int
    n_clean_off_hours: int
    excluded_hours: dict[str, int]
    delta: float
    spend_total_vnd: int
    inc_revenue_mean_vnd: int
    prior: dict[str, Any]
    window: dict[str, LocalDatetime]
    confidence: str
    reasons: list[str]
    flags: list[str]


@router.get(
    "/campaigns/{campaign_id}/iroas",
    response_model=Envelope[IroasOut],
    summary="Hiệu quả thật của ads (iROAS)",
)
def get_iroas(campaign_id: str, conn: Conn, _user: Viewer) -> Envelope[IroasOut]:
    if (
        conn.execute("SELECT 1 FROM campaigns WHERE campaign_id = ?", [campaign_id]).fetchone()
        is None
    ):
        raise NotFound(f"Không có chiến dịch {campaign_id}.", campaign_id=campaign_id)
    est = load_estimate(conn, campaign_id)
    if est is None:
        raise NotFound(
            f"Chưa ước lượng iROAS cho {campaign_id} (chạy estimate-ads).",
            campaign_id=campaign_id,
        )
    infl = (
        est.platform_roas / est.iroas_mean
        if est.platform_roas is not None and est.iroas_mean > 0
        else None
    )
    prior_only = "prior_only" in est.flags or est.n_hours_used == 0
    as_of = data_as_of(conn)
    return Envelope[IroasOut](
        data=IroasOut(
            campaign_id=campaign_id,
            estimate_id=est.estimate_id,
            iroas={
                "p10": est.iroas_p10,
                "p50": est.iroas_mean,
                "p90": est.iroas_p90,
                "mean": est.iroas_mean,
            },
            marginal_iroas=est.marginal_iroas,
            platform_roas=est.platform_roas,
            inflation=round(infl, 3) if infl is not None else None,
            breakeven_iroas=est.breakeven_iroas,
            method=est.model_id,
            n_hours_used=est.n_hours_used,
            n_clean_off_hours=est.n_clean_off_hours,
            excluded_hours=est.excluded_hours,
            delta=est.delta,
            spend_total_vnd=est.spend_total,
            inc_revenue_mean_vnd=est.inc_revenue_mean,
            prior={
                "source": "prior_only" if prior_only else "shop_kappa_prior",
                "a_mean": est.a_mean,
            },
            window={"from": est.period_start, "to": est.period_end},
            confidence=est.confidence,
            reasons=list(est.reasons),
            flags=list(est.flags),
        ),
        meta=Meta(
            data_as_of=est.period_end,
            computed_at=est.created_at,
            # estimated once a day (04 §7): stale only when older than 2 days of data
            compute_state=compute_state(
                est.period_end, as_of - timedelta(days=2) if as_of else None
            ),
            confidence=est.confidence,
            sources=[
                SourceRef(
                    id=est.estimate_id,
                    kind="iroas",
                    label=f"Ước lượng iROAS {campaign_id} ({est.model_id})",
                    data_as_of=est.period_end,
                    tool="get_iroas",
                    href=f"/api/v1/campaigns/{campaign_id}/iroas",
                )
            ],
        ),
    )
