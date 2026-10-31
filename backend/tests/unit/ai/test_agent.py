"""AI-01 / AI-10 · agent loop with FakeClient: tool calls, grounding rewrites, give-up,
budget, roles, ai_sessions / ai_tool_calls, record / replay, answer cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from app.ai.agent import Agent
from app.ai.cache import ANSWER_CACHE
from app.ai.client import (
    FakeClient,
    LlmRequest,
    LlmResponse,
    RecordReplayClient,
    make_client,
    tool_outputs,
)
from app.core.config import Settings

STUB: dict[str, Any] = {
    "get_sku_snapshot": {
        "sku": "NOICHIEN2L",
        "stock_available": 41,
        "price_effective": 531000,
        "incoming": [{"qty": 400, "planned_date": "2026-11-12"}],
        "_sources": [{"id": "snap-NOICHIEN2L-1459", "kind": "snapshot", "label": "Tồn"}],
    }
}


def _agent(conn: duckdb.DuckDBPyConnection, settings: Settings, client: Any, **kw: Any) -> Agent:
    return Agent(client, conn, settings=settings, stub_results=STUB, **kw)


def test_tool_call_then_grounded_answer(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    def answer(req: LlmRequest) -> LlmResponse:
        snap = tool_outputs(req)["get_sku_snapshot"]
        return FakeClient.say(
            f"Nồi chiên còn {snap['stock_available']} sp [[src:{snap['source']['id']}]],"
            f" hàng về 400 sp ngày 12/11."
        )

    fc = FakeClient([FakeClient.call("get_sku_snapshot", {"sku": "NOICHIEN2L"}), answer])
    turn = _agent(mem_conn, ai_settings, fc).run_turn("Nồi chiên còn bao nhiêu?")
    assert turn.outcome == "answered" and turn.grounding_ok
    assert turn.grounding is not None and turn.grounding.badge == "Đã kiểm số: 3/3"
    assert turn.tools_called() == ["get_sku_snapshot"]
    assert {c.source_id for c in turn.citations} == {"snap-NOICHIEN2L-1459"}
    # the system prompt is the Vietnamese base + flow part, tools are strict functions
    req = fc.requests[0]
    assert (
        "Mọi con số bạn nói ra" in req.instructions
        and "<du_lieu_khong_tin_cay>" in req.instructions
    )
    assert all(t["strict"] for t in req.tools)
    s = mem_conn.execute(
        "SELECT flow, outcome, grounding_ok, rewrite_count, input_tokens > 0 FROM ai_sessions"
    ).fetchall()
    assert s == [("chat", "answered", True, 0, True)]
    assert mem_conn.execute("SELECT tool, ok FROM ai_tool_calls").fetchall() == [
        ("get_sku_snapshot", True)
    ]


def test_invented_number_is_rewritten(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    fc = FakeClient(
        [
            FakeClient.call("get_sku_snapshot", {"sku": "NOICHIEN2L"}),
            FakeClient.say("Còn 41 sp, tháng sau lời khoảng 50 triệu."),
            FakeClient.say("Còn 41 sp. Mình không có số đáng tin cho lợi nhuận tháng sau."),
        ]
    )
    turn = _agent(mem_conn, ai_settings, fc).run_turn("Ước chừng tháng sau lời bao nhiêu?")
    assert turn.outcome == "answered" and turn.grounding_ok and turn.rewrite_count == 1
    assert "50 triệu" not in turn.text
    rewrite = fc.requests[2].input[-1]
    assert rewrite["role"] == "developer" and "50 triệu" in rewrite["content"]


def test_gives_up_after_two_rewrites(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    bad = FakeClient.say("Lời 50 triệu.")
    fc = FakeClient([FakeClient.call("get_sku_snapshot", {"sku": "NOICHIEN2L"}), bad, bad, bad])
    turn = _agent(mem_conn, ai_settings, fc).run_turn("Lời bao nhiêu?")
    assert turn.outcome == "gave_up" and not turn.grounding_ok and turn.rewrite_count == 2
    assert "50 triệu" not in turn.text and "get_sku_snapshot" in turn.text
    assert mem_conn.execute("SELECT grounding_ok, outcome FROM ai_sessions").fetchone() == (
        False,
        "gave_up",
    )


def test_tool_outside_flow_refused(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    fc = FakeClient([FakeClient.call("draft_shift_handoff", {}), FakeClient.say("Xong.")])
    turn = _agent(mem_conn, ai_settings, fc, flow="investigate").run_turn("Bàn giao ca")
    assert turn.tool_calls[0].error == "unknown_tool"


def test_viewer_and_budget_refused(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    turn = _agent(mem_conn, ai_settings, FakeClient(), role="viewer").run_turn("Chào")
    assert turn.outcome == "refused"
    small = ai_settings.model_copy(update={"ai_session_token_budget": 10})
    fc = FakeClient([FakeClient.say("Một câu trả lời khá dài " * 20)])
    turn = _agent(mem_conn, small, fc).run_turn("Chào " * 50)
    assert turn.outcome == "refused" and "hạn mức" in turn.text


def test_offline_when_script_ends(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings
) -> None:
    turn = _agent(mem_conn, ai_settings, FakeClient()).run_turn("Chào")
    assert turn.outcome == "offline" and "ngoại tuyến" in turn.text


def test_record_then_replay(
    mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings, tmp_path: Path
) -> None:
    script = [
        FakeClient.call("get_sku_snapshot", {"sku": "NOICHIEN2L"}),
        FakeClient.say("Còn 41 sp [[src:snap-NOICHIEN2L-1459]]."),
    ]
    rec = RecordReplayClient("record", FakeClient(script), root=tmp_path)
    t1 = _agent(mem_conn, ai_settings, rec, flow="chat", model="fake").run_turn(
        "Nồi chiên còn bao nhiêu?"
    )
    assert t1.outcome == "answered" and not t1.replayed
    files = list((tmp_path / "chat").glob("*.json"))
    assert len(files) == 1 and "Khách Test" not in files[0].read_text(encoding="utf-8")
    rep = RecordReplayClient("replay", None, root=tmp_path)
    t2 = _agent(mem_conn, ai_settings, rep).run_turn("nồi chiên  còn bao nhiêu")
    assert t2.outcome == "answered" and t2.replayed and t2.grounding_ok
    assert t2.label is not None and "đã lưu" in t2.label
    assert t2.tools_called() == ["get_sku_snapshot"]  # tools re-run on current data
    miss = RecordReplayClient("replay", None, root=tmp_path)
    t3 = _agent(mem_conn, ai_settings, miss).run_turn("Câu chưa ghi bao giờ")
    assert t3.outcome == "offline"


def test_make_client_modes(ai_settings: Settings) -> None:
    import pytest

    from app.core.errors import AiUnavailable

    off = ai_settings.model_copy(update={"ai_enabled": False})
    with pytest.raises(AiUnavailable):
        make_client(off)
    replay = ai_settings.model_copy(update={"ai_mode": "replay"})
    assert isinstance(make_client(replay), RecordReplayClient)
    with pytest.raises(AiUnavailable):  # live without a key
        make_client(ai_settings.model_copy(update={"openai_api_key": None}))
    rec = make_client(ai_settings.model_copy(update={"ai_mode": "record"}), live=FakeClient())
    assert isinstance(rec, RecordReplayClient) and rec.mode == "record"


def test_answer_cache(mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings) -> None:
    ANSWER_CACHE.clear()
    fc = FakeClient([FakeClient.say("Chào bạn, mình giúp gì được?")])
    a1 = _agent(mem_conn, ai_settings, fc, use_cache=True).run_turn("Xin chào")
    a2 = _agent(mem_conn, ai_settings, FakeClient(), use_cache=True).run_turn("xin chào")
    assert a1.outcome == "asked_back" and a2.cached and a2.text == a1.text
    ANSWER_CACHE.clear()


def test_session_history_kept(mem_conn: duckdb.DuckDBPyConnection, ai_settings: Settings) -> None:
    fc = FakeClient([FakeClient.say("Chào."), FakeClient.say("Vẫn là mình.")])
    a = _agent(mem_conn, ai_settings, fc)
    a.run_turn("Chào")
    t = Agent(fc, mem_conn, settings=ai_settings, session_id=a.session.session_id).run_turn("Ai?")
    assert t.turn == 2
    assert [i.get("content") for i in fc.requests[1].input] == ["Chào", "Chào.", "Ai?"]
