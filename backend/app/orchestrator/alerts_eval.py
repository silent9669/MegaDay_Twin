"""AlertEvaluator (ORC-05, 09 §2.5 rules, 10 §3 table ``alerts``).

Two entry points:

* :meth:`AlertEvaluator.evaluate` — after each recompute, on the data clock ``as_of``
  (exclusive mark: ``as_of`` 14:00 = data up to 13:59). State rules:

  ============================  ========================================================
  ``stockout_risk`` (phase out)  SKU in a campaign whose last known ads hour is
                                 *running* and stock at the last hour = 0 → high
  ``stockout_risk`` (phase low)  same, stock > 0 and ``hours_of_cover.p50 < 6`` → high
  ``sales_above_forecast``       Σ sold 3 h > Σ p90 3 h of the **baseline of the day**
                                 and ≥ 5 units → high if cover < 24 h, else medium
  ``sales_below_forecast``       Σ sold 3 h < Σ p10 3 h (≥ 3 units expected, no
                                 stock-out hour) → high if impact > 1 tr, else medium
  ``budget_exhausted``           last ads hour "Hết ngân sách" before 20:00 → medium
  ``data_stale``                 a source beyond its stale threshold (06 §4) → medium
  ``data_blocking``              open ``error`` issue on a SKU with running ads → high
  ============================  ========================================================

* bus handlers — ``DriftDetected`` (merged into the sales alert of the same product and
  direction, so CUSUM and the 3-hour rule never raise two alerts for one problem),
  ``RecommendationChanged`` (``recommendation_changed``, medium) and ``ComputeFailed``
  (``compute_failed``, high).

Anti-spam
---------
* **Dedupe** (``app.governance.alerts.create_alert``): one live alert per ``dedupe_key``;
  a repeat refreshes it in place and raises nothing. Sales alerts are keyed per
  *product and day* (``group_key`` = product), stock alerts per SKU and phase, so a whole
  11.11 replay gives at most two stock alerts per SKU and one sales alert per product
  and direction. Conditions that clear (stock back, ads stopped, data fresh again)
  resolve their alert; sales alerts stay until the day ends (no flapping).
* **Budget** (09 §2.5): at most :data:`ALERT_BUDGET` = 8 *open* alerts per shift (= the
  VN day of the data clock); the others become ``hidden_by_budget`` by severity then
  impact, and come back when a slot frees. ``AlertRaised`` is published only for alerts
  that are visible (open) after the budget.

Times: ``raised_at`` is the wall clock (snooze/ack use it too); the data hour that raised
the alert is in ``detail.data_as_of`` / ``detail.data_hour`` ("dữ liệu tới 13:59").
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import duckdb

from app.core.events import (
    AlertRaised,
    AlertResolved,
    ComputeFailed,
    DriftDetected,
    Event,
    EventBus,
    RecommendationChanged,
)
from app.core.logging import get_logger
from app.core.timeutil import floor_hour, local_date, local_day_bounds_utc, to_local, utcnow
from app.forecast.base import ForecastResult
from app.forecast.drift import DriftAlarm
from app.governance import alerts as store
from app.orchestrator.recompute import campaign_map, day_start, load_baseline

log = get_logger(__name__)

CursorFactory = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]
ALERT_BUDGET = 8
COVER_LOW_HOURS = 6.0
COVER_RISK_HOURS = 24.0
WINDOW_HOURS = 3
MIN_ABOVE_UNITS = 10  # product, 3 h
# "above" (per product, 3 h): Σ sold > 1.5 × Σ p90 and ≥ 2 × Σ p50, or, when CUSUM drift
# "up" fired for the product today, Σ sold > Σ p90 and ≥ 1.5 × Σ p50
ABOVE_RATIO = 1.5
MIN_BELOW_EXPECTED = 3.0
HIGH_IMPACT_VND = 1_000_000
BUDGET_EXHAUSTED_BEFORE = 20  # local hour
SYSTEM_KINDS = (
    "stockout_risk",
    "sales_above_forecast",
    "sales_below_forecast",
    "drift",
    "budget_exhausted",
    "data_stale",
    "data_blocking",
    "recommendation_changed",
    "compute_failed",
)
SEV = store.SEVERITY_RANK
BLOCKING_VI = {"MISSING_COST": "giá vốn"}
STATUS_VI = {"undetermined": "chưa đủ chắc", "none_meets_goal": "không phương án nào đạt"}


def _hm(t: datetime) -> str:
    return to_local(t).strftime("%H:%M")


@dataclass
class SkuInfo:
    sku: str
    product_id: str
    name: str
    variant: str | None
    list_price: int
    is_bundle: bool

    @property
    def label(self) -> str:
        return f"{self.name} ({self.variant})" if self.variant else self.name


@dataclass
class AdState:
    status: str
    hour: datetime
    spend_per_hour: float


class AlertEvaluator:
    def __init__(
        self,
        cursor: CursorFactory | None = None,
        *,
        lock: threading.Lock | None = None,
        shop_id: str = store.DEFAULT_SHOP,
        demo_mode: bool = True,
        budget: int = ALERT_BUDGET,
    ) -> None:
        self._cursor = cursor
        self.lock = lock or threading.Lock()
        self.shop_id = shop_id
        self.demo_mode = demo_mode
        self.budget = budget
        self.bus: EventBus | None = None
        self._drift_pending: dict[str, list[dict[str, Any]]] = {}

    # ------------------------------------------------------------------ bus handlers
    def attach(self, bus: EventBus) -> None:
        self.bus = bus
        bus.subscribe(DriftDetected, self.on_event)
        bus.subscribe(RecommendationChanged, self.on_event)
        bus.subscribe(ComputeFailed, self.on_event)

    async def on_event(self, event: Event) -> None:
        import asyncio

        if self._cursor is None or self.bus is None:
            return
        events = await asyncio.to_thread(self._handle_sync, event)
        for ev in events:
            await self.bus.publish(ev)

    def _handle_sync(self, event: Event) -> list[Event]:
        assert self._cursor is not None
        with self.lock, self._cursor() as conn:
            return self.handle(conn, event)

    def handle(self, conn: duckdb.DuckDBPyConnection, event: Event) -> list[Event]:
        """Alert for one bus event (sync; the caller holds the write lock)."""
        raised: list[str] = []
        if isinstance(event, DriftDetected):
            raised += self._drift(conn, event)
        elif isinstance(event, RecommendationChanged):
            raised += self._rec_changed(conn, event)
        elif isinstance(event, ComputeFailed):
            if event.target == "alerts":  # our own failure: do not loop
                return []
            raised += self._upsert(
                conn,
                "compute_failed",
                ("system", event.target),
                "high",
                f"Không tính lại được {event.target} ({event.stage})",
                {"stage": event.stage, "error": event.error, "target": event.target},
                key=f"compute_failed:{event.target}:{event.stage}",
                source_event_id=event.id,
            )
        return self._finish(conn, raised, [])

    # ------------------------------------------------------------------ evaluate
    def evaluate(
        self,
        conn: duckdb.DuckDBPyConnection,
        as_of: datetime,
        drift: Sequence[DriftAlarm] = (),
    ) -> list[Event]:
        """State rules on the data clock ``as_of``. ``drift`` = CUSUM alarms of this tick
        (the orchestrator passes them so the sales rule sees them in the same tick).
        Returns ``AlertRaised`` / ``AlertResolved`` events to publish."""
        raised: list[str] = []
        resolved: list[str] = []
        ctx = _Context.load(conn, as_of)
        for a in drift:
            info = ctx.info.get(a.sku_id)
            pid = info.product_id if info else a.sku_id
            self._drift_pending.setdefault(_sales_key(a.direction, pid, ctx.day), []).append(
                {
                    "sku": a.sku_id,
                    "variant": info.variant if info else None,
                    "stat": round(a.stat, 2),
                    "data_hour": _hm(as_of - timedelta(minutes=1)),
                }
            )
        r, s = self._stock(conn, ctx)
        raised += r
        resolved += s
        raised += self._sales(conn, ctx)
        r, s = self._budget_exhausted(conn, ctx)
        raised += r
        resolved += s
        r, s = self._stale(conn, ctx)
        raised += r
        resolved += s
        r, s = self._blocking(conn, ctx)
        raised += r
        resolved += s
        resolved += self._expire(conn, ctx.day)
        return self._finish(conn, raised, resolved)

    def on_restart(self, conn: duckdb.DuckDBPyConnection) -> list[Event]:
        """Replay restarted from hour 0: alerts raised by the old run are resolved."""
        self._drift_pending.clear()
        ids = [
            str(r[0])
            for r in conn.execute(
                "SELECT id FROM alerts WHERE list_contains(?, kind)"
                " AND status IN ('open', 'acknowledged', 'snoozed', 'hidden_by_budget')",
                [list(SYSTEM_KINDS)],
            ).fetchall()
        ]
        for aid in ids:
            store.resolve_alert(conn, aid, "system", note="Phát lại dữ liệu từ đầu")
        return [AlertResolved(alert_id=a) for a in ids]

    # ------------------------------------------------------------------ rules
    def _stock(self, conn: duckdb.DuckDBPyConnection, ctx: _Context) -> tuple[list[str], list[str]]:
        raised: list[str] = []
        resolved: list[str] = []
        for sku, camps in sorted(ctx.sku_campaigns.items()):
            info = ctx.info.get(sku)
            if info is None or info.is_bundle:
                continue
            key_out = f"stockout_risk:sku:{sku}:out"
            key_low = f"stockout_risk:sku:{sku}:low"
            running = [c for c in camps if ctx.ads.get(c) and ctx.ads[c].status == "running"]
            stock = ctx.stock.get(sku)
            if not running or stock is None:
                resolved += self._resolve(conn, key_out) + self._resolve(conn, key_low)
                continue
            spend_h = sum(ctx.ads[c].spend_per_hour for c in running)
            hours_left = max((ctx.day_end - ctx.as_of).total_seconds() / 3600, 0.0)
            detail_base: dict[str, Any] = {
                "sku": sku,
                "product": info.name,
                "variant": info.variant,
                "stock_units": stock,
                "campaigns": running,
                "ads_status_at": ctx.ads[running[0]].hour.isoformat(),
                "spend_per_hour_vnd": round(spend_h),
                "data_as_of": ctx.as_of.isoformat(),
                "data_hour": _hm(ctx.as_of - timedelta(minutes=1)),
            }
            impact = int(spend_h * hours_left)
            if stock <= 0:
                resolved += self._resolve(conn, key_low)
                if (ctx.stock_day0.get(sku) or 0) <= 0:
                    continue  # out of stock since before today: not news (09 §2.5)
                raised += self._upsert(
                    conn,
                    "stockout_risk",
                    ("sku", sku),
                    "high",
                    f"{info.label} đã hết hàng nhưng quảng cáo {', '.join(running)} vẫn chạy",
                    {**detail_base, "phase": "out", "impact_basis": "chi ads còn lại hôm nay"},
                    key=key_out,
                    impact_vnd=impact,
                    group_key=info.product_id,
                )
                continue
            resolved += self._resolve(conn, key_out)
            cover = ctx.cover(sku, float(stock))
            if cover is not None and cover < COVER_LOW_HOURS:
                raised += self._upsert(
                    conn,
                    "stockout_risk",
                    ("sku", sku),
                    "high",
                    f"{info.label} sắp hết hàng: còn {stock} sp, đủ bán khoảng {cover:.0f} giờ",
                    {
                        **detail_base,
                        "phase": "low",
                        "hours_of_cover_p50": cover,
                        "impact_basis": "chi ads còn lại hôm nay",
                    },
                    key=key_low,
                    impact_vnd=impact,
                    group_key=info.product_id,
                )
            else:
                resolved += self._resolve(conn, key_low)
        return raised, resolved

    def _sales(self, conn: duckdb.DuckDBPyConnection, ctx: _Context) -> list[str]:
        """3-hour window vs the baseline of the day, grouped by product."""
        hours = ctx.window()
        if not hours:
            return []
        by_product: dict[str, list[dict[str, Any]]] = {}
        for sku in sorted(ctx.sku_campaigns):
            info = ctx.info.get(sku)
            fc = ctx.baseline.get(sku)
            if info is None or info.is_bundle or fc is None:
                continue
            band = _band(fc, hours)
            if band is None:
                continue
            lo, mid, hi = band
            by_product.setdefault(info.product_id, []).append(
                {
                    "sku": sku,
                    "variant": info.variant,
                    "sold_3h": sum(ctx.units.get((sku, h), 0) for h in hours),
                    "p10_3h": round(lo, 1),
                    "p50_3h": round(mid, 1),
                    "p90_3h": round(hi, 1),
                    "stockout_hours": sum(1 for h in hours if ctx.stockout.get((sku, h), False)),
                    "price": info.list_price,
                }
            )
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for pid, rows in by_product.items():
            sold = sum(int(r["sold_3h"]) for r in rows)
            hi = sum(float(r["p90_3h"]) for r in rows)
            mid = sum(float(r["p50_3h"]) for r in rows)
            strict = sold > ABOVE_RATIO * hi and sold >= 2 * mid
            mild = sold > hi and sold >= ABOVE_RATIO * mid
            confirmed = bool(self._drift_pending.get(_sales_key("up", pid, ctx.day)))
            if sold >= MIN_ABOVE_UNITS and (strict or (mild and confirmed)):
                up = [r for r in rows if r["sold_3h"] > r["p90_3h"]] or rows
                for r in up:
                    r["impact_vnd"] = int(max(r["sold_3h"] - r["p90_3h"], 0) * r["price"])
                groups[("up", pid)] = up
                continue
            ok = [r for r in rows if r["stockout_hours"] == 0]
            lo = sum(float(r["p10_3h"]) for r in ok)
            sold_ok = sum(int(r["sold_3h"]) for r in ok)
            if ok and sold_ok < lo and lo >= MIN_BELOW_EXPECTED:
                down = [r for r in ok if r["sold_3h"] < r["p10_3h"]] or ok
                for r in down:
                    r["impact_vnd"] = int(max(r["p10_3h"] - r["sold_3h"], 0) * r["price"])
                groups[("down", pid)] = down
        raised: list[str] = []
        for (direction, pid), rows in sorted(groups.items()):
            top = max(rows, key=lambda r: r["impact_vnd"])
            info = ctx.info[top["sku"]]
            impact = sum(int(r["impact_vnd"]) for r in rows)
            if direction == "up":
                covers = [ctx.cover(r["sku"], float(ctx.stock.get(r["sku"]) or 0)) for r in rows]
                risky = any(c is not None and c < COVER_RISK_HOURS for c in covers)
                severity = "high" if risky else "medium"
                title = f"{info.name} bán vượt dự báo 3 giờ liền" + (
                    ", có nguy cơ hết hàng" if risky else ""
                )
            else:
                severity = "high" if impact > HIGH_IMPACT_VND else "medium"
                title = f"{info.name} bán thấp hơn dự báo 3 giờ liền"
            key = _sales_key(direction, pid, ctx.day)
            drift = _merge_rows([], self._drift_pending.get(key, []))
            raised += self._upsert(
                conn,
                "sales_above_forecast" if direction == "up" else "sales_below_forecast",
                ("sku", top["sku"]),
                severity,
                title,
                {
                    "product_id": pid,
                    "direction": direction,
                    "window": [_hm(hours[0]), _hm(hours[-1] + timedelta(hours=1))],
                    "skus": rows,
                    "baseline_origin": ctx.day0.isoformat(),
                    "data_as_of": ctx.as_of.isoformat(),
                    "data_hour": _hm(ctx.as_of - timedelta(minutes=1)),
                    "impact_basis": "chênh số bán × giá niêm yết",
                    **({"drift": drift} if drift else {}),
                },
                key=key,
                impact_vnd=impact,
                group_key=pid,
            )
        return raised

    def _budget_exhausted(
        self, conn: duckdb.DuckDBPyConnection, ctx: _Context
    ) -> tuple[list[str], list[str]]:
        raised: list[str] = []
        for cid, ad in sorted(ctx.ads.items()):
            if ad.status != "budget_exhausted" or ad.hour < ctx.day0:
                continue
            if to_local(ad.hour).hour >= BUDGET_EXHAUSTED_BEFORE:
                continue
            raised += self._upsert(
                conn,
                "budget_exhausted",
                ("campaign", cid),
                "medium",
                f"Chiến dịch {cid} hết ngân sách lúc {_hm(ad.hour)}, trước 20:00",
                {
                    "campaign_id": cid,
                    "hour": ad.hour.isoformat(),
                    "data_as_of": ctx.as_of.isoformat(),
                },
                key=f"budget_exhausted:campaign:{cid}:{ctx.day.isoformat()}",
                group_key=cid,
            )
        return raised, []

    def _stale(self, conn: duckdb.DuckDBPyConnection, ctx: _Context) -> tuple[list[str], list[str]]:
        from app.api.common import freshness, reference_now

        raised: list[str] = []
        resolved: list[str] = []
        now = reference_now(conn, self.demo_mode)
        for f in freshness(conn, now):
            if f.source not in ("orders", "inventory", "ads"):
                continue
            key = f"data_stale:source:{f.source}"
            if f.status == "stale":
                raised += self._upsert(
                    conn,
                    "data_stale",
                    ("source", f.source),
                    "medium",
                    f"Dữ liệu {f.label.lower()} trễ {f.lag_minutes} phút"
                    f" (ngưỡng {f.threshold_minutes} phút)",
                    {
                        "source": f.source,
                        "as_of": f.as_of.isoformat() if f.as_of else None,
                        "lag_minutes": f.lag_minutes,
                        "threshold_minutes": f.threshold_minutes,
                        "data_as_of": ctx.as_of.isoformat(),
                        "data_hour": _hm(ctx.as_of - timedelta(minutes=1)),
                    },
                    key=key,
                )
            elif f.status == "fresh":
                resolved += self._resolve(conn, key)
        return raised, resolved

    def _blocking(
        self, conn: duckdb.DuckDBPyConnection, ctx: _Context
    ) -> tuple[list[str], list[str]]:
        running_skus = {
            s
            for s, camps in ctx.sku_campaigns.items()
            if any(ctx.ads.get(c) and ctx.ads[c].status == "running" for c in camps)
        }
        by_code: dict[str, list[str]] = {}
        for sku, code in conn.execute(
            "SELECT DISTINCT entity_id, code FROM data_quality_issues WHERE status = 'open'"
            " AND severity = 'error' AND entity_type = 'sku' ORDER BY 2, 1"
        ).fetchall():
            if str(sku) in running_skus:
                by_code.setdefault(str(code), []).append(str(sku))
        raised: list[str] = []
        resolved: list[str] = []
        live_codes = [
            str(r[0]).split(":", 2)[2]
            for r in conn.execute(
                "SELECT dedupe_key FROM alerts WHERE kind = 'data_blocking'"
                " AND starts_with(dedupe_key, 'data_blocking:code:')"
                " AND status IN ('open', 'acknowledged', 'snoozed', 'hidden_by_budget')"
            ).fetchall()
        ]
        for code in sorted(set(live_codes) - set(by_code)):
            resolved += self._resolve(conn, f"data_blocking:code:{code}")
        for code, skus in sorted(by_code.items()):
            what = BLOCKING_VI.get(code, code)
            raised += self._upsert(
                conn,
                "data_blocking",
                ("issue", code),
                "high",
                f"Thiếu {what} cho {len(skus)} SKU đang chạy quảng cáo",
                {"code": code, "skus": [{"sku": x} for x in skus], "n_skus": len(skus)},
                key=f"data_blocking:code:{code}",
            )
        return raised, resolved

    def _drift(self, conn: duckdb.DuckDBPyConnection, ev: DriftDetected) -> list[str]:
        """Floor (09 §2.5): CUSUM alone does not raise an alert. The alarm is attached to the
        sales alert of the same product, direction and day, now if it exists, else when the
        3-hour rule fires (``_drift_pending``)."""
        row = conn.execute(
            "SELECT product_id, variant_name FROM skus WHERE sku_id = ?", [ev.sku_id]
        ).fetchone()
        pid, variant = (str(row[0]), row[1]) if row else (ev.sku_id, None)
        day = self._day_of(conn)
        if day is None:
            return []
        alarm: dict[str, Any] = {"sku": ev.sku_id, "variant": variant, "stat": round(ev.stat, 2)}
        as_of = _data_as_of(conn)
        if as_of is not None:
            alarm["data_hour"] = _hm(as_of - timedelta(minutes=1))
        key = _sales_key(ev.direction, pid, day)
        prev = self._live_row(conn, key)
        if prev is None:
            self._drift_pending.setdefault(key, []).append(alarm)
            return []
        _status, severity, _detail, title = prev
        return self._upsert(
            conn,
            "sales_above_forecast" if ev.direction == "up" else "sales_below_forecast",
            ("sku", ev.sku_id),
            severity,
            title,
            {"drift": [alarm]},
            key=key,
            group_key=pid,
            source_event_id=ev.id,
        )

    def _rec_changed(self, conn: duckdb.DuckDBPyConnection, ev: RecommendationChanged) -> list[str]:
        if ev.old is None:
            return []

        def label(r: dict[str, Any]) -> str:
            from app.simulator.schemas import ActionSpec

            best = r.get("best")
            if not best:
                return STATUS_VI.get(str(r.get("status")), "không có")
            return ActionSpec.model_validate(best).label_vi()

        old, new = label(ev.old), label(ev.new)
        return self._upsert(
            conn,
            "recommendation_changed",
            ("campaign", ev.campaign_id),
            "medium",
            f"Đề xuất cho {ev.campaign_id} đổi: {old} → {new}",
            {
                "campaign_id": ev.campaign_id,
                "old": old,
                "new": new,
                "recommendation_id": ev.new.get("recommendation_id"),
            },
            key=f"recommendation_changed:campaign:{ev.campaign_id}",
            group_key=ev.campaign_id,
            source_event_id=ev.id,
        )

    # ------------------------------------------------------------------ store glue
    def _live_row(
        self, conn: duckdb.DuckDBPyConnection, key: str
    ) -> tuple[str, str, Any, str] | None:
        row = conn.execute(
            "SELECT id, status, severity, detail, title FROM alerts WHERE dedupe_key = ?"
            " AND status IN ('open', 'acknowledged', 'snoozed', 'hidden_by_budget')"
            " ORDER BY raised_at DESC LIMIT 1",
            [key],
        ).fetchone()
        if row is None:
            return None
        return str(row[1]), str(row[2]), _json(row[3]), str(row[4])

    def _upsert(
        self,
        conn: duckdb.DuckDBPyConnection,
        kind: str,
        scope: tuple[str, str],
        severity: str,
        title: str,
        detail: dict[str, Any],
        *,
        key: str,
        impact_vnd: int | None = None,
        group_key: str | None = None,
        source_event_id: str | None = None,
    ) -> list[str]:
        """``create_alert`` with three extras: never downgrade the severity, merge
        ``detail`` lists (``skus``, ``drift``) with the stored one, and leave an alert
        hidden by the budget hidden (``create_alert`` only dedupes live statuses).
        Returns ``[id]`` when the alert must be announced."""
        prev = self._live_row(conn, key)
        if prev is None or not isinstance(prev[2], dict) or "first_data_hour" not in prev[2]:
            detail = {**detail, "first_data_hour": detail.get("data_hour")}
        if prev is not None:
            status, old_sev, old_detail, _title = prev
            if status == "hidden_by_budget":
                return []
            if SEV.get(old_sev, 0) > SEV.get(severity, 0):
                severity = old_sev
            if isinstance(old_detail, dict):
                merged = {**old_detail, **detail}
                for k in ("skus", "drift"):
                    if k in old_detail or k in detail:
                        merged[k] = _merge_rows(old_detail.get(k, []), detail.get(k, []))
                detail = merged
        up = store.create_alert(
            conn,
            kind,
            scope,
            severity,
            title,
            detail,
            key,
            impact_vnd=impact_vnd,
            group_key=group_key,
            source_event_id=source_event_id,
            shop_id=self.shop_id,
        )
        return [up.alert.id] if up.raised else []

    def _expire(self, conn: duckdb.DuckDBPyConnection, day: date) -> list[str]:
        """Day-keyed alerts (sales, budget) of an earlier data day are resolved."""
        ids = [
            str(r[0])
            for r in conn.execute(
                "SELECT id FROM alerts WHERE list_contains(?, kind)"
                " AND NOT ends_with(dedupe_key, ?)"
                " AND status IN ('open', 'acknowledged', 'snoozed', 'hidden_by_budget')",
                [
                    ["sales_above_forecast", "sales_below_forecast", "budget_exhausted"],
                    ":" + day.isoformat(),
                ],
            ).fetchall()
        ]
        for aid in ids:
            store.resolve_alert(conn, aid, "system", note="Hết ngày dữ liệu")
        return ids

    def _resolve(self, conn: duckdb.DuckDBPyConnection, key: str) -> list[str]:
        return store.resolve_by_key(conn, key, "system", shop_id=self.shop_id)

    def _day_of(self, conn: duckdb.DuckDBPyConnection) -> date | None:
        as_of = _data_as_of(conn)
        return None if as_of is None else local_date(as_of - timedelta(microseconds=1))

    def _finish(
        self,
        conn: duckdb.DuckDBPyConnection,
        raised: Sequence[str],
        resolved: Sequence[str],
    ) -> list[Event]:
        shown, hidden = self.apply_budget(conn)
        announce = [a for a in dict.fromkeys([*raised, *shown]) if a not in hidden]
        open_now = (
            {
                str(r[0])
                for r in conn.execute(
                    "SELECT id FROM alerts WHERE list_contains(?, id) AND status = 'open'",
                    [announce],
                ).fetchall()
            }
            if announce
            else set()
        )
        events: list[Event] = [AlertRaised(alert_id=a) for a in announce if a in open_now]
        events += [AlertResolved(alert_id=a) for a in dict.fromkeys(resolved)]
        return events

    def apply_budget(self, conn: duckdb.DuckDBPyConnection) -> tuple[list[str], list[str]]:
        """Keep the best :attr:`budget` alerts ``open`` (severity, then impact, then oldest);
        the others are ``hidden_by_budget``. Returns ``(promoted, newly_hidden)`` ids.
        [DEV] one shift = the open alerts of the shop: the day-scoped keys and the
        restart/day-end resolution keep it to the current data day."""
        rows = conn.execute(
            "SELECT id, status, severity, coalesce(impact_vnd, 0), raised_at FROM alerts"
            " WHERE status IN ('open', 'hidden_by_budget') AND coalesce(shop_id, ?) = ?",
            [self.shop_id, self.shop_id],
        ).fetchall()
        ranked = sorted(
            rows, key=lambda r: (-SEV.get(str(r[2]), 0), -int(r[3]), r[4] or utcnow(), str(r[0]))
        )
        promoted: list[str] = []
        hidden: list[str] = []
        for i, r in enumerate(ranked):
            aid, status = str(r[0]), str(r[1])
            want = "open" if i < self.budget else "hidden_by_budget"
            if want == status:
                continue
            store.set_status(conn, aid, want)
            (promoted if want == "open" else hidden).append(aid)
        return promoted, hidden


# ---------------------------------------------------------------------- data
def _json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _sales_key(direction: str, product_id: str, day: date) -> str:
    kind = "sales_above_forecast" if direction == "up" else "sales_below_forecast"
    return f"{kind}:product:{product_id}:{day.isoformat()}"


def _merge_rows(old: Sequence[Any], new: Sequence[Any]) -> list[Any]:
    by: dict[str, Any] = {}
    for r in [*old, *new]:
        if isinstance(r, dict) and "sku" in r:
            by[str(r["sku"])] = {**by.get(str(r["sku"]), {}), **r}
    return [by[k] for k in sorted(by)]


def _data_as_of(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    from app.simulator.inputs import data_as_of

    return data_as_of(conn)


def _band(fc: ForecastResult, hours: Sequence[datetime]) -> tuple[float, float, float] | None:
    idx = {h: i for i, h in enumerate(fc.hours)}
    pos = [idx.get(h) for h in hours]
    if any(p is None for p in pos):
        return None
    ii = [p for p in pos if p is not None]
    return (
        float(sum(fc.p10[i] for i in ii)),
        float(sum(fc.p50[i] for i in ii)),
        float(sum(fc.p90[i] for i in ii)),
    )


@dataclass
class _Context:
    as_of: datetime
    day: date
    day0: datetime
    day_end: datetime
    last_hour: datetime
    info: dict[str, SkuInfo]
    sku_campaigns: dict[str, list[str]]
    ads: dict[str, AdState]
    stock: dict[str, int]
    stock_day0: dict[str, int]
    units: dict[tuple[str, datetime], int]
    stockout: dict[tuple[str, datetime], bool]
    baseline: dict[str, ForecastResult]
    current: dict[str, ForecastResult]

    @classmethod
    def load(cls, conn: duckdb.DuckDBPyConnection, as_of: datetime) -> _Context:
        last_hour = floor_hour(as_of - timedelta(microseconds=1))
        day0 = day_start(as_of)
        day = local_date(day0 + timedelta(hours=1))
        day_end = local_day_bounds_utc(day)[1]
        camps = campaign_map(conn)
        sku_campaigns: dict[str, list[str]] = {}
        for cid, skus in camps.items():
            for s in skus:
                sku_campaigns.setdefault(s, []).append(cid)
        info = {
            str(r[0]): SkuInfo(
                sku=str(r[0]),
                product_id=str(r[1]),
                name=str(r[2]),
                variant=r[3],
                list_price=int(r[4] or 0),
                is_bundle=bool(r[5]),
            )
            for r in conn.execute(
                "SELECT s.sku_id, s.product_id, coalesce(p.name, s.sku_id), s.variant_name,"
                " s.list_price, s.is_bundle FROM skus s LEFT JOIN products p USING (product_id)"
            ).fetchall()
        }
        ads: dict[str, AdState] = {}
        rows = conn.execute(
            "SELECT campaign_id, hour_utc, status, spend FROM ad_hourly WHERE hour_utc < ?"
            " QUALIFY row_number() OVER (PARTITION BY campaign_id ORDER BY hour_utc DESC) <= 3"
            " ORDER BY campaign_id, hour_utc DESC",
            [as_of],
        ).fetchall()
        spends: dict[str, list[float]] = {}
        for cid, hour, status, spend in rows:
            c = str(cid)
            if c not in ads:
                ads[c] = AdState(status=str(status), hour=hour, spend_per_hour=0.0)
            spends.setdefault(c, []).append(float(spend or 0))
        for c, v in spends.items():
            ads[c].spend_per_hour = sum(v) / len(v) if v else 0.0
        since = max(day0, last_hour - timedelta(hours=WINDOW_HOURS - 1))
        units: dict[tuple[str, datetime], int] = {}
        stockout: dict[tuple[str, datetime], bool] = {}
        stock: dict[str, int] = {}
        for sku, hour, u, se, so in conn.execute(
            "SELECT sku_id, hour_utc, units_gross, stock_end, stockout FROM hourly_panel"
            " WHERE hour_utc >= ? AND hour_utc <= ?",
            [since, last_hour],
        ).fetchall():
            units[(str(sku), hour)] = int(u or 0)
            stockout[(str(sku), hour)] = bool(so)
            if hour == last_hour and se is not None:
                stock[str(sku)] = int(se)
        skus = sorted(sku_campaigns)
        start = last_hour + timedelta(hours=1)
        current = {
            str(s): ForecastResult.model_validate_json(p)
            for s, p in conn.execute(
                "SELECT sku_id, payload FROM forecasts WHERE list_contains(?, sku_id)"
                " AND origin = ? QUALIFY row_number() OVER (PARTITION BY sku_id"
                " ORDER BY created_at DESC) = 1",
                [skus, start],
            ).fetchall()
        }
        return cls(
            as_of=as_of,
            day=day,
            day0=day0,
            day_end=day_end,
            last_hour=last_hour,
            info=info,
            sku_campaigns=sku_campaigns,
            ads=ads,
            stock=stock,
            stock_day0={
                str(r[0]): int(r[1])
                for r in conn.execute(
                    "SELECT sku_id, stock_begin FROM hourly_panel WHERE hour_utc = ?"
                    " AND stock_begin IS NOT NULL",
                    [day0],
                ).fetchall()
            },
            units=units,
            stockout=stockout,
            baseline=load_baseline(conn, skus, day0),
            current=current,
        )

    def window(self) -> list[datetime]:
        hours = [self.last_hour - timedelta(hours=k) for k in range(WINDOW_HOURS - 1, -1, -1)]
        return hours if hours[0] >= self.day0 else []

    def cover(self, sku: str, stock: float) -> float | None:
        """Hours until the cumulative p50 from the next hour reaches ``stock`` (current
        forecast, else the rest of the baseline). ``None`` = beyond the horizon."""
        if stock <= 0:
            return 0.0
        fc = self.current.get(sku)
        start = self.last_hour + timedelta(hours=1)
        if fc is not None and fc.hours and fc.hours[0] == start:
            for i, c in enumerate(fc.cum_p50):
                if c >= stock - 1e-9:
                    return float(i + 1)
            return None
        base = self.baseline.get(sku)
        if base is None:
            return None
        acc = 0.0
        n = 0
        for h, m in zip(base.hours, base.mean, strict=False):
            if h < start:
                continue
            acc += m
            n += 1
            if acc >= stock - 1e-9:
                return float(n)
        return None
