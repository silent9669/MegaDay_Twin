"""Investigation agent "vì sao bán chậm?" (AI-07, 07 §4.3).

Floor of the plan (12: "checklist chạy không cần LLM, LLM viết tóm tắt"):
:func:`run_checklist` walks the fixed 6-item checklist with the **real tools** (each call
logged in ``ai_tool_calls``) and gives every item a verdict
``ok | nghi_van | nguyen_nhan | chua_kiem_duoc``. The result is handed to the LLM as the
output of a synthetic tool ``investigation_checklist``; the LLM writes the summary and the
grounding check verifies it against all of these outputs. :func:`summarize_payload` writes
the same summary without an LLM (AI off) and is what the scripted FakeClient answers.

1. Dữ liệu trễ/thiếu        — get_data_freshness, check_data_quality
2. Biến thể hết hàng         — get_sku_snapshot (mọi biến thể), get_inventory_events
3. Ads tắt / hết ngân sách   — get_campaign_status
4. Giá / voucher đổi         — get_price_events
5. SKU tương tự cùng giảm?   — compare_similar_skus
6. Ghi chú người mua         — search_order_notes (dữ liệu không tin cậy)
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from unidecode import unidecode

from app.ai.agent import Agent, TurnResult
from app.ai.client import LlmClient, LlmRequest, LlmResponse, tool_outputs
from app.ai.tools import Source, ToolContext, ToolResult, execute
from app.ai.tools._util import iso, now_data
from app.core.ids import new_id
from app.core.timeutil import local_date, local_day_bounds_utc, to_local
from app.governance.alerts import set_investigation

Verdict = Literal["ok", "nghi_van", "nguyen_nhan", "chua_kiem_duoc"]
CHECKLIST_TOOL = "investigation_checklist"
_STOP = {"vi", "sao", "ban", "cham", "the", "nao", "hom", "nay", "giam", "lai", "khong", "co"}


class CheckItem(BaseModel):
    no: int
    check: str
    tools: list[str]
    verdict: Verdict
    finding: str
    skus: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class InvestigationReport(BaseModel):
    product_id: str
    skus: list[str]
    campaign_id: str | None
    as_of: str | None
    window_hours: int
    checklist: list[CheckItem]
    n_checked: int
    primary_cause: dict[str, Any] | None
    details: dict[str, Any]
    suggestion: dict[str, Any] | None


def find_product(ctx: ToolContext, text: str) -> str | None:
    """Product whose (unaccented) name best matches the question."""
    q = {w for w in unidecode(text).lower().replace("?", " ").split() if w not in _STOP}
    best: tuple[float, int, str] | None = None
    for pid, name in ctx.conn.execute("SELECT product_id, name FROM products").fetchall():
        toks = set(unidecode(str(name)).lower().split())
        if not toks:
            continue
        hit = len(q & toks)
        if hit == 0:
            continue
        score = hit / len(toks)
        cand = (score, hit, str(pid))
        if best is None or cand[:2] > best[:2]:
            best = cand
    if best is not None and best[1] >= 2:
        return best[2]
    up = text.upper()
    for (pid,) in ctx.conn.execute("SELECT product_id FROM products").fetchall():
        if str(pid) in up:
            return str(pid)
    return None


def run_checklist(
    ctx: ToolContext,
    *,
    product_id: str | None = None,
    sku: str | None = None,
    question: str = "",
    window_hours: int = 3,
) -> tuple[InvestigationReport, list[ToolResult]]:
    conn = ctx.conn
    if product_id is None and sku is not None:
        row = conn.execute("SELECT product_id FROM skus WHERE sku_id = ?", [sku]).fetchone()
        product_id = str(row[0]) if row else None
    if product_id is None:
        product_id = find_product(ctx, question)
    if product_id is None:
        raise ValueError("Không xác định được sản phẩm cần điều tra.")
    variants = [
        str(r[0])
        for r in conn.execute(
            "SELECT sku_id FROM skus WHERE product_id = ? AND NOT is_bundle ORDER BY sku_id",
            [product_id],
        ).fetchall()
    ]
    results: list[ToolResult] = []

    def run(name: str, args: dict[str, Any]) -> ToolResult:
        res = execute(name, args, ctx)
        results.append(res)
        return res

    as_of = now_data(conn)
    day_start = local_day_bounds_utc(local_date(as_of - timedelta(microseconds=1)))[0]
    items: list[CheckItem] = []
    details: dict[str, Any] = {}

    # 1. data freshness / quality
    fr = run("get_data_freshness", {})
    dq = [run("check_data_quality", {"sku": v, "severity": None}) for v in variants]
    stale = [s for s in fr.data.get("sources", []) if s.get("status") == "stale"]
    stale_core = [s["source"] for s in stale if s["source"] in ("orders", "inventory")]
    ads: dict[str, Any] = next(
        (s for s in fr.data.get("sources", []) if s.get("source") == "ads"), {}
    )
    blocking = sum(int(r.data.get("blocking", 0)) for r in dq if r.ok)
    if not fr.ok:
        v1: Verdict = "chua_kiem_duoc"
        f1 = "Không đọc được độ mới dữ liệu."
    elif stale_core:
        v1, f1 = "nguyen_nhan", f"Dữ liệu {', '.join(stale_core)} đang trễ, số bán có thể chưa đủ."
    elif stale or blocking:
        v1 = "nghi_van"
        f1 = "Có nguồn trễ hoặc vấn đề dữ liệu chặn tính toán."
    else:
        v1 = "ok"
        f1 = (
            f"Đơn và tồn đúng giờ; ads trễ {ads.get('lag_minutes')} phút (bình thường)."
            if ads.get("lag_minutes") is not None
            else "Đơn và tồn đúng giờ."
        )
    items.append(
        CheckItem(
            no=1,
            check="Dữ liệu có trễ hoặc thiếu không",
            tools=["get_data_freshness", "check_data_quality"],
            verdict=v1,
            finding=f1,
            source_ids=[*fr.source_ids, *(s for r in dq for s in r.source_ids)],
        )
    )

    # 2. variants out of stock
    snaps = {v: run("get_sku_snapshot", {"sku": v}) for v in variants}
    out = [
        v
        for v, r in snaps.items()
        if r.ok
        and (
            (r.data.get("stock_available") is not None and r.data["stock_available"] <= 0)
            or r.data.get("stockout_now")
        )
    ]
    low = [
        v
        for v, r in snaps.items()
        if r.ok and v not in out and ((r.data.get("hours_of_cover") or {}).get("p50") or 99) < 3
    ]
    inv: dict[str, ToolResult] = {}
    for v in out or low:
        inv[v] = run("get_inventory_events", {"sku": v, "from": iso(day_start), "to": None})
    campaign_id = next(
        (
            (r.data.get("campaign") or {}).get("id")
            for r in snaps.values()
            if r.ok and r.data.get("campaign")
        ),
        None,
    )
    if out:
        v = out[0]
        ev = inv[v].data if inv[v].ok else {}
        noted = [e for e in ev.get("events", []) if e.get("note")]
        since = ev.get("stockout_since")
        details["stockout"] = {
            "sku": v,
            "stock_now": snaps[v].data.get("stock_available"),
            "since": since,
            "snapshot_source": snaps[v].source_ids[0] if snaps[v].sources else None,
            "inventory_source": inv[v].source_ids[0] if inv[v].sources else None,
            "adjustment": (
                {"t": noted[0]["t"], "change": noted[0]["change"], "flags": noted[0]["flags"]}
                if noted
                else None
            ),
            "incoming": snaps[v].data.get("incoming") or [],
        }
        since_txt = f" từ {to_local_hm(since)}" if since else ""
        f2 = f"{v} hết hàng (tồn {snaps[v].data.get('stock_available')}){since_txt}."
        if noted:
            f2 += f" Kho có ghi chú điều chỉnh tồn lúc {to_local_hm(noted[0]['t'])}."
        v2: Verdict = "nguyen_nhan"
    elif low:
        v2, f2 = "nghi_van", f"Sắp hết hàng: {', '.join(low)}."
    else:
        v2, f2 = "ok", "Mọi biến thể còn hàng."
    items.append(
        CheckItem(
            no=2,
            check="Có biến thể hoặc SKU hết hàng không",
            tools=["get_sku_snapshot", "get_inventory_events"],
            verdict=v2,
            finding=f2,
            skus=out or low,
            source_ids=[s for r in [*snaps.values(), *inv.values()] for s in r.source_ids],
        )
    )

    # 3. ads
    if campaign_id:
        cs = run("get_campaign_status", {"campaign_id": campaign_id})
        st3 = cs.data.get("status_last_3h", []) if cs.ok else []
        if not cs.ok:
            v3: Verdict = "chua_kiem_duoc"
            f3 = "Không đọc được trạng thái chiến dịch."
        elif any(s in ("paused", "budget_exhausted") for s in st3):
            v3, f3 = "nguyen_nhan", f"Ads {campaign_id} bị tắt hoặc hết ngân sách gần đây."
        else:
            v3 = "ok"
            seen = to_local_hm(cs.data.get("ads_as_of"), -1)
            f3 = f"Ads {campaign_id} vẫn chạy (số liệu ads tới {seen})."
        details["campaign"] = {
            "id": campaign_id,
            "status": cs.data.get("status_latest_known") if cs.ok else None,
            "ads_as_of": cs.data.get("ads_as_of") if cs.ok else None,
            "source": cs.source_ids[0] if cs.sources else None,
        }
        src3 = list(cs.source_ids)
    else:
        v3, f3, src3 = "chua_kiem_duoc", "Sản phẩm không thuộc chiến dịch nào.", []
    items.append(
        CheckItem(
            no=3,
            check="Ads có bị tắt, hết ngân sách không",
            tools=["get_campaign_status"],
            verdict=v3,
            finding=f3,
            source_ids=src3,
        )
    )

    # 4. price / voucher
    prices = [run("get_price_events", {"sku": v, "from": None, "to": None}) for v in variants]
    up = [p.data["sku"] for p in prices if p.ok and p.data.get("price_up")]
    eff = next(
        (p.data["days"][-1]["price_effective"] for p in prices if p.ok and p.data.get("days")),
        None,
    )
    items.append(
        CheckItem(
            no=4,
            check="Giá, voucher có đổi không",
            tools=["get_price_events"],
            verdict="nghi_van"
            if up
            else ("ok" if prices and all(p.ok for p in prices) else "chua_kiem_duoc"),
            finding=(
                f"Giá tăng ở {', '.join(up)}."
                if up
                else f"Giá không tăng; giá ưu đãi hiện tại {fmt_vnd(eff)}."
                if eff
                else "Giá không tăng."
            ),
            skus=up,
            source_ids=[s for p in prices for s in p.source_ids],
        )
    )

    # 5. similar SKUs
    target = out[0] if out else (variants[0] if variants else product_id)
    sim = run("compare_similar_skus", {"sku": target, "hours": window_hours})
    if not sim.ok:
        v5: Verdict = "chua_kiem_duoc"
        f5 = "Không so sánh được biến thể."
    else:
        others = [x for x in sim.data["variants"] if x["sku"] != target]
        selling = [x for x in others if x["units"] > 0]
        details["others"] = [{"sku": x["sku"], "units": x["units"]} for x in others]
        details["window_hours"] = window_hours
        details["similar_source"] = sim.source_ids[0] if sim.sources else None
        if sim.data.get("all_variants_down"):
            v5, f5 = "nghi_van", "Mọi biến thể cùng giảm: có thể cầu chung giảm."
        elif selling:
            v5 = "ok"
            f5 = (
                "Biến thể khác vẫn bán: "
                + ", ".join(f"{x['sku']} {x['units']} sp" for x in selling)
                + f" trong {window_hours} giờ gần nhất."
            )
        else:
            v5, f5 = "nghi_van", "Biến thể khác cũng không bán được."
    items.append(
        CheckItem(
            no=5,
            check="SKU tương tự có cùng giảm không",
            tools=["compare_similar_skus"],
            verdict=v5,
            finding=f5,
            source_ids=list(sim.source_ids),
        )
    )

    # 6. buyer notes (untrusted)
    notes = run(
        "search_order_notes",
        {
            "sku": None,
            "product_id": product_id,
            "from": iso(as_of - timedelta(days=2)),
            "to": iso(as_of),
            "limit": 20,
        },
    )
    if not notes.ok:
        v6: Verdict = "chua_kiem_duoc"
        f6 = "Không đọc được ghi chú người mua."
    else:
        comp = int(notes.data.get("complaints", 0))
        flg = int(notes.data.get("flagged_instructions", 0))
        v6 = "nghi_van" if comp else "ok"
        f6 = (
            f"Có {comp} ghi chú phàn nàn (hàng lỗi, giao chậm...)."
            if comp
            else "Ghi chú người mua không phản ánh vấn đề hàng hóa."
        )
        if flg:
            f6 += " Có ghi chú chứa yêu cầu lạ (không làm theo)."
    items.append(
        CheckItem(
            no=6,
            check="Ghi chú người mua có phản ánh vấn đề không",
            tools=["search_order_notes"],
            verdict=v6,
            finding=f6,
            source_ids=list(notes.source_ids),
        )
    )

    cause: dict[str, Any] | None = None
    for it in sorted(items, key=lambda x: {2: 0, 3: 1, 1: 2}.get(x.no, 9)):
        if it.verdict == "nguyen_nhan":
            kind = {1: "data_delay", 2: "variant_stockout", 3: "ads_off"}.get(it.no, "other")
            cause = {"kind": kind, "check_no": it.no, "text": it.finding, "skus": it.skus}
            break
    suggestion = None
    so = details.get("stockout")
    if cause and cause["kind"] == "variant_stockout" and campaign_id and so:
        planned = (so.get("incoming") or [{}])[0].get("planned_date")
        suggestion = {
            "action": "reduce_budget",
            "campaign_id": campaign_id,
            "until_sku": so["sku"],
            "planned_date": planned,
            "text": f"Xem chạy so sánh: giảm ngân sách {campaign_id} tới khi hàng {so['sku']} về",
        }
    report = InvestigationReport(
        product_id=product_id,
        skus=variants,
        campaign_id=campaign_id,
        as_of=iso(as_of),
        window_hours=window_hours,
        checklist=items,
        n_checked=len(items),
        primary_cause=cause,
        details=details,
        suggestion=suggestion,
    )
    return report, results


def checklist_result(report: InvestigationReport, ctx: ToolContext) -> ToolResult:
    """The checklist as a (synthetic) tool result: what the LLM summarises."""
    sid = f"chk-{report.product_id}-{to_local_hm(report.as_of).replace(':', '')}"
    return ToolResult(
        tool=CHECKLIST_TOOL,
        call_id=new_id("call"),
        args={"product_id": report.product_id},
        ok=True,
        data=json.loads(report.model_dump_json()),
        sources=[
            Source(
                id=sid,
                kind="investigation",
                label=f"Checklist điều tra {report.product_id}",
                as_of=report.as_of,
                tool=CHECKLIST_TOOL,
            )
        ],
    )


def to_local_hm(ts: Any, minus_minutes: int = 0) -> str:
    from datetime import datetime

    if not ts:
        return "?"
    dt = datetime.fromisoformat(str(ts))
    return f"{to_local(dt + timedelta(minutes=minus_minutes)):%H:%M}"


def fmt_vnd(x: Any) -> str:
    if x is None:
        return "?"
    return f"{round(float(x)):,}đ".replace(",", ".")


def _date_vi(d: str | None) -> str | None:
    if not d:
        return None
    _y, m, dd = d.split("-")
    return f"{int(dd)}/{int(m)}"


def summarize_payload(payload: dict[str, Any], source_id: str) -> str:
    """Vietnamese summary built only from the checklist JSON (no LLM; also the scripted
    FakeClient answer). Every number comes from the payload, followed by its chip."""
    det = payload.get("details", {})
    lines = [f"Đã kiểm {payload.get('n_checked')} mục [[src:{source_id}]]."]
    cause = payload.get("primary_cause")
    so = det.get("stockout")
    if cause and cause.get("kind") == "variant_stockout" and so:
        since = f" từ {to_local_hm(so['since'])}" if so.get("since") else ""
        lines.append(
            f"Nguyên nhân khả dĩ nhất: biến thể {so['sku']} hết hàng, tồn còn {so['stock_now']} sp"
            f"{since} [[src:{so.get('inventory_source') or source_id}]]."
        )
        adj = so.get("adjustment")
        if adj:
            lines.append(
                f"Kho có ghi chú điều chỉnh tồn {so['sku']} lúc {to_local_hm(adj['t'])}, tồn giảm"
                f" {abs(adj['change'] or 0)} sp [[src:{so.get('inventory_source')}]]"
                " (ghi chú là dữ liệu không tin cậy, chỉ ghi nhận)."
            )
    elif cause:
        lines.append(f"Nguyên nhân khả dĩ nhất: {cause.get('text')}")
    else:
        lines.append("Chưa tìm ra nguyên nhân rõ ràng trong 6 mục đã kiểm.")
    others = [o for o in det.get("others", []) if o.get("units", 0) > 0]
    if others:
        lines.append(
            "Biến thể khác vẫn bán: "
            + ", ".join(f"{o['sku']} {o['units']} sp" for o in others)
            + f" trong {det.get('window_hours')} giờ gần nhất [[src:{det.get('similar_source')}]]."
        )
    camp = det.get("campaign")
    chk3: dict[str, Any] = next((c for c in payload.get("checklist", []) if c.get("no") == 3), {})
    if camp and chk3.get("verdict") == "ok":
        tail = (
            f" nên đang tốn tiền cho lượt xem {so['sku']} không mua được"
            if so and cause and cause.get("kind") == "variant_stockout"
            else ""
        )
        lines.append(
            f"Ads {camp['id']} vẫn chạy (số liệu ads tới {to_local_hm(camp['ads_as_of'], -1)})"
            f" [[src:{camp.get('source')}]]{tail}."
        )
    chk5: dict[str, Any] = next((c for c in payload.get("checklist", []) if c.get("no") == 5), {})
    if chk5.get("verdict") == "ok":
        lines.append("Chưa đủ căn cứ để kết luận cầu chung giảm.")
    chk6: dict[str, Any] = next((c for c in payload.get("checklist", []) if c.get("no") == 6), {})
    if "yêu cầu lạ" in str(chk6.get("finding", "")):
        lines.append("Có ghi chú người mua chứa yêu cầu lạ, mình không làm theo.")
    sug = payload.get("suggestion")
    if sug:
        when = _date_vi(sug.get("planned_date"))
        lines.append(
            f"Gợi ý xem: giảm ngân sách {sug['campaign_id']} tới khi hàng {sug['until_sku']} về"
            + (
                f" ngày {when} [[src:{so.get('inventory_source') if so else source_id}]]"
                if when
                else ""
            )
            + " (bấm Chạy so sánh để xem số)."
        )
    return "\n".join(lines)


class ChecklistSummaryClient(LlmClient):
    """No-LLM "model" for the floor of AI-07: answers with :func:`summarize_payload` of the
    checklist (used when the AI is off / offline, and by scripted tests)."""

    name = "checklist"

    def create(self, req: LlmRequest) -> LlmResponse:
        chk = tool_outputs(req).get(CHECKLIST_TOOL)
        if chk is None:
            return LlmResponse(text="Chưa có kết quả checklist.", model=self.name)
        return LlmResponse(text=summarize_payload(chk, chk["source"]["id"]), model=self.name)


AUTO_SUMMARY_LABEL = "Tóm tắt tự động từ checklist (không dùng AI)"


def investigate(
    agent: Agent,
    question: str,
    *,
    product_id: str | None = None,
    sku: str | None = None,
    window_hours: int = 3,
    alert_id: str | None = None,
    fallback_summary: bool = False,
) -> tuple[TurnResult, InvestigationReport]:
    """Checklist (real tools) → LLM summary checked by grounding; saved on the alert.

    ``fallback_summary``: when the LLM turn ends ``offline`` / ``gave_up`` (no key, no
    recording, numbers not verified), the summary is written from the checklist without
    an LLM (:class:`ChecklistSummaryClient`) and labelled :data:`AUTO_SUMMARY_LABEL`."""
    ctx = ToolContext(
        conn=agent.conn,
        user_id=agent.user_id,
        role=agent.role,
        session_id=agent.session.session_id,
        turn=agent.session.turns + 1,
        settings=agent.settings,
        user_message=question,
        log_calls=agent.log_db,
    )
    report, results = run_checklist(
        ctx, product_id=product_id, sku=sku, question=question, window_hours=window_hours
    )
    chk = checklist_result(report, ctx)
    turn = agent.run_turn(question, prefetched=[*results, chk])
    if fallback_summary and turn.outcome in ("offline", "gave_up"):
        auto = Agent(
            ChecklistSummaryClient(),
            agent.conn,
            settings=agent.settings,
            flow="investigate",
            user_id=agent.user_id,
            role=agent.role,
            session_id=agent.session.session_id,
            log_db=agent.log_db,
            model=ChecklistSummaryClient.name,
        )
        turn = auto.run_turn(question, prefetched=[*results, chk])
        turn.label = AUTO_SUMMARY_LABEL
    if alert_id is not None:
        set_investigation(
            agent.conn,
            alert_id,
            {
                "checklist": json.loads(report.model_dump_json()),
                "summary": turn.text,
                "citations": [c.model_dump() for c in turn.citations],
                "grounding_ok": turn.grounding_ok,
                "label": turn.label,
                "session_id": turn.session_id,
            },
        )
    return turn, report
