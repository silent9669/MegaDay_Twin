"""AI-01: ``OpenAIClient`` request building / response parsing with a stubbed SDK call
(never the network), model defaults, replay chip remapping."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.ai.client import (
    DEFAULT_MODELS,
    LlmRequest,
    OpenAIClient,
    is_reasoning_model,
    model_for,
    remap_sources,
    user_item,
)
from app.core.config import Settings
from app.core.errors import AiUnavailable


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, app_env="test", **kw)  # type: ignore[call-arg]


def test_model_defaults_and_env_override() -> None:
    s = _settings()
    assert model_for("reasoning", s) == DEFAULT_MODELS["reasoning"]
    assert model_for("fast", s) == DEFAULT_MODELS["fast"]
    s2 = _settings(openai_model_reasoning="my-reasoner", openai_model_fast="my-fast")
    assert model_for("reasoning", s2) == "my-reasoner" and model_for("fast", s2) == "my-fast"
    with pytest.raises(AiUnavailable):
        model_for("embed", s)
    assert is_reasoning_model("gpt-5-mini") and is_reasoning_model("o4-mini")
    assert not is_reasoning_model("gpt-4.1") and not is_reasoning_model("gpt-5-chat-latest")


def test_no_key_message() -> None:
    with pytest.raises(AiUnavailable) as e:
        OpenAIClient(_settings(openai_api_key=None))
    assert "OPENAI_API_KEY" in e.value.message and "AI_MODE=replay" in e.value.message


class _Item(SimpleNamespace):
    def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _resp(*items: _Item) -> SimpleNamespace:
    return SimpleNamespace(
        output=list(items),
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        model="gpt-5-2026",
    )


def test_request_kwargs_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAIClient(_settings(openai_api_key="test-not-a-real-key"))
    seen: list[dict[str, Any]] = []

    def create(**kw: Any) -> SimpleNamespace:
        seen.append(kw)
        return _resp(
            _Item(type="reasoning", id="rs_1", summary=[], encrypted_content="xx"),
            _Item(
                type="function_call",
                id="fc_1",
                call_id="call_1",
                name="get_sku_snapshot",
                arguments='{"sku": "NOICHIEN2L"}',
                status="completed",
            ),
        )

    monkeypatch.setattr(client._client.responses, "create", create)
    req = LlmRequest(
        model="gpt-5",
        instructions="sys",
        input=[user_item("Gọi mình số 0901234567 hoặc a.b@shop.vn nhé, nồi chiên còn bao nhiêu?")],
        tools=[{"type": "function", "name": "get_sku_snapshot"}],
        temperature=0.2,
        task="reasoning",
    )
    out = client.create(req)
    kw = seen[0]
    assert kw["store"] is False and "temperature" not in kw
    assert kw["reasoning"] == {"effort": "low"} and kw["include"] == ["reasoning.encrypted_content"]
    sent = kw["input"][0]["content"]
    assert "0901234567" not in sent and "a.b@shop.vn" not in sent  # no PII leaves the machine
    assert req.input[0]["content"].startswith("Gọi mình số 0901234567")  # caller's copy intact
    assert out.tool_calls[0].name == "get_sku_snapshot" and out.text is None
    assert [i["type"] for i in out.output_items or []] == ["reasoning", "function_call"]
    assert out.usage.input_tokens == 100 and out.model == "gpt-5-2026"


def test_text_answer_and_temperature_for_non_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAIClient(_settings(openai_api_key="test-not-a-real-key"))
    seen: list[dict[str, Any]] = []

    def create(**kw: Any) -> SimpleNamespace:
        seen.append(kw)
        return _resp(_Item(type="message", content=[{"type": "output_text", "text": "Còn 41 sp."}]))

    monkeypatch.setattr(client._client.responses, "create", create)
    out = client.create(
        LlmRequest(model="gpt-4.1-mini", instructions="s", input=[user_item("x")], task="fast")
    )
    assert seen[0]["temperature"] == 0.2 and "reasoning" not in seen[0]
    assert out.text == "Còn 41 sp." and out.output_items is None


def test_remap_sources() -> None:
    old = [["compare_actions", "scn-A"], ["run_scenario", "scn-B"]]
    new = [["compare_actions", "scn-X"], ["run_scenario", "scn-Y"]]
    text = "Đề xuất [[src:scn-A]]; giả định [[src:scn-B]] và [[src:snap-1]]."
    assert remap_sources(text, old, new) == (
        "Đề xuất [[src:scn-X]]; giả định [[src:scn-Y]] và [[src:snap-1]]."
    )
