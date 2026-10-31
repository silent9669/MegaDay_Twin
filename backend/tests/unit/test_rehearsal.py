"""eval.rehearsal: bước kiểm và báo lỗi, chạy trên client giả (không dựng app)."""

from __future__ import annotations

import json
from typing import Any

from eval import rehearsal as rh


class _Resp:
    def __init__(self, status: int, body: Any) -> None:
        self.status_code = status
        self._body = body
        self.text = json.dumps(body, ensure_ascii=False)
        self.content = self.text.encode()

    def json(self) -> Any:
        return self._body


class _Fake:
    """Routes ``(method, path)`` to canned answers; records the calls."""

    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, str]] = []

    def request(self, method: str, url: str, headers: dict[str, str], **_: Any) -> _Resp:
        path = url.removeprefix("/api/v1")
        self.calls.append((method, path, headers["X-User"]))
        out = self.routes.get((method, path))
        if callable(out):
            out = out(headers)
        if out is None:
            return _Resp(404, {"error": {"message": "không có"}})
        status, body = out if isinstance(out, tuple) else (200, out)
        return _Resp(status, body)


def test_freshness_off_clock_is_reported_not_raised() -> None:
    fake = _Fake({("GET", "/freshness"): {"data": {"data_as_of": "2026-11-11T14:00:00+07:00"}}})
    res = rh.rehearse(rh.Api(fake), runs=1)
    a = res[0]
    assert a.step == "a" and not a.ok and "01:00" in a.detail
    assert len(res) == len(rh.STEPS)  # later steps still run and report


def test_decision_step_needs_403_on_self_approval() -> None:
    def approve(headers: dict[str, str]) -> Any:
        if headers["X-User"] == rh.LEAD2[0]:
            return 403, {"error": {"code": "FORBIDDEN", "details": {"reason": "self_approval"}}}
        return {"data": {"id": "dec-1", "state": "approved"}}

    routes: dict[tuple[str, str], Any] = {
        ("POST", "/decisions"): (201, {"data": {"id": "dec-1", "state": "draft"}}),
        ("POST", "/decisions/dec-1/submit"): {"data": {"id": "dec-1", "state": "pending_approval"}},
        ("POST", "/decisions/dec-1/approve"): approve,
        ("POST", "/decisions/dec-1/cancel"): {"data": {"id": "dec-1", "state": "cancelled"}},
    }
    fake = _Fake(routes)
    st = rh.State(recommendation_id="rec-1")
    detail = rh.step_d(rh.Api(fake), st)
    assert "403 (self_approval)" in detail
    approvers = [u for m, p, u in fake.calls if p.endswith("/approve")]
    assert approvers == [rh.LEAD[0], rh.LEAD2[0]]


def test_ai_turn_must_be_replayed_grounded_and_sourced() -> None:
    turn = {
        "outcome": "answered",
        "replayed": True,
        "replay_match": "question",
        "grounding": {"ok": True, "passed": 8, "checked": 8},
        "sources": [{"id": "scn-1"}],
        "text": "nên giảm ngân sách 30%: xác suất bán đủ 81%",
    }
    assert "8/8" in rh._ai_ok(turn, must=["giảm ngân sách 30%"])
    bad = {**turn, "grounding": {"ok": False, "violations": ["81%"]}}
    try:
        rh._ai_ok(bad, must=[])
    except rh.StepFailed as e:
        assert "grounding" in str(e)
    else:  # pragma: no cover
        raise AssertionError("grounding lỗi phải làm bước LỖI")


def test_demo_recordings_say_what_the_rehearsal_checks() -> None:
    """One recording per demo flow, and its final answer has the phrases step c / f check
    (the whatif answer of the full pitch DB, M5 "Phương án A")."""
    from app.ai.client import RECORDINGS_DIR

    must = {
        "whatif": rh.WHATIF_MUST,
        "explain": ["khung giờ"],
        "investigate": ["AOGIUNHIET-M", "hết hàng"],
    }
    for flow, phrases in must.items():
        files = sorted((RECORDINGS_DIR / flow).glob("*.json"))
        assert len(files) == 1, (flow, files)
        rec = json.loads(files[0].read_text(encoding="utf-8"))
        text = rec["steps"][-1]["response"]["text"]
        for m in phrases:
            assert m in text, (flow, m)
