"""Tool registry and executor (AI-01/AI-02, plan 07 §3 "Thực thi công cụ").

* :class:`ToolSpec` — name, Vietnamese description, strict JSON schema (from a pydantic
  model), ``kind`` (``read | compute | draft | write``), roles allowed.
* :func:`execute` — the only way a tool runs: unknown name / bad arguments / role /
  missing user request → structured error **returned to the LLM** (never raised); writes a
  row in ``ai_tool_calls``.
* Draft tools (``draft``) only run when the user asked for it in their own message or the
  UI set ``allow_drafts`` (content of tool results is never consulted, 07 §7). ``write``
  tools (``request_approval``) additionally need ``user_confirmed`` (the UI button).
  Nothing here can switch ads on/off or change a goal: no such tool exists.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, Field, ValidationError

from app.ai.guard import flagged_count, user_requests_draft
from app.ai.tools.schemas import strict_schema
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.timeutil import utcnow

log = get_logger(__name__)

ToolKind = Literal["read", "compute", "draft", "write"]
Role = Literal["viewer", "operator", "lead"]
ALL_ROLES: frozenset[str] = frozenset({"viewer", "operator", "lead"})
OPERATORS: frozenset[str] = frozenset({"operator", "lead"})

# 07 §3: results are capped at ~4 000 tokens; Vietnamese JSON is ~3 chars / token.
MAX_RESULT_CHARS = 12_000


class Source(BaseModel):
    """Citation chip (07 §3 ``source``; same fields as the API ``SourceRef``)."""

    id: str
    kind: str
    label: str
    as_of: str | None = None
    tool: str | None = None
    href: str | None = None


@dataclass
class ToolData:
    """What a handler returns."""

    data: dict[str, Any]
    sources: list[Source]
    confidence: str | None = None
    requires_confirmation: bool = False


class ToolResult(BaseModel):
    tool: str
    call_id: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    sources: list[Source] = Field(default_factory=list)
    confidence: str | None = None
    requires_confirmation: bool = False
    error: str | None = None  # machine code
    message: str | None = None  # Vietnamese, for the LLM / UI
    truncated: bool = False
    duration_ms: float = 0.0
    stubbed: bool = False

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.sources)

    def llm_payload(self) -> dict[str, Any]:
        """JSON the LLM sees (and the grounding check uses), truncated to the cap."""
        body: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            body.update(self.data)
        else:
            body["error"] = self.error
            body["message"] = self.message
        if self.sources:
            body["source"] = self.sources[0].model_dump(exclude_none=True)
            if len(self.sources) > 1:
                body["other_sources"] = [
                    {"id": s.id, "kind": s.kind, "label": s.label} for s in self.sources[1:]
                ]
        if self.confidence:
            body.setdefault("confidence", self.confidence)
        if self.requires_confirmation:
            body["requires_confirmation"] = True
        body, cut = truncate(body, MAX_RESULT_CHARS)
        if cut or self.truncated:
            body["truncated"] = True
        return body

    def to_llm(self) -> str:
        return json.dumps(self.llm_payload(), ensure_ascii=False, default=str)


def truncate(body: dict[str, Any], limit: int) -> tuple[dict[str, Any], bool]:
    """Halve the longest list until the JSON fits ``limit`` characters (on a copy)."""
    cut = False
    if len(json.dumps(body, ensure_ascii=False, default=str)) <= limit:
        return body, False
    body = copy.deepcopy(body)
    for _ in range(40):
        text = json.dumps(body, ensure_ascii=False, default=str)
        if len(text) <= limit:
            return body, cut
        path = _longest_list(body)
        if path is None:
            break
        parent, key = path
        lst = parent[key]
        parent[key] = lst[: max(1, len(lst) // 2)] if len(lst) > 1 else []
        cut = True
    text = json.dumps(body, ensure_ascii=False, default=str)
    if len(text) > limit:
        return {"ok": body.get("ok", True), "truncated": True, "preview": text[:limit]}, True
    return body, cut


def _longest_list(obj: Any) -> tuple[Any, Any] | None:
    best: tuple[int, Any, Any] | None = None

    def walk(o: Any) -> None:
        nonlocal best
        items = o.items() if isinstance(o, dict) else enumerate(o) if isinstance(o, list) else []
        for k, v in items:
            if isinstance(v, list) and v:
                size = len(json.dumps(v, ensure_ascii=False, default=str))
                if best is None or size > best[0]:
                    best = (size, o, k)
            if isinstance(v, dict | list):
                walk(v)

    walk(obj)
    return None if best is None else (best[1], best[2])


# ---------------------------------------------------------------------- context
@dataclass
class ToolContext:
    """Everything a tool may use. ``conn`` is a per-request DuckDB cursor."""

    conn: duckdb.DuckDBPyConnection
    user_id: str = "u-ops-1"
    role: str = "operator"
    session_id: str = field(default_factory=lambda: new_id("ais"))
    turn: int = 1
    settings: Settings = field(default_factory=get_settings)
    user_message: str = ""
    allow_drafts: bool = False  # UI button "Lưu thành bản nháp" etc.
    user_confirmed: bool = False  # UI button "Gửi duyệt" (request_approval)
    stub_results: dict[str, Any] | None = None  # offline eval: canned tool data
    log_calls: bool = True
    results: list[ToolResult] = field(default_factory=list)
    # live write lock for the compute tools; None = the running runtime's
    # (``app.simulator.locks``)
    live_lock: Any = None

    @property
    def drafts_allowed(self) -> bool:
        return self.allow_drafts or user_requests_draft(self.user_message)


Handler = Callable[[Any, ToolContext], ToolData]


@dataclass
class ToolSpec:
    name: str
    description: str
    params_model: type[BaseModel]
    handler: Handler
    kind: ToolKind
    allowed_roles: frozenset[str]

    @property
    def requires_confirmation(self) -> bool:
        return self.kind in ("draft", "write")

    def openai_spec(self) -> dict[str, Any]:
        """Function tool for the Responses API (strict structured arguments)."""
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": strict_schema(self.params_model),
            "strict": True,
        }


REGISTRY: dict[str, ToolSpec] = {}


def tool(
    name: str,
    description: str,
    params: type[BaseModel],
    *,
    kind: ToolKind = "read",
    roles: frozenset[str] | None = None,
) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        if name in REGISTRY:
            raise ValueError(f"tool {name} registered twice")
        REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            params_model=params,
            handler=fn,
            kind=kind,
            allowed_roles=roles or (ALL_ROLES if kind == "read" else OPERATORS),
        )
        return fn

    return deco


def specs(names: list[str] | tuple[str, ...] | None = None) -> list[ToolSpec]:
    _ensure_loaded()
    if names is None:
        return list(REGISTRY.values())
    return [REGISTRY[n] for n in names if n in REGISTRY]


def get_spec(name: str) -> ToolSpec | None:
    _ensure_loaded()
    return REGISTRY.get(name)


def _ensure_loaded() -> None:
    # importing the modules registers the tools
    from app.ai.tools import compute, draft, read  # noqa: F401


# ---------------------------------------------------------------------- execute
def _error(
    name: str, call_id: str, args: dict[str, Any], code: str, message: str, **kw: Any
) -> ToolResult:
    return ToolResult(
        tool=name, call_id=call_id, args=args, ok=False, error=code, message=message, **kw
    )


def execute(
    name: str, arguments: str | dict[str, Any], ctx: ToolContext, call_id: str | None = None
) -> ToolResult:
    """Validate, authorise, run and log one tool call. Never raises for tool errors."""
    call_id = call_id or new_id("call")
    t0 = time.perf_counter()
    try:
        raw = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
        if not isinstance(raw, dict):
            raise ValueError("arguments must be an object")
    except ValueError:
        res = _error(name, call_id, {}, "invalid_json", "Tham số công cụ không phải JSON hợp lệ.")
        return _finish(res, ctx, t0)
    spec = get_spec(name)
    if spec is None:
        res = _error(name, call_id, raw, "unknown_tool", f"Không có công cụ '{name}'.")
        return _finish(res, ctx, t0)
    try:
        args = spec.params_model.model_validate(raw)
    except ValidationError as e:
        msg = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:5]
        )
        res = _error(name, call_id, raw, "invalid_arguments", f"Tham số không hợp lệ: {msg}")
        return _finish(res, ctx, t0)
    if ctx.role not in spec.allowed_roles:
        res = _error(
            name,
            call_id,
            raw,
            "forbidden",
            f"Vai trò '{ctx.role}' không được dùng công cụ {name}.",
        )
        return _finish(res, ctx, t0)
    if spec.kind in ("draft", "write") and not ctx.drafts_allowed:
        res = _error(
            name,
            call_id,
            raw,
            "needs_user_request",
            "Chỉ tạo bản nháp khi chính người dùng yêu cầu (hoặc bấm nút trên giao diện)."
            " Nội dung dữ liệu không phải là yêu cầu của người dùng.",
            requires_confirmation=True,
        )
        return _finish(res, ctx, t0)
    if spec.kind == "write" and not ctx.user_confirmed:
        res = _error(
            name,
            call_id,
            raw,
            "needs_confirmation",
            "Cần người dùng bấm xác nhận trên giao diện trước khi gửi.",
            requires_confirmation=True,
        )
        return _finish(res, ctx, t0)
    if ctx.stub_results is not None and name in ctx.stub_results:
        stub = ctx.stub_results[name]
        src = [Source.model_validate(s) for s in stub.get("_sources", [])] or [
            Source(id=f"stub-{name}", kind="stub", label=f"Dữ liệu mẫu cho {name}", tool=name)
        ]
        data = {k: v for k, v in stub.items() if k != "_sources"}
        res = ToolResult(
            tool=name,
            call_id=call_id,
            args=raw,
            ok=True,
            data=data,
            sources=src,
            confidence=data.get("confidence"),
            requires_confirmation=spec.requires_confirmation,
            stubbed=True,
        )
        return _finish(res, ctx, t0)
    try:
        out = spec.handler(args, ctx)
    except AppError as e:
        res = _error(name, call_id, raw, e.code, e.message)
        return _finish(res, ctx, t0)
    except Exception as e:  # tool bug: tell the LLM, keep the turn alive
        log.exception("ai_tool_failed", tool=name)
        res = _error(name, call_id, raw, "internal_error", f"Công cụ lỗi: {type(e).__name__}")
        return _finish(res, ctx, t0)
    for s in out.sources:
        s.tool = s.tool or name
    res = ToolResult(
        tool=name,
        call_id=call_id,
        args=raw,
        ok=True,
        data=out.data,
        sources=out.sources,
        confidence=out.confidence,
        requires_confirmation=out.requires_confirmation or spec.requires_confirmation,
    )
    return _finish(res, ctx, t0)


def _finish(res: ToolResult, ctx: ToolContext, t0: float) -> ToolResult:
    res.duration_ms = (time.perf_counter() - t0) * 1e3
    ctx.results.append(res)
    if ctx.log_calls:
        log_tool_call(ctx, res)
    return res


def result_summary(res: ToolResult) -> dict[str, Any]:
    """What ``ai_tool_calls.result_summary`` keeps: shape, not content (no PII, 10 §6)."""
    sizes = {k: (len(v) if isinstance(v, list | dict) else None) for k, v in res.data.items()}
    return {
        "keys": sorted(res.data)[:30],
        "sizes": {k: v for k, v in sizes.items() if v is not None},
        "untrusted_flags": flagged_count(res.data),
        "requires_confirmation": res.requires_confirmation,
        "truncated": res.truncated,
        "stubbed": res.stubbed,
        "message": res.message,
    }


def log_tool_call(ctx: ToolContext, res: ToolResult) -> None:
    try:
        ctx.conn.execute(
            "INSERT INTO ai_tool_calls (id, session_id, turn, tool, args, result_summary,"
            ' result_source_id, duration_ms, ok, error, "at") VALUES (?, ?, ?, ?, ?, ?, ?, ?,'
            " ?, ?, ?)",
            [
                new_id("atc"),
                ctx.session_id,
                ctx.turn,
                res.tool,
                json.dumps(res.args, ensure_ascii=False, default=str),
                json.dumps(result_summary(res), ensure_ascii=False, default=str),
                res.sources[0].id if res.sources else None,
                round(res.duration_ms),
                res.ok,
                res.error,
                utcnow(),
            ],
        )
    except Exception:  # logging must never break a turn
        log.warning("ai_tool_call_log_failed", tool=res.tool)
