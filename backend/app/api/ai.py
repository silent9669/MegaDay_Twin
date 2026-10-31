"""AI endpoints (API-07 phần AI, 08 §3.12, 07 §4).

* ``POST /ai/chat`` — one assistant turn. JSON once by default (floor of API-07); with
  ``Accept: text/event-stream`` (or ``?stream=true``) the same turn as SSE events
  ``session → tool_call / tool_result / rewrite … → answer → done`` (08 §3.12). The answer
  is never streamed word by word: it must pass the grounding check first.
  Response: text with ``[[src:id]]`` chips, ``sources`` (chip objects), ``grounding`` (badge
  + per-number table), ``tool_calls``, ``drafts`` waiting for a person, ``outcome``,
  ``replayed`` / ``label`` (``AI_MODE=replay``).
  ``flow=investigate`` runs the 6-step checklist without the LLM (AI-07 floor); the LLM only
  writes the summary, and when it cannot (no key, no recording) the summary is written from
  the checklist itself.
* ``GET /ai/sessions/{id}`` — turns + tool calls of a session (Nhật ký / Trợ lý).
* ``GET /ai/drafts`` · ``POST /ai/drafts/{id}/confirm`` · ``POST /ai/drafts/{id}/reject`` —
  drafts made by the AI only take effect when a person confirms them (07 §1 rule 2).
* ``POST /ai/investigate`` — checklist + summary for a SKU / product (button "Vì sao?").
* ``GET /ai/status`` — AI on/off, mode, whether a key is configured (never the key).

Roles (10 §1): chat / drafts / investigate need ``operator``; reading a session ``viewer``.
No key in ``AI_MODE=live|record`` → 503 ``AI_UNAVAILABLE`` with the fix in the message;
``AI_ENABLED=false`` → 503 too, every other screen keeps working.
"""

from __future__ import annotations

import json
import queue
import re
import threading
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Annotated, Any, Literal

import duckdb
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.ai.agent import Agent, ToolCallInfo, TurnResult, get_session
from app.ai.client import LlmClient, make_client
from app.ai.flows import FLOWS
from app.ai.flows.investigate import AUTO_SUMMARY_LABEL, investigate
from app.ai.grounding import Citation, NumberCheck
from app.ai.tools import draft as draft_tools
from app.api.common import data_as_of
from app.api.deps import Conn, Operator, User, Viewer
from app.api.schemas import Envelope, Meta, SourceRef
from app.core.config import Settings
from app.core.errors import (
    AiUnavailable,
    AppError,
    Forbidden,
    NotFound,
    StateConflict,
    ValidationFailed,
)
from app.core.logging import get_logger
from app.core.timeutil import utcnow
from app.economics.parameters import can_edit, confirm_suggestion
from app.governance import decisions as gov
from app.governance.audit import write_audit
from app.governance.roles import Actor
from app.warehouse.db import Database
from app.warehouse.repo import fetch_all, fetch_one

log = get_logger(__name__)
router = APIRouter(prefix="/ai", tags=["ai"])

FlowName = Literal["chat", "whatif", "investigate", "explain", "handoff", "ingest"]
ClientFactory = Callable[[Settings], LlmClient]
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")

# draft tool → (kind shown to the UI, label)
DRAFT_TOOLS: dict[str, tuple[str, str]] = {
    "draft_decision": ("decision", "Bản nháp quyết định"),
    "suggest_parameter": ("parameter", "Tham số AI gợi ý"),
    "propose_column_mapping": ("column_mapping", "Nháp ghép cột"),
    "propose_sku_matches": ("sku_match", "Nháp ghép SKU"),
    "draft_shift_handoff": ("handoff", "Nháp ghi chú bàn giao"),
}
_LOCK = threading.Lock()
_DRAFTS: dict[str, dict[str, Any]] = {}  # draft id → index entry (session, tool, args)


# ---------------------------------------------------------------------- models
class ChatContext(BaseModel):
    """What the user is looking at (screen context); ids only, validated."""

    campaign_id: str | None = None
    sku: str | None = None
    product_id: str | None = None
    scenario_id: str | None = None
    alert_id: str | None = None
    screen: str | None = None

    @field_validator("campaign_id", "sku", "product_id", "scenario_id", "alert_id", "screen")
    @classmethod
    def _id(cls, v: str | None) -> str | None:
        if v is not None and not _ID_RE.match(v):
            raise ValueError("Mã không hợp lệ (chỉ chữ, số, '-' và '_').")
        return v


class ChatRequest(BaseModel):
    session_id: str | None = None
    flow: FlowName = "chat"
    message: str = Field(min_length=1, max_length=2000)
    context: ChatContext = Field(default_factory=ChatContext)
    allow_drafts: bool = Field(
        False, description="Nút 'Lưu thành bản nháp' trên UI (cho phép công cụ nháp)"
    )


class ChatSource(BaseModel):
    """Citation chip: ``[[src:<id>]]`` in ``text`` ↔ one entry here."""

    id: str
    kind: str | None = None
    label: str | None = None
    data_as_of: str | None = None
    tool: str | None = None
    href: str | None = None
    mentions: list[str] = Field(default_factory=list)


class GroundingOut(BaseModel):
    ok: bool
    checked: int
    passed: int
    badge: str
    rewrite_count: int
    violations: list[str]
    numbers: list[NumberCheck]


class DraftOut(BaseModel):
    id: str
    kind: str  # decision | parameter | column_mapping | sku_match | handoff
    label: str
    tool: str
    status: str  # pending_confirmation | confirmed | rejected | <decision state>
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    saved_decision_id: str | None = None  # handoff saved as decisions.kind='handoff_note'
    confirm_path: str
    reject_path: str


class ChatTurn(BaseModel):
    session_id: str
    turn: int
    flow: str
    outcome: str
    text: str
    sources: list[ChatSource]
    grounding: GroundingOut | None
    tool_calls: list[ToolCallInfo]
    drafts: list[DraftOut]
    policy_violations: list[str]
    untrusted_flags: int
    replayed: bool
    replay_match: str | None
    label: str | None
    model: str | None
    usage: dict[str, int]
    ai_mode: str
    investigation: dict[str, Any] | None = None


class SessionTurn(BaseModel):
    turn: int
    at: datetime | None
    question: str | None
    answer: ChatTurn | None


class SessionToolCall(BaseModel):
    id: str
    turn: int | None
    tool: str
    args: Any
    result_summary: Any
    result_source_id: str | None
    ok: bool | None
    error: str | None
    duration_ms: int | None
    at: datetime | None


class SessionOut(BaseModel):
    session_id: str
    user_id: str | None
    role: str | None
    flow: str | None
    started_at: datetime | None
    model: str | None
    input_tokens: int
    output_tokens: int
    grounding_ok: bool | None
    outcome: str | None
    turns: list[SessionTurn]
    tool_calls: list[SessionToolCall]
    drafts: list[DraftOut]
    in_memory: bool


class AiStatus(BaseModel):
    enabled: bool
    mode: str
    available: bool
    has_key: bool
    models: dict[str, str | None]
    recordings: dict[str, int]
    message: str | None = None


class InvestigateRequest(BaseModel):
    sku: str | None = None
    product_id: str | None = None
    question: str | None = Field(None, max_length=500)
    alert_id: str | None = None
    window_hours: int = Field(3, ge=1, le=24)
    session_id: str | None = None

    @field_validator("sku", "product_id", "alert_id")
    @classmethod
    def _id(cls, v: str | None) -> str | None:
        if v is not None and not _ID_RE.match(v):
            raise ValueError("Mã không hợp lệ.")
        return v


class DraftDecisionBody(BaseModel):
    reason: str | None = Field(None, max_length=500)


class ConfirmBody(BaseModel):
    note: str | None = Field(None, max_length=4000, description="Bàn giao: nội dung đã sửa")


# ---------------------------------------------------------------------- helpers
def _settings(request: Request) -> Settings:
    s: Settings = request.app.state.settings
    return s


def client_factory(request: Request) -> ClientFactory:
    """``app.state.ai_client_factory`` (tests: FakeClient) or :func:`make_client`."""
    f: ClientFactory | None = getattr(request.app.state, "ai_client_factory", None)
    return f or (lambda s: make_client(s))


def _context_text(ctx: ChatContext) -> str:
    parts = []
    if ctx.screen:
        parts.append(f"màn {ctx.screen}")
    if ctx.campaign_id:
        parts.append(f"chiến dịch {ctx.campaign_id}")
    if ctx.sku:
        parts.append(f"SKU {ctx.sku}")
    if ctx.product_id:
        parts.append(f"sản phẩm {ctx.product_id}")
    if ctx.scenario_id:
        parts.append(f"kịch bản {ctx.scenario_id}")
    return ", ".join(parts) if parts else "không có"


def _check_session(session_id: str | None, user: User) -> None:
    if session_id is None:
        return
    st = get_session(session_id)
    if st is None:
        raise NotFound(
            "Không có phiên trợ lý này (phiên cũ mất khi khởi động lại). Hãy bắt đầu phiên mới.",
            session_id=session_id,
        )
    if st.user_id != user.id:
        raise Forbidden("Phiên trợ lý này thuộc người dùng khác.", session_id=session_id)


def _source(c: Citation) -> ChatSource:
    return ChatSource(
        id=c.source_id,
        kind=c.kind,
        label=c.label,
        data_as_of=c.as_of,
        tool=c.tool,
        href=c.href,
        mentions=c.mentions,
    )


def _meta_sources(sources: list[ChatSource]) -> list[SourceRef]:
    out = []
    for s in sources:
        try:
            out.append(
                SourceRef(
                    id=s.id,
                    kind=s.kind or "source",
                    label=s.label or s.id,
                    data_as_of=s.data_as_of,
                    tool=s.tool,
                    href=s.href,
                )
            )
        except ValueError:
            out.append(SourceRef(id=s.id, kind=s.kind or "source", label=s.label or s.id))
    return out


def _register_drafts(turn: TurnResult) -> None:
    for c in turn.tool_calls:
        if c.tool in DRAFT_TOOLS and c.ok and c.requires_confirmation and c.source_ids:
            with _LOCK:
                _DRAFTS.setdefault(
                    c.source_ids[0],
                    {
                        "id": c.source_ids[0],
                        "tool": c.tool,
                        "args": c.args,
                        "session_id": turn.session_id,
                        "status": "pending_confirmation",
                    },
                )


def _draft_out(conn: duckdb.DuckDBPyConnection, entry: dict[str, Any]) -> DraftOut:
    did, tool = entry["id"], entry["tool"]
    kind, label = DRAFT_TOOLS[tool]
    status = str(entry.get("status", "pending_confirmation"))
    payload: dict[str, Any] = {}
    if kind == "decision":
        row = fetch_one(
            conn,
            "SELECT id, target_id, action, state, rationale, scenario_id, created_by, created_at"
            " FROM decisions WHERE id = ?",
            [did],
        )
        if row is not None:
            status = str(row["state"])
            payload = {
                "campaign_id": row["target_id"],
                "action": json.loads(row["action"]) if row["action"] else None,
                "scenario_id": row["scenario_id"],
                "rationale": row["rationale"],
                "created_by": row["created_by"],
            }
    elif kind == "parameter":
        a = entry.get("args", {})
        payload = {
            "key": a.get("key"),
            "value": a.get("value"),
            "scope": a.get("scope"),
            "scope_id": a.get("scope_id") or "*",
            "version": _param_version(did),
        }
    else:
        d = draft_tools.DRAFTS.get(did)
        if d is not None:
            payload = d["payload"]
            status = d["status"] if status == "pending_confirmation" else status
    return DraftOut(
        id=did,
        kind=kind,
        label=label,
        tool=tool,
        status=status,
        session_id=entry.get("session_id"),
        payload=payload,
        saved_decision_id=entry.get("saved_decision_id"),
        confirm_path=f"/api/v1/ai/drafts/{did}/confirm",
        reject_path=f"/api/v1/ai/drafts/{did}/reject",
    )


def _param_version(source_id: str) -> int | None:
    m = re.search(r"-v(\d+)$", source_id)
    return int(m.group(1)) if m else None


def _session_drafts(conn: duckdb.DuckDBPyConnection, session_id: str) -> list[DraftOut]:
    with _LOCK:
        entries = [dict(e) for e in _DRAFTS.values() if e["session_id"] == session_id]
    return [_draft_out(conn, e) for e in entries]


def to_chat_turn(
    conn: duckdb.DuckDBPyConnection,
    turn: TurnResult,
    settings: Settings,
    investigation: dict[str, Any] | None = None,
) -> ChatTurn:
    _register_drafts(turn)
    g = turn.grounding
    return ChatTurn(
        session_id=turn.session_id,
        turn=turn.turn,
        flow=turn.flow,
        outcome=turn.outcome,
        text=turn.text,
        sources=[_source(c) for c in turn.citations],
        grounding=(
            GroundingOut(
                ok=g.ok,
                checked=g.checked,
                passed=g.passed,
                badge=g.badge,
                rewrite_count=g.rewrite_count,
                violations=g.violations,
                numbers=g.numbers,
            )
            if g is not None
            else None
        ),
        tool_calls=turn.tool_calls,
        drafts=_session_drafts(conn, turn.session_id),
        policy_violations=turn.policy_violations,
        untrusted_flags=turn.untrusted_flags,
        replayed=turn.replayed,
        replay_match=turn.replay_match,
        label=turn.label,
        model=turn.model,
        usage=turn.usage,
        ai_mode=settings.ai_mode if settings.ai_enabled else "off",
        investigation=investigation,
    )


def _make(settings: Settings, factory: ClientFactory, *, optional: bool) -> LlmClient | None:
    try:
        return factory(settings)
    except AiUnavailable:
        if optional:
            return None
        raise


def run_chat(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    user: User,
    body: ChatRequest,
    factory: ClientFactory,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    on_session: Callable[[str], None] | None = None,
) -> ChatTurn:
    """One turn (shared by JSON and SSE). Raises ``AiUnavailable`` when no client can be
    built, except for ``investigate`` (checklist + automatic summary, AI-07 floor)."""
    from app.ai.flows.investigate import ChecklistSummaryClient

    _check_session(body.session_id, user)
    is_inv = body.flow == "investigate"
    client = _make(settings, factory, optional=is_inv)
    agent = Agent(
        client or ChecklistSummaryClient(),
        conn,
        settings=settings,
        flow=body.flow,
        user_id=user.id,
        role=user.role,
        session_id=body.session_id,
        allow_drafts=body.allow_drafts,
        use_cache=settings.ai_demo_cache and settings.ai_mode == "live",
        context={"screen_context": _context_text(body.context)},
        model=None if client is not None else ChecklistSummaryClient.name,
        on_event=on_event,
    )
    if on_session is not None:
        on_session(agent.session.session_id)
    if is_inv:
        try:
            turn, report = investigate(
                agent,
                body.message,
                product_id=body.context.product_id,
                sku=body.context.sku,
                alert_id=body.context.alert_id,
                fallback_summary=True,
            )
        except ValueError as e:  # product not found → ask back, no LLM call
            raise ValidationFailed(str(e)) from e
        if client is None:
            turn.label = AUTO_SUMMARY_LABEL
        return to_chat_turn(conn, turn, settings, json.loads(report.model_dump_json()))
    return to_chat_turn(conn, agent.run_turn(body.message), settings)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _stream(
    db: Database, settings: Settings, user: User, body: ChatRequest, factory: ClientFactory
) -> Iterator[str]:
    q: queue.Queue[tuple[str, Any] | None] = queue.Queue()

    def work() -> None:
        try:
            with db.reader() as cur:
                res = run_chat(
                    cur,
                    settings,
                    user,
                    body,
                    factory,
                    on_event=lambda k, d: q.put((k, d)),
                    on_session=lambda sid: q.put(("session", {"session_id": sid})),
                )
            q.put(("answer", json.loads(res.model_dump_json(exclude={"usage"}))))
            q.put(("done", res.usage))
        except AppError as e:
            q.put(("error", {"code": e.code, "message": e.message, "details": e.details}))
        except Exception as e:  # pragma: no cover - surfaced as an SSE error event
            log.exception("ai_stream_failed")
            q.put(("error", {"code": "INTERNAL", "message": type(e).__name__}))
        finally:
            q.put(None)

    threading.Thread(target=work, name="ai-chat-sse", daemon=True).start()
    while True:
        item = q.get()
        if item is None:
            return
        yield _sse(*item)


# ---------------------------------------------------------------------- endpoints
@router.post(
    "/chat",
    response_model=Envelope[ChatTurn],
    summary="Hỏi trợ lý AI (JSON, hoặc SSE khi Accept: text/event-stream)",
)
def chat(
    body: ChatRequest,
    request: Request,
    conn: Conn,
    user: Operator,
    stream: Annotated[bool, Query(description="true → text/event-stream")] = False,
) -> Any:
    settings = _settings(request)
    factory = client_factory(request)
    if stream or "text/event-stream" in request.headers.get("accept", ""):
        _check_session(body.session_id, user)
        if body.flow != "investigate":
            _make(settings, factory, optional=False)  # fail fast with a JSON 503
        return StreamingResponse(
            _stream(request.app.state.db, settings, user, body, factory),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    res = run_chat(conn, settings, user, body, factory)
    if res.outcome == "offline":  # 08 §1: OpenAI down and no replay recording → 503
        raise AiUnavailable(res.text, session_id=res.session_id, ai_mode=res.ai_mode)
    return Envelope[ChatTurn](
        data=res,
        meta=Meta(
            data_as_of=data_as_of(conn),
            computed_at=utcnow(),
            compute_state="fresh",
            confidence="scenario"
            if any(t.tool == "run_scenario" and t.ok for t in res.tool_calls)
            else None,
            sources=_meta_sources(res.sources),
        ),
    )


@router.post(
    "/investigate",
    response_model=Envelope[ChatTurn],
    summary="Điều tra 'vì sao bán chậm' (checklist 6 bước + tóm tắt)",
)
def ai_investigate(
    body: InvestigateRequest, request: Request, conn: Conn, user: Operator
) -> Envelope[ChatTurn]:
    if body.sku is None and body.product_id is None and not body.question:
        raise ValidationFailed("Cần sku, product_id hoặc câu hỏi.")
    q = body.question or f"Vì sao {body.sku or body.product_id} bán chậm?"
    req = ChatRequest(
        session_id=body.session_id,
        flow="investigate",
        message=q,
        context=ChatContext(sku=body.sku, product_id=body.product_id, alert_id=body.alert_id),
    )
    res = run_chat(conn, _settings(request), user, req, client_factory(request))
    return Envelope[ChatTurn](
        data=res,
        meta=Meta(
            data_as_of=data_as_of(conn),
            computed_at=utcnow(),
            compute_state="fresh",
            sources=_meta_sources(res.sources),
        ),
    )


def investigate_alert(
    conn: duckdb.DuckDBPyConnection, request: Request, user: User, alert_id: str
) -> Envelope[ChatTurn]:
    """``POST /alerts/{id}/investigate`` (07 §4.3): checklist on the alert's SKU / product
    (``group_key`` = product id), summary by the LLM or from the checklist, result stored
    on ``alerts.investigation``. Synchronous (floor; 08 says 202 + SSE)."""
    from app.governance.alerts import get_alert

    a = get_alert(conn, alert_id)
    sku = a.target_id if a.target_kind == "sku" else None
    product = None if sku else a.group_key
    if sku is None and product is None:
        raise ValidationFailed(
            "Cảnh báo không gắn với SKU hay sản phẩm nên chưa điều tra được.", alert_id=alert_id
        )
    req = ChatRequest(
        flow="investigate",
        message=f"Vì sao {sku or product} bán chậm?",
        context=ChatContext(sku=sku, product_id=product, alert_id=alert_id),
    )
    res = run_chat(conn, _settings(request), user, req, client_factory(request))
    return Envelope[ChatTurn](
        data=res,
        meta=Meta(
            data_as_of=data_as_of(conn),
            computed_at=utcnow(),
            compute_state="fresh",
            sources=_meta_sources(res.sources),
        ),
    )


@router.get("/sessions/{session_id}", response_model=Envelope[SessionOut], summary="Phiên trợ lý")
def get_ai_session(
    session_id: str, request: Request, conn: Conn, user: Viewer
) -> Envelope[SessionOut]:
    settings = _settings(request)
    row = fetch_one(conn, "SELECT * FROM ai_sessions WHERE id = ?", [session_id])
    st = get_session(session_id)
    if row is None and st is None:
        raise NotFound("Không có phiên trợ lý này.", session_id=session_id)
    owner = (st.user_id if st else None) or (row or {}).get("user_id")
    if user.role == "operator" and owner not in (None, user.id):
        raise Forbidden("Phiên trợ lý này thuộc người dùng khác.", session_id=session_id)
    turns: list[SessionTurn] = []
    if st is not None:
        for e in list(st.log):
            res: TurnResult = e["result"]
            turns.append(
                SessionTurn(
                    turn=e["turn"],
                    at=e["at"],
                    question=e["question"],
                    answer=to_chat_turn(conn, res, settings),
                )
            )
    calls = fetch_all(
        conn,
        "SELECT id, turn, tool, args, result_summary, result_source_id, ok, error, duration_ms,"
        ' "at" FROM ai_tool_calls WHERE session_id = ? ORDER BY "at", id',
        [session_id],
    )
    r = row or {}
    return Envelope[SessionOut](
        data=SessionOut(
            session_id=session_id,
            user_id=owner,
            role=r.get("role") or (st.role if st else None),
            flow=r.get("flow") or (st.flow if st else None),
            started_at=r.get("started_at") or (st.started_at if st else None),
            model=r.get("model"),
            input_tokens=int(r.get("input_tokens") or 0),
            output_tokens=int(r.get("output_tokens") or 0),
            grounding_ok=r.get("grounding_ok"),
            outcome=r.get("outcome"),
            turns=turns,
            tool_calls=[
                SessionToolCall(
                    **{
                        **c,
                        "args": _json(c["args"]),
                        "result_summary": _json(c["result_summary"]),
                    }
                )
                for c in calls
            ],
            drafts=_session_drafts(conn, session_id),
            in_memory=st is not None,
        ),
        meta=Meta(computed_at=utcnow()),
    )


def _json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


@router.get("/drafts", response_model=Envelope[list[DraftOut]], summary="Bản nháp AI chờ xác nhận")
def list_drafts(
    conn: Conn,
    user: Operator,
    session_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query(description="vd pending_confirmation, draft")] = None,
) -> Envelope[list[DraftOut]]:
    with _LOCK:
        entries = [
            dict(e) for e in _DRAFTS.values() if session_id is None or e["session_id"] == session_id
        ]
    out = []
    for e in entries:
        st = get_session(e["session_id"])
        if st is not None and st.user_id != user.id and user.role != "lead":
            continue
        d = _draft_out(conn, e)
        if status is None or d.status == status:
            out.append(d)
    return Envelope[list[DraftOut]](data=out, meta=Meta(computed_at=utcnow()))


def _entry(draft_id: str, user: User) -> dict[str, Any]:
    with _LOCK:
        e = _DRAFTS.get(draft_id)
        e = dict(e) if e else None
    if e is None:
        raise NotFound("Không có bản nháp AI này (hoặc đã mất khi khởi động lại).", id=draft_id)
    st = get_session(e["session_id"])
    if st is not None and st.user_id != user.id:
        raise Forbidden("Chỉ người tạo bản nháp mới xác nhận hoặc từ chối được.", id=draft_id)
    return e


def _actor(request: Request, user: User) -> Actor:
    return Actor(
        id=user.id, role=user.role, via="ui", request_id=request.headers.get("X-Request-ID")
    )


def _decision_event(
    conn: duckdb.DuckDBPyConnection,
    request: Request,
    user: User,
    draft_id: str,
    event: Literal["submit", "cancel"],
) -> None:
    """Decision drafts go through the governance state machine: ``submit`` (draft →
    pending_approval; not the creator → 403, blocking issue → 422, stale inputs → 409
    ``STALE_INPUT``) or ``cancel``."""
    (gov.submit if event == "submit" else gov.cancel)(conn, draft_id, _actor(request, user))


def _set_status(draft_id: str, status: str) -> None:
    with _LOCK:
        if draft_id in _DRAFTS:
            _DRAFTS[draft_id]["status"] = status
    d = draft_tools.DRAFTS.get(draft_id)
    if d is not None:
        d["status"] = status


@router.post(
    "/drafts/{draft_id}/confirm",
    response_model=Envelope[DraftOut],
    summary="Người xác nhận bản nháp AI",
)
def confirm_draft(
    draft_id: str,
    request: Request,
    conn: Conn,
    user: Operator,
    body: ConfirmBody | None = None,
) -> Envelope[DraftOut]:
    """Decision draft → ``pending_approval`` (the lead still approves, 10 §3); parameter
    suggestion → ``ai_suggested_confirmed`` (``editable_by`` checked); mapping / SKU match /
    handoff → saved as a ``decisions`` row ``kind='handoff_note'`` (body ``note`` = the text
    the person edited; default = the draft rendered as text); mapping / SKU match →
    ``confirmed`` (payload returned for the ingest screen)."""
    e = _entry(draft_id, user)
    kind = DRAFT_TOOLS[e["tool"]][0]
    if e.get("status") in ("confirmed", "rejected"):
        raise StateConflict(f"Bản nháp đã {e['status']}.", id=draft_id)
    if kind == "decision":
        _decision_event(conn, request, user, draft_id, "submit")
    elif kind == "parameter":
        a = e["args"]
        key, version = a.get("key"), _param_version(draft_id)
        if not can_edit(str(key), user.role):
            raise Forbidden(f"Vai trò '{user.role}' không được xác nhận tham số '{key}'.")
        if version is None:
            raise StateConflict("Bản nháp tham số thiếu phiên bản.", id=draft_id)
        p = confirm_suggestion(
            conn,
            str(key),
            scope=str(a.get("scope") or "shop"),
            scope_id=str(a.get("scope_id") or "*"),
            version=version,
            reason=f"Xác nhận gợi ý AI bởi {user.id}",
        )
        write_audit(
            conn,
            _actor(request, user),
            "parameter.confirm",
            "parameter",
            str(key),
            after=p.model_dump(mode="json"),
            shop_id=_settings(request).shop_id,
        )
    elif kind == "handoff":
        saved = draft_tools.save_handoff(
            conn,
            _actor(request, user),
            draft_id,
            note=body.note if body else None,
            shop_id=_settings(request).shop_id,
        )
        e = {**e, "saved_decision_id": saved.id}
        with _LOCK:
            if draft_id in _DRAFTS:
                _DRAFTS[draft_id]["saved_decision_id"] = saved.id
    else:
        write_audit(
            conn,
            _actor(request, user),
            "ai_draft.confirm",
            kind,
            draft_id,
            shop_id=_settings(request).shop_id,
        )
    _set_status(draft_id, "confirmed")
    return Envelope[DraftOut](
        data=_draft_out(conn, {**e, "status": "confirmed"}), meta=Meta(computed_at=utcnow())
    )


@router.post(
    "/drafts/{draft_id}/reject",
    response_model=Envelope[DraftOut],
    summary="Người từ chối bản nháp AI",
)
def reject_draft(
    draft_id: str,
    request: Request,
    conn: Conn,
    user: Operator,
    body: DraftDecisionBody | None = None,
) -> Envelope[DraftOut]:
    """Decision draft → ``cancelled``; the others → ``rejected`` (nothing takes effect)."""
    e = _entry(draft_id, user)
    kind = DRAFT_TOOLS[e["tool"]][0]
    if e.get("status") in ("confirmed", "rejected"):
        raise StateConflict(f"Bản nháp đã {e['status']}.", id=draft_id)
    if kind == "decision":
        _decision_event(conn, request, user, draft_id, "cancel")
    write_audit(
        conn,
        _actor(request, user),
        "ai_draft.reject",
        kind,
        draft_id,
        after={"reason": body.reason if body else None},
        shop_id=_settings(request).shop_id,
    )
    _set_status(draft_id, "rejected")
    return Envelope[DraftOut](
        data=_draft_out(conn, {**e, "status": "rejected"}), meta=Meta(computed_at=utcnow())
    )


@router.get("/status", response_model=Envelope[AiStatus], summary="Trạng thái lớp AI")
def ai_status(request: Request, _user: Viewer) -> Envelope[AiStatus]:
    from app.ai.client import RECORDINGS_DIR, model_for

    s = _settings(request)
    rec = {
        f: len(list((RECORDINGS_DIR / f).glob("*.json")))
        for f in FLOWS
        if (RECORDINGS_DIR / f).is_dir()
    }
    msg = None
    if not s.ai_enabled:
        msg = "AI đang tắt (AI_ENABLED=false)."
    elif s.ai_mode != "replay" and not s.openai_api_key:
        msg = "Chưa có OPENAI_API_KEY: đặt trong backend/.env, hoặc dùng AI_MODE=replay."
    models: dict[str, str | None] = {}
    for task in ("reasoning", "fast"):
        try:
            models[task] = model_for(task, s)
        except AiUnavailable:
            models[task] = None
    return Envelope[AiStatus](
        data=AiStatus(
            enabled=s.ai_enabled,
            mode=s.ai_mode,
            available=s.ai_available,
            has_key=bool(s.openai_api_key),
            models=models,
            recordings=rec,
            message=msg,
        ),
        meta=Meta(computed_at=utcnow()),
    )
