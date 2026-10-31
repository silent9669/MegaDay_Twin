"""SKU / campaign snapshots read straight from the warehouse (API-04).

One pass over the catalogue computes the "Tổng quan ca" rows (08 §3.4): stock, hours of
cover from the latest forecast, units sold today, deviation from the forecast, campaign
status, active recommendation, open alerts and a priority score (06 §2 without the
``w_force`` term, which needs the orchestrator). ``GET /skus/{sku}`` adds the fields of the
AI tool ``get_sku_snapshot`` (07 §3).

Every time here is the **data clock** (``common.data_as_of``), not the wall clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import duckdb

from app.api.common import compute_state, data_as_of, parse_json, scalar, today_bounds
from app.core.timeutil import floor_hour, iso_local, local_date, to_local
from app.economics.contribution import contribution
from app.economics.parameters import load_param_set
from app.warehouse.repo import fetch_all

AD_STATUS_VI: dict[str, str] = {
    "running": "Đang chạy",
    "paused": "Tạm dừng",
    "budget_exhausted": "Hết ngân sách",
    "unknown": "Không rõ",
}
EPS = 1e-9


@dataclass
class ForecastView:
    forecast_id: str
    sku_id: str
    model_id: str
    origin: datetime
    confidence: str
    data_as_of: datetime
    created_at: datetime
    payload: dict[str, Any]

    @property
    def hours(self) -> list[datetime]:
        return [datetime.fromisoformat(h) for h in self.payload.get("hours", [])]


@dataclass
class Clock:
    as_of: datetime | None  # data mark (exclusive)
    last_hour: datetime | None  # last panel hour < as_of
    today_start: datetime | None
    today_end: datetime | None


@dataclass
class SkuRow:
    sku: str
    name: str
    variant: str
    product_id: str
    is_bundle: bool
    list_price: int | None
    demand_group: str | None
    demand_group_source: str | None
    stock_available: int | None = None
    eff_price: int | None = None
    sold_today_units: int = 0
    sold_last_hour_units: int = 0
    sold_3h_units: int = 0
    spend_3h_vnd: float = 0.0
    stockout_now: bool = False
    hours_of_cover: dict[str, float | None] | None = None
    deviation: str | None = None
    deviation_score: float = 0.0
    campaign_id: str | None = None
    open_alerts: int = 0
    forecast: ForecastView | None = None
    recommendation: dict[str, Any] | None = None
    compute_state: str | None = None
    computed_at: datetime | None = None
    confidence: str | None = None
    priority_score: float = 0.0
    components: list[dict[str, Any]] = field(default_factory=list)


def clock(conn: duckdb.DuckDBPyConnection) -> Clock:
    as_of = data_as_of(conn)
    if as_of is None:
        return Clock(None, None, None, None)
    lh = scalar(conn, "SELECT max(hour_utc) FROM hourly_panel WHERE hour_utc < ?", [as_of])
    start, end = today_bounds(as_of)
    return Clock(as_of, lh if isinstance(lh, datetime) else None, start, end)


def latest_forecasts(
    conn: duckdb.DuckDBPyConnection, sku_ids: list[str] | None = None
) -> dict[str, ForecastView]:
    where = "" if sku_ids is None else "WHERE list_contains(?, sku_id)"
    rows = conn.execute(
        "SELECT forecast_id, sku_id, model_id, origin, confidence, data_as_of, created_at,"
        f" payload FROM forecasts {where}"
        " QUALIFY row_number() OVER (PARTITION BY sku_id ORDER BY created_at DESC,"
        " origin DESC) = 1",
        [] if sku_ids is None else [sku_ids],
    ).fetchall()
    out: dict[str, ForecastView] = {}
    for r in rows:
        out[str(r[1])] = ForecastView(
            forecast_id=str(r[0]),
            sku_id=str(r[1]),
            model_id=str(r[2]),
            origin=r[3],
            confidence=str(r[4]),
            data_as_of=r[5],
            created_at=r[6],
            payload=parse_json(r[7]),
        )
    return out


def active_recommendations(
    conn: duckdb.DuckDBPyConnection, campaign_ids: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    where = "" if campaign_ids is None else " AND list_contains(?, r.campaign_id)"
    rows = fetch_all(
        conn,
        "SELECT r.recommendation_id, r.campaign_id, r.status, r.best_action, r.confidence,"
        " r.created_at, r.scenario_id, s.inputs_ref FROM recommendations r"
        " LEFT JOIN scenarios s USING (scenario_id) WHERE r.superseded_by IS NULL"
        " AND NOT coalesce(r.what_if, FALSE)" + where,
        [] if campaign_ids is None else [campaign_ids],
    )
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        ref = parse_json(r["inputs_ref"]) or {}
        best = parse_json(r["best_action"])
        prev = out.get(str(r["campaign_id"]))
        if prev is not None and prev["created_at"] >= r["created_at"]:
            continue
        out[str(r["campaign_id"])] = {
            "recommendation_id": r["recommendation_id"],
            "status": r["status"],
            "best": best,
            "confidence": r["confidence"],
            "created_at": r["created_at"],
            "scenario_id": r["scenario_id"],
            "data_as_of": _ts(ref.get("data_as_of")),
        }
    return out


def _ts(v: Any) -> datetime | None:
    """``inputs_ref.data_as_of`` is stored as JSON text (UTC, ``Z``): parse it so the API
    writes it with ``+07:00`` like every other time."""
    if isinstance(v, datetime) or v is None:
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _cover(stock: float, fc: ForecastView, at: datetime) -> dict[str, float | None] | None:
    """Hours until the cumulative forecast reaches ``stock`` (p50; p10 = fast selling from
    ``cum_p90``, p90 = slow selling from ``cum_p10``). ``None`` values = beyond horizon."""
    hours = fc.hours
    if not hours:
        return None
    start = floor_hour(at)
    idx0 = next((i for i, h in enumerate(hours) if h >= start), None)
    if idx0 is None:
        return None
    p = fc.payload

    def first(cum: list[float]) -> float | None:
        for i, c in enumerate(cum):
            if c >= stock - EPS:
                return float(i + 1)
        return None

    if stock <= 0:
        return {"p10": 0.0, "p50": 0.0, "p90": 0.0, "horizon_hours": float(len(hours) - idx0)}
    if idx0 == 0:
        return {
            "p10": first(p.get("cum_p90", [])),
            "p50": first(p.get("cum_p50", [])),
            "p90": first(p.get("cum_p10", [])),
            "horizon_hours": float(len(hours)),
        }
    mean = [float(x) for x in p.get("mean", [])][idx0:]
    cum, acc = [], 0.0
    for x in mean:
        acc += x
        cum.append(acc)
    return {"p10": None, "p50": first(cum), "p90": None, "horizon_hours": float(len(mean))}


def _deviation(
    fc: ForecastView, actual_by_hour: dict[datetime, int], at: datetime
) -> tuple[str | None, float]:
    """Actual since the forecast origin vs the cumulative interval (only when the origin is
    inside the elapsed data)."""
    hours = fc.hours
    elapsed = [h for h in hours if h < at]
    if not elapsed or fc.origin >= at:
        return None, 0.0
    k = len(elapsed)
    actual = float(sum(actual_by_hour.get(h, 0) for h in elapsed))
    p = fc.payload
    lo, mid, hi = (float(p[f"cum_{q}"][k - 1]) for q in ("p10", "p50", "p90"))
    score = min(abs(actual - mid) / max(hi - lo, 1.0), 2.0) / 2.0
    if actual < lo:
        return "below_forecast", score
    if actual > hi:
        return "above_forecast", score
    return "on_track", score


def bundle_components(conn: duckdb.DuckDBPyConnection) -> dict[str, list[tuple[str, int]]]:
    out: dict[str, list[tuple[str, int]]] = {}
    for b, c, q in conn.execute(
        "SELECT bundle_sku_id, component_sku_id, qty FROM bundle_components ORDER BY 1, 2"
    ).fetchall():
        out.setdefault(str(b), []).append((str(c), int(q)))
    return out


def campaign_status(
    conn: duckdb.DuckDBPyConnection, at: datetime | None
) -> dict[str, dict[str, Any]]:
    """Latest ``ad_hourly`` row per campaign before ``at`` (within 3 days)."""
    if at is None:
        return {}
    rows = fetch_all(
        conn,
        "SELECT campaign_id, hour_utc, status, daily_budget, spend FROM ad_hourly"
        " WHERE hour_utc < ? AND hour_utc >= ?"
        " QUALIFY row_number() OVER (PARTITION BY campaign_id ORDER BY hour_utc DESC) = 1",
        [at, at - timedelta(days=3)],
    )
    return {str(r["campaign_id"]): r for r in rows}


def sku_rows(
    conn: duckdb.DuckDBPyConnection, sku_ids: list[str] | None = None
) -> tuple[list[SkuRow], Clock]:
    ck = clock(conn)
    where = "" if sku_ids is None else " WHERE list_contains(?, s.sku_id)"
    base = fetch_all(
        conn,
        "SELECT s.sku_id, coalesce(p.name, s.product_id) AS name, s.variant_name, s.product_id,"
        " s.is_bundle, s.list_price, s.demand_group, s.demand_group_source FROM skus s"
        " LEFT JOIN products p USING (product_id)" + where + " ORDER BY s.sku_id",
        [] if sku_ids is None else [sku_ids],
    )
    rows = {
        str(r["sku_id"]): SkuRow(
            sku=str(r["sku_id"]),
            name=str(r["name"]),
            variant=str(r["variant_name"] or ""),
            product_id=str(r["product_id"]),
            is_bundle=bool(r["is_bundle"]),
            list_price=r["list_price"],
            demand_group=r["demand_group"],
            demand_group_source=r["demand_group_source"],
        )
        for r in base
    }
    if not rows:
        return [], ck
    ids = list(rows)
    comps = bundle_components(conn)
    actual: dict[str, dict[datetime, int]] = {}
    if ck.as_of is not None and ck.last_hour is not None and ck.today_start is not None:
        lh = ck.last_hour
        for r in conn.execute(
            "SELECT sku_id, stock_end, eff_price, stockout FROM hourly_panel"
            " WHERE hour_utc = ? AND list_contains(?, sku_id)",
            [lh, ids],
        ).fetchall():
            row = rows[str(r[0])]
            row.stock_available = None if r[1] is None else int(r[1])
            row.eff_price = None if r[2] is None else int(r[2])
            row.stockout_now = bool(r[3])
        since = min(ck.today_start, lh - timedelta(hours=2))
        for r in conn.execute(
            "SELECT sku_id, hour_utc, units_gross, coalesce(ad_spend_alloc, 0) FROM hourly_panel"
            " WHERE hour_utc >= ? AND hour_utc < ? AND list_contains(?, sku_id)",
            [since, ck.as_of, ids],
        ).fetchall():
            sku, h, u, sp = str(r[0]), r[1], int(r[2] or 0), float(r[3] or 0.0)
            row = rows[sku]
            actual.setdefault(sku, {})[h] = u
            if h >= ck.today_start:
                row.sold_today_units += u
            if h == lh:
                row.sold_last_hour_units += u
            if h >= lh - timedelta(hours=2):
                row.sold_3h_units += u
                row.spend_3h_vnd += sp
        # combos hold no stock: available = min over components of stock // qty
        for sku, row in rows.items():
            if row.is_bundle and sku in comps:
                stocks = []
                for c, q in comps[sku]:
                    cs = scalar(
                        conn,
                        "SELECT stock_end FROM hourly_panel WHERE sku_id = ? AND hour_utc = ?",
                        [c, lh],
                    )
                    stocks.append(None if cs is None else int(cs) // max(q, 1))
                    row.components.append({"sku": c, "qty": q})
                if stocks and all(s is not None for s in stocks):
                    row.stock_available = min(s for s in stocks if s is not None)
    camp_of: dict[str, str] = {}
    for c, s in conn.execute(
        "SELECT campaign_id, sku_id FROM campaign_skus ORDER BY campaign_id"
    ).fetchall():
        camp_of.setdefault(str(s), str(c))
    alerts = {
        str(r[0]): int(r[1])
        for r in conn.execute(
            "SELECT target_id, count(*) FROM alerts WHERE status = 'open' AND target_kind = 'sku'"
            " GROUP BY 1"
        ).fetchall()
    }
    states = {
        str(r[0]): (r[1], r[2], r[3])
        for r in conn.execute(
            "SELECT sku_id, state, computed_at, data_as_of FROM sku_compute_state"
        ).fetchall()
    }
    fcs = latest_forecasts(conn, ids)
    recs = active_recommendations(conn)
    total_spend = sum(r.spend_3h_vnd for r in rows.values())
    for sku, row in rows.items():
        row.campaign_id = camp_of.get(sku)
        row.open_alerts = alerts.get(sku, 0)
        fc = fcs.get(sku)
        row.forecast = fc
        at = ck.as_of
        if fc is not None and at is not None:
            if row.stock_available is not None:
                row.hours_of_cover = _cover(float(row.stock_available), fc, at)
            row.deviation, row.deviation_score = _deviation(fc, actual.get(sku, {}), at)
        rec = recs.get(row.campaign_id) if row.campaign_id else None
        row.recommendation = rec
        stored = states.get(sku)
        res_as_of = fc.data_as_of if fc is not None else None
        row.compute_state = compute_state(
            res_as_of, ck.as_of, stored[0] if stored else None, stored[2] if stored else None
        )
        row.computed_at = (stored[1] if stored else None) or (fc.created_at if fc else None)
        row.confidence = (rec or {}).get("confidence") or (fc.confidence if fc else None)
        # 06 §2 priority without w_force (needs the orchestrator)
        cover = (row.hours_of_cover or {}).get("p50")
        if row.stock_available is not None and row.stock_available <= 0:
            stock_risk = 1.0
        elif cover is None:
            stock_risk = 0.0
        else:
            stock_risk = min(max(1 - cover / 24.0, 0.0), 1.0)
        spend_share = row.spend_3h_vnd / total_spend if total_spend > 0 else 0.0
        hours_since = (
            (at - row.computed_at).total_seconds() / 3600
            if (at is not None and row.computed_at is not None)
            else 6.0
        )
        staleness = min(max(hours_since / 6.0, 0.0), 1.0)
        row.priority_score = round(
            40 * stock_risk + 25 * spend_share + 25 * row.deviation_score + 10 * staleness, 2
        )
    return list(rows.values()), ck


# ---------------------------------------------------------------------- detail extras
def _windows_local(start: datetime | None, end: datetime | None) -> list[int] | None:
    if start is None or end is None:
        return None
    a = to_local(start).hour
    b = to_local(end).hour or 24
    return [a, b]


def commitments(
    conn: duckdb.DuckDBPyConnection,
    *,
    sku: str | None = None,
    campaign_id: str | None = None,
    since: date | None = None,
) -> list[dict[str, Any]]:
    cond: list[str] = []
    params: list[Any] = []
    if sku is not None:
        cond.append("sku_id = ?")
        params.append(sku)
    if campaign_id is not None:
        cond.append("campaign_id = ?")
        params.append(campaign_id)
    if not cond:
        return []
    sql = (
        "SELECT commitment_id, program, day_local, kind, sku_id, campaign_id, value_int,"
        f" window_start, window_end, raw_text FROM sale_commitments WHERE ({' OR '.join(cond)})"
    )
    if since is not None:
        sql += " AND day_local >= ?"
        params.append(since)
    out = []
    for r in fetch_all(conn, sql + " ORDER BY day_local, window_start NULLS FIRST", params):
        out.append(
            {
                "id": r["commitment_id"],
                "program": r["program"],
                "day_local": r["day_local"].isoformat() if r["day_local"] else None,
                "kind": r["kind"],
                "sku": r["sku_id"],
                "campaign_id": r["campaign_id"],
                "value": r["value_int"],
                "windows_local": (
                    [_windows_local(r["window_start"], r["window_end"])]
                    if r["window_start"] is not None
                    else None
                ),
                "window_start": iso_local(r["window_start"]),
                "window_end": iso_local(r["window_end"]),
                "text": r["raw_text"],
            }
        )
    return out


def latest_cost(conn: duckdb.DuckDBPyConnection, sku: str) -> dict[str, Any] | None:
    r = conn.execute(
        "SELECT unit_cost, packaging_cost, valid_from, note FROM cost_items WHERE sku_id = ?"
        " ORDER BY valid_from DESC LIMIT 1",
        [sku],
    ).fetchone()
    if r is None:
        return None
    return {
        "unit_cost_vnd": int(r[0]),
        "packaging_cost_vnd": int(r[1] or 0),
        "valid_from": iso_local(r[2]),
        "note": r[3],
    }


def detail(conn: duckdb.DuckDBPyConnection, row: SkuRow, ck: Clock) -> dict[str, Any]:
    """Fields of ``get_sku_snapshot`` (07 §3) on top of the overview row."""
    today = local_date(ck.as_of - timedelta(microseconds=1)) if ck.as_of else None
    incoming = []
    if ck.as_of is not None:
        for r in conn.execute(
            "SELECT qty_incoming, planned_date, seen_at FROM restock_plans WHERE sku_id = ?"
            " AND seen_at < ? QUALIFY row_number() OVER (PARTITION BY planned_date"
            " ORDER BY seen_at DESC) = 1 ORDER BY planned_date",
            [row.sku, ck.as_of],
        ).fetchall():
            if today is None or r[1] >= today:
                incoming.append(
                    {"qty": int(r[0]), "planned_date": r[1].isoformat(), "seen_at": iso_local(r[2])}
                )
    shared: list[str] = []
    camp: dict[str, Any] | None = None
    if row.campaign_id:
        crow = conn.execute(
            "SELECT name, daily_budget, sku_level_control FROM campaigns WHERE campaign_id = ?",
            [row.campaign_id],
        ).fetchone()
        shared = [
            str(r[0])
            for r in conn.execute(
                "SELECT sku_id FROM campaign_skus WHERE campaign_id = ? AND sku_id <> ? ORDER BY 1",
                [row.campaign_id, row.sku],
            ).fetchall()
        ]
        st = campaign_status(conn, ck.as_of).get(row.campaign_id)
        camp = {
            "id": row.campaign_id,
            "name": crow[0] if crow else None,
            "daily_budget_vnd": int((st or {}).get("daily_budget") or crow[1]) if crow else None,
            "sku_level_control": bool(crow[2]) if crow else None,
            "status": AD_STATUS_VI.get(str(st["status"]), str(st["status"])) if st else None,
            "status_code": st["status"] if st else None,
            "status_at": iso_local(st["hour_utc"]) if st else None,
            "shared_with": shared,
        }
    used_in = [
        str(r[0])
        for r in conn.execute(
            "SELECT bundle_sku_id FROM bundle_components WHERE component_sku_id = ? ORDER BY 1",
            [row.sku],
        ).fetchall()
    ]
    issues = [
        {"code": str(r[0]), "severity": str(r[1]), "count": int(r[2])}
        for r in conn.execute(
            "SELECT code, severity, count(*) FROM data_quality_issues WHERE entity_id = ?"
            " AND status = 'open' GROUP BY 1, 2 ORDER BY 1",
            [row.sku],
        ).fetchall()
    ]
    cost = latest_cost(conn, row.sku)
    m = None
    if cost is not None and row.eff_price is not None and not row.is_bundle:
        fee = load_param_set(conn).num("platform_fee_rate")
        m = round(
            contribution(row.eff_price, cost["unit_cost_vnd"], cost["packaging_cost_vnd"], fee)
        )
    camp_comms = commitments(conn, sku=row.sku, campaign_id=row.campaign_id, since=today)
    as_of_local = to_local(ck.as_of - timedelta(minutes=1)) if ck.as_of else None
    return {
        "incoming": incoming,
        "price_list_vnd": row.list_price,
        "price_effective_vnd": row.eff_price,
        "sold_last_hour_units": row.sold_last_hour_units,
        "demand_group": {
            "value": row.demand_group,
            "source": row.demand_group_source or "auto",
            "confirmed": row.demand_group_source == "user",
        },
        "data_labels": issues,
        "campaign_detail": camp,
        "components_of": row.components,
        "used_in_combos": used_in,
        "commitments": camp_comms,
        "cost": cost,
        "contribution_per_unit_vnd": m,
        "source": {
            "kind": "snapshot",
            "id": f"snap-{row.sku}-{as_of_local:%H%M}" if as_of_local else f"snap-{row.sku}",
            "label": (
                f"Tình trạng SKU, dữ liệu tới {as_of_local:%H:%M %d/%m}"
                if as_of_local
                else "Tình trạng SKU"
            ),
        },
    }
