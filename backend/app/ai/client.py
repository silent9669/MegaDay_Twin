"""LLM clients behind one small interface (AI-01, 07 §2, §8, §9).

* :class:`LlmClient` — ``create(LlmRequest) -> LlmResponse``. Items in ``LlmRequest.input``
  use the Responses API shapes (``{"role": "user", "content": …}``,
  ``{"type": "function_call", …}``, ``{"type": "function_call_output", …}``) so the OpenAI
  client passes them through unchanged.
* :class:`FakeClient` — scripted steps for tests / offline eval (no network).
* :class:`OpenAIClient` — ``openai`` SDK, Responses API + strict function calling; model
  names come from ``OPENAI_MODEL_REASONING / FAST / EMBED``, else :data:`DEFAULT_MODELS`.
  Request building is unit-tested with a stubbed SDK; no real call has been made yet (no key).
* :class:`RecordReplayClient` — ``AI_MODE=record|replay`` (07 §9): record writes every LLM
  step of a turn to ``backend/eval/ai_recordings/<flow>/<key>.json``; replay serves them
  back by key (question + data fingerprint + prompt version) and marks them ``replayed``.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.core.config import BACKEND_DIR, Settings
from app.core.errors import AiUnavailable
from app.core.timeutil import utcnow

ModelTask = Literal["reasoning", "fast", "embed"]
RECORDINGS_DIR = BACKEND_DIR / "eval" / "ai_recordings"
_CALL_SEQ = itertools.count(1)


# Defaults when ``OPENAI_MODEL_*`` is empty (chosen 2026-10-30, see M4-ai.md "AI ra API"):
# GPT-5 family on the Responses API; reasoning effort low keeps a demo turn < ~15 s.
DEFAULT_MODELS: dict[str, str] = {"reasoning": "gpt-5", "fast": "gpt-5-mini"}
REASONING_EFFORT: dict[str, str] = {"reasoning": "low", "fast": "minimal"}


def model_for(task: ModelTask, settings: Settings) -> str:
    """07 §8: model per task from env (``OPENAI_MODEL_REASONING / FAST / EMBED``), else the
    documented default of :data:`DEFAULT_MODELS`. No embed default → ``AiUnavailable``."""
    name = {
        "reasoning": settings.openai_model_reasoning,
        "fast": settings.openai_model_fast or settings.openai_model_reasoning,
        "embed": settings.openai_model_embed,
    }[task] or DEFAULT_MODELS.get(task)
    if not name:
        raise AiUnavailable("Chưa cấu hình OPENAI_MODEL_EMBED.", task=task)
    return name


def is_reasoning_model(model: str) -> bool:
    """GPT-5 / o-series: no ``temperature``, take ``reasoning.effort``."""
    m = model.lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4")) and "chat" not in m


# ---------------------------------------------------------------------- data shapes
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class LlmRequest:
    model: str
    instructions: str
    input: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    temperature: float | None = 0.2
    text_format: dict[str, Any] | None = None  # structured output json_schema
    max_output_tokens: int | None = 1500
    flow: str = "chat"
    task: ModelTask = "reasoning"


@dataclass
class LlmResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    # items to append to ``input`` for the next step (reasoning + function_call items for
    # the Responses API; ``None`` → the agent builds them from ``tool_calls``/``text``)
    output_items: list[dict[str, Any]] | None = None
    replayed: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": [c.__dict__ for c in self.tool_calls],
            "usage": self.usage.__dict__,
            "model": self.model,
            "output_items": self.output_items,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> LlmResponse:
        return cls(
            text=d.get("text"),
            tool_calls=[ToolCall(**c) for c in d.get("tool_calls", [])],
            usage=Usage(**d.get("usage", {})),
            model=d.get("model", ""),
            output_items=d.get("output_items"),
        )


def user_item(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def assistant_item(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def developer_item(text: str) -> dict[str, Any]:
    return {"role": "developer", "content": text}


def call_item(call: ToolCall) -> dict[str, Any]:
    return {
        "type": "function_call",
        "call_id": call.id,
        "name": call.name,
        "arguments": call.arguments,
    }


def output_item(call_id: str, output: str) -> dict[str, Any]:
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def json_schema_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """``text.format`` for strict structured outputs (Responses API)."""
    return {"type": "json_schema", "name": name, "schema": schema, "strict": True}


# ---------------------------------------------------------------------- clients
class LlmClient(ABC):
    name = "abstract"

    @abstractmethod
    def create(self, req: LlmRequest) -> LlmResponse: ...

    def start_turn(self, key: str, meta: dict[str, Any]) -> None:  # noqa: B027
        """Called by the agent before the first step of a turn (record/replay key)."""

    def finish_turn(self, ok: bool) -> None:  # noqa: B027
        """Called after the last step of a turn."""


Step = LlmResponse | Callable[[LlmRequest], LlmResponse]


class FakeClient(LlmClient):
    """Scripted client. Each step is an ``LlmResponse`` or a function of the request (so a
    test can answer with numbers read from the real tool outputs)."""

    name = "fake"

    def __init__(self, steps: Sequence[Step] = (), *, default_text: str | None = None) -> None:
        self.steps: list[Step] = list(steps)
        self.requests: list[LlmRequest] = []
        self.default_text = default_text

    def create(self, req: LlmRequest) -> LlmResponse:
        self.requests.append(req)
        if not self.steps:
            if self.default_text is None:
                raise AiUnavailable("FakeClient hết kịch bản.")
            return LlmResponse(text=self.default_text, model="fake")
        step = self.steps.pop(0)
        resp = step(req) if callable(step) else step
        if not resp.model:
            resp.model = "fake"
        if resp.usage.total == 0:
            size = len(json.dumps(req.input, ensure_ascii=False, default=str)) + len(
                req.instructions
            )
            resp.usage = Usage(size // 4, len(resp.text or "") // 4 + 20 * len(resp.tool_calls))
        return resp

    # helpers -----------------------------------------------------------
    @staticmethod
    def call(
        name: str, args: dict[str, Any] | None = None, call_id: str | None = None
    ) -> LlmResponse:
        cid = call_id or f"call_{name}_{next(_CALL_SEQ)}"
        return LlmResponse(tool_calls=[ToolCall(cid, name, json.dumps(args or {}))])

    @staticmethod
    def calls(*items: tuple[str, dict[str, Any]]) -> LlmResponse:
        return LlmResponse(
            tool_calls=[
                ToolCall(f"call_{i}_{n}", n, json.dumps(a, ensure_ascii=False))
                for i, (n, a) in enumerate(items)
            ]
        )

    @staticmethod
    def say(text: str) -> LlmResponse:
        return LlmResponse(text=text)


def tool_outputs(req: LlmRequest) -> dict[str, dict[str, Any]]:
    """``{tool name: parsed output}`` of the function calls in a request (last wins); for
    FakeClient steps that write answers from the real tool results."""
    names = {i["call_id"]: i["name"] for i in req.input if i.get("type") == "function_call"}
    out: dict[str, dict[str, Any]] = {}
    for i in req.input:
        if i.get("type") == "function_call_output":
            try:
                out[names.get(i["call_id"], i["call_id"])] = json.loads(i["output"])
            except (ValueError, KeyError):
                continue
    return out


def all_tool_outputs(req: LlmRequest) -> list[tuple[str, dict[str, Any]]]:
    names = {i["call_id"]: i["name"] for i in req.input if i.get("type") == "function_call"}
    out = []
    for i in req.input:
        if i.get("type") == "function_call_output":
            try:
                out.append((names.get(i["call_id"], ""), json.loads(i["output"])))
            except (ValueError, KeyError):
                continue
    return out


def scrub_user_text(text: str) -> str:
    """Mask phone numbers / e-mails a user may paste into a question (10 §9): nothing
    personal leaves the machine. Tool outputs are already PII-free (ingest drops the buyer
    columns, ``guard`` masks contacts in notes)."""
    from app.ingest import adapters as _adapters  # noqa: F401  (import cycle in ingest)
    from app.ingest.pii import mask_contacts

    return mask_contacts(text)[0]


def _scrub_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for it in items:
        if it.get("role") in ("user", "developer") and isinstance(it.get("content"), str):
            it = {**it, "content": scrub_user_text(it["content"])}
        out.append(it)
    return out


class OpenAIClient(LlmClient):
    """Responses API + strict function calling (``openai`` ≥ 1.40, tested with 3.x).

    * ``store=False`` (no server-side retention); reasoning items come back with
      ``reasoning.encrypted_content`` so they can be passed to the next step statelessly.
    * GPT-5 / o-series: no ``temperature``; ``reasoning.effort`` from the task
      (:data:`REASONING_EFFORT`). Others: ``temperature`` as asked.
    * User / developer text is scrubbed of contacts before it is sent.
    * The key is read from ``Settings`` (env / ``backend/.env``) and never logged.
    """

    name = "openai"

    def __init__(self, settings: Settings, *, timeout: float = 90.0) -> None:
        if not settings.openai_api_key:
            raise AiUnavailable(
                "Chưa có OPENAI_API_KEY (đặt trong biến môi trường hoặc backend/.env),"
                " hoặc chạy AI_MODE=replay để dùng bản ghi demo."
            )
        from openai import OpenAI

        self._client = OpenAI(api_key=settings.openai_api_key, timeout=timeout, max_retries=2)

    def build_kwargs(self, req: LlmRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": req.model,
            "instructions": req.instructions,
            "input": _scrub_items(req.input),
            "store": False,
        }
        if req.tools:
            kwargs["tools"] = req.tools
            kwargs["parallel_tool_calls"] = True
        if is_reasoning_model(req.model):
            kwargs["reasoning"] = {"effort": REASONING_EFFORT.get(req.task, "low")}
            kwargs["include"] = ["reasoning.encrypted_content"]
        elif req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if req.text_format is not None:
            kwargs["text"] = {"format": req.text_format}
        if req.max_output_tokens:
            # reasoning tokens count against the limit: leave room for them
            extra = 4000 if is_reasoning_model(req.model) else 0
            kwargs["max_output_tokens"] = req.max_output_tokens + extra
        return kwargs

    def create(self, req: LlmRequest) -> LlmResponse:
        import openai

        kwargs = self.build_kwargs(req)
        try:
            resp = self._client.responses.create(**kwargs)
        except openai.BadRequestError as e:
            msg = str(e)
            retry = False
            for k in ("temperature", "reasoning", "include"):
                if k in msg and k in kwargs:
                    kwargs.pop(k)
                    retry = True
            if not retry:
                raise AiUnavailable(f"OpenAI từ chối yêu cầu: {_short(msg)}") from e
            try:
                resp = self._client.responses.create(**kwargs)
            except openai.OpenAIError as e2:
                raise AiUnavailable(f"OpenAI từ chối yêu cầu: {_short(str(e2))}") from e2
        except openai.AuthenticationError as e:
            raise AiUnavailable("OPENAI_API_KEY không hợp lệ.") from e
        except openai.OpenAIError as e:
            raise AiUnavailable(f"Không gọi được OpenAI: {type(e).__name__}") from e
        return parse_response(resp)


def _short(msg: str, n: int = 300) -> str:
    return msg if len(msg) <= n else msg[:n] + "…"


def parse_response(resp: Any) -> LlmResponse:
    """``openai`` Responses object → :class:`LlmResponse` (reasoning + function_call items
    are kept in ``output_items`` to feed the next step)."""
    calls: list[ToolCall] = []
    texts: list[str] = []
    items: list[dict[str, Any]] = []
    for item in resp.output:
        d = item.model_dump(exclude_none=True)
        if item.type == "function_call":
            calls.append(ToolCall(d["call_id"], d["name"], d.get("arguments") or "{}"))
            items.append(d)
        elif item.type == "reasoning":
            items.append(d)
        elif item.type == "message":
            for c in d.get("content", []):
                if c.get("type") == "output_text":
                    texts.append(c.get("text", ""))
    u = resp.usage
    usage = Usage(int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0))
    return LlmResponse(
        text="".join(texts) if texts else None,
        tool_calls=calls,
        usage=usage,
        model=str(resp.model),
        output_items=items if calls else None,
    )


# ---------------------------------------------------------------------- record / replay
class ReplayMiss(AiUnavailable):
    default_message = "Trợ lý đang ngoại tuyến (chưa có bản ghi cho câu hỏi này)."


def recording_key(flow: str, question: str, data_fp: str, prompt_version: str) -> str:
    raw = json.dumps([flow, normalize_question(question), data_fp, prompt_version])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def normalize_question(q: str) -> str:
    return " ".join(q.lower().split()).rstrip(" ?.!")


class RecordReplayClient(LlmClient):
    """``record``: wraps a live client and saves each turn; ``replay``: serves saved turns.

    Replay looks up the exact key first (flow + question + data fingerprint + prompt
    version + history). On a miss it falls back to the newest recording of the **same flow
    and normalised question** (the demo data clock moves, so the fingerprint rarely matches
    exactly): tools still run on the current data and grounding re-checks every number, so
    a stale recording ends as ``gave_up``, never as a wrong number. ``last_match`` says
    which one was used (``exact | question | None``)."""

    def __init__(
        self,
        mode: Literal["record", "replay"],
        inner: LlmClient | None,
        root: Path = RECORDINGS_DIR,
        *,
        question_fallback: bool = True,
    ) -> None:
        if mode == "record" and inner is None:
            raise ValueError("record mode needs a live client")
        self.mode = mode
        self.inner = inner
        self.root = root
        self.question_fallback = question_fallback
        self.name = f"{mode}:{inner.name if inner else 'none'}"
        self._key: str | None = None
        self._meta: dict[str, Any] = {}
        self._steps: list[dict[str, Any]] = []
        self._cursor = 0
        self.last_match: str | None = None
        self.last_file: str | None = None

    def _path(self, key: str, flow: str) -> Path:
        return self.root / flow / f"{key}.json"

    def _by_question(self, flow: str, question: str) -> Path | None:
        d = self.root / flow
        if not d.is_dir():
            return None
        want = normalize_question(question)
        best: tuple[str, Path] | None = None
        for f in d.glob("*.json"):
            try:
                body = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                not body.get("ok", True)
                or normalize_question(str(body.get("question", ""))) != want
            ):
                continue
            at = str(body.get("recorded_at", ""))
            if best is None or at > best[0]:
                best = (at, f)
        return best[1] if best else None

    def start_turn(self, key: str, meta: dict[str, Any]) -> None:
        self._key, self._meta, self._cursor = key, dict(meta), 0
        self._steps = []
        self.last_match = self.last_file = None
        if self.mode == "replay":
            flow = str(meta.get("flow", "chat"))
            p: Path | None = self._path(key, flow)
            match = "exact"
            if p is None or not p.exists():
                p = (
                    self._by_question(flow, str(meta.get("question", "")))
                    if (self.question_fallback)
                    else None
                )
                match = "question"
            if p is not None:
                self._steps = json.loads(p.read_text(encoding="utf-8")).get("steps", [])
                self.last_match, self.last_file = match, p.name

    def create(self, req: LlmRequest) -> LlmResponse:
        if self.mode == "replay":
            if self._cursor >= len(self._steps):
                raise ReplayMiss()
            step = self._steps[self._cursor]
            self._cursor += 1
            resp = LlmResponse.from_json(step["response"])
            resp.replayed = True
            old = step.get("request", {}).get("sources")
            if resp.text and old:  # ids of runs not persisted (scenarios) change per run
                resp.text = remap_sources(resp.text, old, source_trail(req))
            return resp
        assert self.inner is not None
        resp = self.inner.create(req)
        body = resp.to_json()
        if body.get("output_items"):  # drop opaque reasoning blobs (not needed to replay)
            body["output_items"] = [
                {k: v for k, v in it.items() if k != "encrypted_content"}
                for it in body["output_items"]
            ]
        self._steps.append(
            {
                "request": {
                    "model": req.model,
                    "n_items": len(req.input),
                    "tools": [t.get("name") for t in req.tools],
                    "last_item": _item_kind(req.input[-1]) if req.input else None,
                    "sources": source_trail(req),
                },
                "response": body,
            }
        )
        return resp

    def finish_turn(self, ok: bool) -> None:
        if self.mode != "record" or self._key is None or not self._steps:
            return
        flow = str(self._meta.get("flow", "chat"))
        p = self._path(self._key, flow)
        p.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "key": self._key,
            **self._meta,
            "client": self.inner.name if self.inner else None,
            "ok": ok,
            "recorded_at": utcnow().isoformat(),
            "steps": self._steps,
        }
        p.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        self.last_file = p.name


def source_trail(req: LlmRequest) -> list[list[str]]:
    """``[[tool, source id], …]`` of the tool outputs in a request, in call order."""
    trail: list[list[str]] = []
    for name, out in all_tool_outputs(req):
        src = out.get("source") if isinstance(out, dict) else None
        if isinstance(src, dict) and src.get("id"):
            trail.append([name, str(src["id"])])
    return trail


def remap_sources(text: str, old: list[list[str]], new: list[list[str]]) -> str:
    """Rewrite recorded chip ids to the ids of the same tool call (same tool, same rank)
    in the replayed turn; grounding then re-checks every number against the new data."""

    def ranked(trail: list[list[str]]) -> dict[tuple[str, int], str]:
        seen: dict[str, int] = {}
        out: dict[tuple[str, int], str] = {}
        for tool_name, sid in trail:
            k = seen.get(tool_name, 0)
            seen[tool_name] = k + 1
            out[(tool_name, k)] = sid
        return out

    now = ranked(new)
    mapping = {sid: now[key] for key, sid in ranked(old).items() if key in now}
    for a, b in mapping.items():
        if a != b:
            text = text.replace(f"[[src:{a}]]", f"[[src:{b}]]")
    return text


def _item_kind(item: dict[str, Any]) -> str:
    return str(item.get("type") or item.get("role") or "?")


def make_client(settings: Settings, live: LlmClient | None = None) -> LlmClient:
    """Client for ``AI_MODE`` (07 §9). ``live`` overrides the OpenAI client (tests).

    * ``AI_ENABLED=false`` → ``AiUnavailable``.
    * ``replay`` → recordings only (no key, no network).
    * ``live`` / ``record`` without ``OPENAI_API_KEY`` → ``AiUnavailable`` with the fix.
    """
    if not settings.ai_enabled:
        raise AiUnavailable("AI đang tắt (AI_ENABLED=false).")
    if settings.ai_mode == "replay":
        return RecordReplayClient("replay", None)
    inner = live or OpenAIClient(settings)
    if settings.ai_mode == "record":
        return RecordReplayClient("record", inner)
    return inner
