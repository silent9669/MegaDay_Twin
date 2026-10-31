"""Draft / write tools (07 §3): they never take effect by themselves.

* ``draft_decision`` → a ``decisions`` row in state ``draft`` (``created_via='ai'``, 10 §3);
  only for a persisted scenario without assumptions, and only a feasible action.
* ``request_approval`` → ``draft`` → ``pending_approval``; needs the UI confirmation flag.
* ``suggest_parameter`` → ``parameters`` row with ``set_by='ai_suggested'`` (not effective
  until a person confirms it, 05 §3).
* ``propose_column_mapping`` / ``propose_sku_matches`` / ``draft_shift_handoff`` → drafts
  kept in :data:`DRAFTS` (in memory) until the confirmation endpoints exist (M4-ai.md).

The registry runs these only when the user asked for a draft in their own words or the UI
set ``allow_drafts`` (07 §7: data content can never trigger a write).
"""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

from app.ai import guard
from app.ai.tools._util import iso, now_data, parse_ts, require_campaign
from app.ai.tools.registry import Source, ToolContext, ToolData, tool
from app.ai.tools.schemas import (
    ColumnMappingArgs,
    DraftDecisionArgs,
    HandoffArgs,
    ProposeSkuArgs,
    RequestApprovalArgs,
    SuggestParameterArgs,
)
from app.api.common import parse_json
from app.core.errors import NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.timeutil import utcnow
from app.economics.parameters import set_parameter
from app.governance import decisions as gov
from app.governance.audit import write_audit
from app.governance.roles import Actor
from app.simulator.schemas import ActionSpec
from app.warehouse.repo import fetch_all, fetch_one

_LOCK = threading.Lock()
DRAFTS: dict[str, dict[str, Any]] = {}

MONEY_FIELDS = {
    "list_price", "effective_price", "line_total", "daily_budget", "spend",
    "attributed_revenue", "unit_cost", "packaging_cost",
}  # fmt: skip
PII_FIELDS = {"buyer_name", "buyer_phone", "buyer_address"}


def _store(kind: str, ctx: ToolContext, payload: dict[str, Any]) -> str:
    did = new_id(kind)
    with _LOCK:
        DRAFTS[did] = {
            "draft_id": did,
            "kind": kind,
            "session_id": ctx.session_id,
            "user_id": ctx.user_id,
            "created_at": utcnow().isoformat(),
            "status": "pending_confirmation",
            "payload": payload,
        }
    return did


def actor_of(ctx: ToolContext) -> Actor:
    """Governance actor for an AI tool call: the user, ``via='ai'``, request = AI session."""
    return Actor(id=ctx.user_id, role=ctx.role, via="ai", request_id=ctx.session_id)  # type: ignore[arg-type]


def _audit(ctx: ToolContext, action: str, target_kind: str, target_id: str, after: Any) -> None:
    write_audit(
        ctx.conn,
        actor_of(ctx),
        action,
        target_kind,
        target_id,
        after=after,
        shop_id=ctx.settings.shop_id,
    )


@tool(
    "draft_decision",
    "Tạo BẢN NHÁP quyết định cho một chiến dịch từ một kết quả mô phỏng (không có giả định)."
    " Người dùng phải xem và gửi duyệt.",
    DraftDecisionArgs,
    kind="draft",
)
def draft_decision(args: DraftDecisionArgs, ctx: ToolContext) -> ToolData:
    """``governance.decisions.create_decision`` (snapshot + audit, ``created_via='ai'``).
    Errors stay ``NotFound`` / ``ValidationFailed`` / ``ConstraintViolation``."""
    require_campaign(ctx.conn, args.campaign_id)
    windows = [tuple(w) for w in args.action.windows_local] if args.action.windows_local else None
    try:
        action = ActionSpec(kind=args.action.kind, pct=args.action.pct, windows_local=windows)
    except ValueError as e:
        raise ValidationFailed(f"Hành động không hợp lệ: {e}") from e
    if (
        ctx.conn.execute(
            "SELECT 1 FROM scenarios WHERE scenario_id = ?", [args.scenario_id]
        ).fetchone()
        is None
    ):
        raise NotFound(
            "Không có kịch bản này trong hệ thống (kịch bản giả định không lưu được thành quyết"
            " định; hãy dùng ghi chú cho lead).",
            scenario_id=args.scenario_id,
        )
    d = gov.create_decision(
        ctx.conn,
        actor_of(ctx),
        campaign_id=args.campaign_id,
        scenario_id=args.scenario_id,
        action=action,
        rationale=args.rationale[:2000],
        shop_id=ctx.settings.shop_id,
    )
    return ToolData(
        data={
            "decision_id": d.id,
            "state": d.state,
            "action": d.action,
            "action_label": d.action_label or action.label_vi(),
            "override_of_recommendation": bool(d.override_of_recommendation),
            "message": "Đã tạo bản nháp. Người dùng cần xem và bấm Gửi duyệt.",
        },
        sources=[Source(id=d.id, kind="decision", label="Bản nháp quyết định")],
        requires_confirmation=True,
    )


@tool(
    "request_approval",
    "Chuyển bản nháp quyết định sang chờ lead duyệt. Chỉ chạy khi người dùng đã bấm Gửi duyệt.",
    RequestApprovalArgs,
    kind="write",
)
def request_approval(args: RequestApprovalArgs, ctx: ToolContext) -> ToolData:
    """``governance.decisions.submit``: draft → pending_approval. Not the creator → 403
    ``FORBIDDEN``; blocking data issue → 422; inputs changed since the scenario → 409
    ``STALE_INPUT``; wrong state → 409 ``STATE_CONFLICT`` (all reported to the LLM)."""
    d = gov.submit(ctx.conn, args.decision_id, actor_of(ctx))
    return ToolData(
        data={"decision_id": d.id, "state": d.state},
        sources=[Source(id=d.id, kind="decision", label="Quyết định chờ duyệt")],
        requires_confirmation=True,
    )


@tool(
    "suggest_parameter",
    "Gợi ý một giá trị tham số (ghi set_by=ai_suggested). Chưa có hiệu lực cho tới khi người"
    " dùng xác nhận.",
    SuggestParameterArgs,
    kind="draft",
)
def suggest_parameter(args: SuggestParameterArgs, ctx: ToolContext) -> ToolData:
    p = set_parameter(
        ctx.conn,
        args.key,
        args.value,
        scope=args.scope,
        scope_id=args.scope_id or "*",
        set_by="ai_suggested",
        reason=f"[AI gợi ý] {args.reason}"[:500],
    )
    _audit(ctx, "parameter.suggest", "parameter", args.key, {"version": p.version})
    return ToolData(
        data={
            "key": p.key,
            "scope": p.scope,
            "scope_id": p.scope_id,
            "suggested_value": p.value,
            "version": p.version,
            "set_by": p.set_by,
            "effective": False,
            "message": "Gợi ý chưa có hiệu lực; người dùng cần bấm Xác nhận.",
        },
        sources=[
            Source(id=f"param-{p.key}-v{p.version}", kind="parameter", label="Tham số AI gợi ý")
        ],
        requires_confirmation=True,
    )


@tool(
    "propose_column_mapping",
    "Đề xuất ghép cột của file vừa tải lên vào schema chuẩn. Người dùng phải xác nhận.",
    ColumnMappingArgs,
    kind="draft",
)
def propose_column_mapping(args: ColumnMappingArgs, ctx: ToolContext) -> ToolData:
    if (
        ctx.conn.execute(
            "SELECT 1 FROM ingest_batches WHERE batch_id = ?", [args.batch_id]
        ).fetchone()
        is None
    ):
        raise NotFound(f"Không có lần nạp {args.batch_id}.")
    rules = []
    mappings = []
    for m in args.mappings:
        d = m.model_dump()
        # hard rules run after the LLM (07 §4.1 step 4)
        if m.target_field in MONEY_FIELDS and m.transform != "parse_vnd":
            d["transform"] = "parse_vnd"
            rules.append(f"{m.source_column}: tiền luôn parse_vnd")
        if m.target_field in PII_FIELDS and m.transform != "strip_pii":
            d["transform"] = "strip_pii"
            rules.append(f"{m.source_column}: cột thông tin cá nhân luôn strip_pii")
        mappings.append(d)
    did = _store(
        "map", ctx, {"batch_id": args.batch_id, "file_kind": args.file_kind, "mappings": mappings}
    )
    return ToolData(
        data={"draft_id": did, "mappings": mappings, "rules_applied": rules},
        sources=[Source(id=did, kind="draft", label="Nháp ghép cột")],
        requires_confirmation=True,
    )


@tool(
    "propose_sku_matches",
    "Đề xuất ghép tên/mã hàng chưa ghép với SKU sàn. Người dùng phải xác nhận.",
    ProposeSkuArgs,
    kind="draft",
)
def propose_sku_matches(args: ProposeSkuArgs, ctx: ToolContext) -> ToolData:
    out = []
    for m in args.matches:
        alias = fetch_one(
            ctx.conn, "SELECT alias_id FROM sku_aliases WHERE alias_id = ?", [m.alias]
        )
        sku = ctx.conn.execute("SELECT 1 FROM skus WHERE sku_id = ?", [m.sku]).fetchone()
        out.append(
            {
                **m.model_dump(),
                "valid": alias is not None and sku is not None,
                "flag": "auto"
                if m.confidence >= 0.9
                else "check"
                if m.confidence >= 0.6
                else "low",
            }
        )
    did = _store("skumatch", ctx, {"batch_id": args.batch_id, "matches": out})
    return ToolData(
        data={"draft_id": did, "matches": out},
        sources=[Source(id=did, kind="draft", label="Nháp ghép SKU")],
        requires_confirmation=True,
    )


@tool(
    "draft_shift_handoff",
    "Soạn nháp ghi chú bàn giao ca: quyết định đã duyệt, việc chờ duyệt, cảnh báo mở, dữ liệu"
    " trễ. Người dùng sửa rồi lưu.",
    HandoffArgs,
    kind="draft",
)
def draft_shift_handoff(args: HandoffArgs, ctx: ToolContext) -> ToolData:
    end = parse_ts(args.to, now_data(ctx.conn))
    start = parse_ts(args.from_, end - timedelta(hours=8))
    decisions = fetch_all(
        ctx.conn,
        "SELECT id, target_id, action, state, rationale FROM decisions WHERE created_at >= ?"
        " ORDER BY created_at",
        [start],
    )
    alerts = fetch_all(
        ctx.conn,
        "SELECT id, kind, target_id, title, severity FROM alerts WHERE status = 'open'"
        " ORDER BY raised_at DESC LIMIT 20",
    )
    sections = {
        "approved": [d for d in decisions if d["state"] in ("approved", "sent", "verified")],
        "pending": [d for d in decisions if d["state"] in ("draft", "pending_approval")],
        "open_alerts": alerts,
    }
    for sec in ("approved", "pending"):
        for d in sections[sec]:
            d["action"] = parse_json(d["action"])
            d["rationale"] = guard.wrap(d["rationale"], "ly_do_quyet_dinh")
    did = _store("handoff", ctx, {"from": iso(start), "to": iso(end), **sections})
    return ToolData(
        data={"draft_id": did, "from": iso(start), "to": iso(end), **sections},
        sources=[Source(id=did, kind="draft", label="Nháp ghi chú bàn giao", as_of=iso(end))],
        requires_confirmation=True,
    )


def handoff_text(payload: dict[str, Any]) -> str:
    """Plain-text note from a ``draft_shift_handoff`` payload (what is saved by default)."""
    lines = [f"Bàn giao ca {payload.get('from')} → {payload.get('to')}"]
    for title, key in (
        ("Quyết định đã duyệt", "approved"),
        ("Việc chờ duyệt", "pending"),
        ("Cảnh báo còn mở", "open_alerts"),
    ):
        items = payload.get(key) or []
        lines.append(f"{title}: {len(items)}")
        for it in items[:20]:
            label = it.get("title") or it.get("target_id") or ""
            lines.append(f"- {it.get('id')} {label}".rstrip())
    return "\n".join(lines)


def save_handoff(
    conn: Any, actor: Actor, draft_id: str, note: str | None = None, shop_id: str = "shop-demo"
) -> gov.Decision:
    """A person saves the handoff draft: ``decisions`` row ``kind='handoff_note'`` (07 §4.5)."""
    d = DRAFTS.get(draft_id)
    if d is None or d["kind"] != "handoff":
        raise NotFound("Không có nháp bàn giao này.", draft_id=draft_id)
    text = note.strip() if note and note.strip() else handoff_text(d["payload"])
    return gov.create_decision(conn, actor, kind="handoff_note", rationale=text, shop_id=shop_id)
