"""Diễn tập demo tự động (M5): chạy đúng thứ tự các cảnh pitch qua API và kiểm từng bước.

Dùng (trong ``backend/``)::

    # server đang chạy (vd bật bằng node scripts/demo.mjs --api-port 8100 ...)
    uv run python -m eval.rehearsal --base-url http://localhost:8100

    # tự dựng app trong process (TestClient) trên bản sao của một DB demo 01:00
    uv run python -m eval.rehearsal --db data/pitch-0100/demo.duckdb

    # không có DB: tự dựng như demo.mjs (app.demo_import --replay-hour 0 --models) vào thư mục tạm
    uv run python -m eval.rehearsal

Mỗi lượt (mặc định 2 lượt, lượt sau chạy tiếp sau bước g "về đầu"):

a. ``/freshness`` + Tổng quan ca ở 01:00 ngày 11.11;
b. CD002: khuyến nghị ``schedule_hours:0-2,12-14``, hơn ``continue`` ~3,97 tr, tạm dừng bị cấm,
   có ZoneBar (cũng làm nóng màn So sánh);
c. AI replay ``explain`` + ``whatif`` (gõ nguyên văn), grounding đạt, có nguồn; ``whatif`` "chỉ
   tiêu 97" trên DB đầy đủ: không phương án nào đạt chắc 80%, cao nhất là chạy tiếp 70%;
d. quyết định từ khuyến nghị CD002: operator tạo + gửi, lead khác duyệt, lead tự duyệt → 403;
e. ``jump`` 10 (dữ liệu tới 10:59) → cảnh báo bình nước bán vượt dự báo;
f. ``jump`` 13 (tới 13:59) → cảnh báo áo size M hết hàng khi CD005 vẫn chạy; AI replay
   ``investigate`` nhắc size M hết hàng;
g. ``reset`` + ``jump`` 0 → về lại 01:00.

Kết quả: bảng ĐẠT/LỖI + thời gian mỗi bước, ghi ``eval/reports/rehearsal.json``. Thoát mã 1 nếu
có bước LỖI. Không đọc ``ground_truth``; mọi con số so với giá trị đã ghi trong runbook.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
SHOP = (BACKEND / ".." / "mock-data" / "out" / "demo").resolve()
REPORT = BACKEND / "eval" / "reports" / "rehearsal.json"

CLOCK_0100 = "2026-11-11T01:00:00+07:00"
Q_EXPLAIN = "Vì sao nồi chiên chỉ chạy khung giờ?"
Q_WHATIF = "Nếu sếp giao chỉ tiêu 97 cái hôm nay thì sao?"
Q_INVESTIGATE = "Vì sao áo giữ nhiệt bán chậm?"
CD002_BEST = "schedule_hours:0-2,12-14"
GAIN_VS_CONTINUE = 3.97e6  # runbook K4, ± 0,1 tr (cùng dung sai eval.pitch_numbers)
# whatif "chỉ tiêu 97" trên DB đầy đủ (M5 "Phương án A"): không phương án nào đạt, cái giá thật
WHATIF_MUST = ["không phương án nào đạt chắc 80%", "chạy tiếp: xác suất bán đủ 70%", "3,97 triệu"]
GAIN_TOL = 0.1e6

OPERATOR = ("op-demo", "operator")
LEAD = ("lead-1", "lead")
LEAD2 = ("lead-2", "lead")


class StepFailed(Exception):
    pass


def check(cond: object, msg: str) -> None:
    if not cond:
        raise StepFailed(msg)


# ---------------------------------------------------------------------- HTTP
class Api:
    """Tiny wrapper over an httpx-like client (``httpx.Client`` or ``TestClient``)."""

    def __init__(self, client: Any) -> None:
        self.c = client

    def call(
        self,
        method: str,
        path: str,
        who: tuple[str, str] = OPERATOR,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        expect: int | tuple[int, ...] = (200, 201),
    ) -> Any:
        r = self.c.request(
            method,
            "/api/v1" + path,
            headers={"X-User": who[0], "X-Role": who[1]},
            json=json_body,
            params=params,
        )
        ok = (r.status_code in expect) if isinstance(expect, tuple) else r.status_code == expect
        if not ok:
            raise StepFailed(f"{method} {path} → HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    def get(self, path: str, who: tuple[str, str] = OPERATOR, **params: Any) -> Any:
        return self.call("GET", path, who, params=params or None)

    def post(
        self,
        path: str,
        body: Any = None,
        who: tuple[str, str] = OPERATOR,
        expect: int | tuple[int, ...] = (200, 201),
    ) -> Any:
        return self.call(
            "POST", path, who, json_body=body if body is not None else {}, expect=expect
        )


# ---------------------------------------------------------------------- steps
@dataclass
class StepResult:
    run: int
    step: str
    name: str
    ok: bool
    seconds: float
    detail: str = ""


@dataclass
class State:
    scenario_id: str | None = None
    recommendation_id: str | None = None
    notes: dict[str, Any] = field(default_factory=dict)


def _same_instant(a: str | None, b: str) -> bool:
    if not a:
        return False
    return datetime.fromisoformat(a) == datetime.fromisoformat(b)


def _hhmm(iso: str | None) -> str | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso).strftime("%H:%M")


def ensure_goal(api: Api) -> str:
    """Lead's profit goal for CD002 (runbook 1.5); created once."""
    goals = api.get("/goals", LEAD, scope_id="CD002")["data"]
    if any(g.get("kind") == "profit" for g in goals):
        return "đã có mục tiêu profit CD002"
    api.post(
        "/goals",
        {
            "scope": "campaign",
            "scope_id": "CD002",
            "kind": "profit",
            "deadline": "2026-11-12T00:00:00+07:00",
            "reason": "Lãi trong ngày 11.11",
        },
        LEAD,
    )
    return "đã đặt mục tiêu profit CD002"


def step_a(api: Api, st: State) -> str:
    fr = api.get("/freshness")["data"]
    check(
        _same_instant(fr["data_as_of"], CLOCK_0100), f"data_as_of = {fr['data_as_of']}, cần 01:00"
    )
    srcs = {s["source"]: s for s in fr["sources"]} if fr.get("sources") else {}
    skus = api.get("/skus", limit=500)
    check(len(skus["data"]) > 0, "Tổng quan ca không có SKU")
    noi = [
        s for s in skus["data"] if s.get("sku") == "NOICHIEN2L" or s.get("sku_id") == "NOICHIEN2L"
    ]
    ads = srcs.get("ads", {})
    return (
        f"data_as_of 01:00; {len(skus['data'])} SKU; ads {ads.get('status', '?')}"
        f" (trễ {ads.get('lag_minutes', '?')} phút)" + ("; có NOICHIEN2L" if noi else "")
    )


def step_b(api: Api, st: State) -> str:
    note = ensure_goal(api)
    run = api.post("/scenarios/run", {"campaign_id": "CD002"})["data"]
    rec = run["recommendation"]
    check(rec is not None, "không có khuyến nghị")
    by = {r["key"]: r for r in run["results"]}
    best = rec["best"]
    best_key = next((k for k, r in by.items() if r["action"] == best), None)
    check(best_key == CD002_BEST, f"khuyến nghị {best_key}, cần {CD002_BEST}")
    check(rec["status"] == "recommend", f"trạng thái {rec['status']}")
    gain = by[CD002_BEST]["objective"]["mean"] - by["continue"]["objective"]["mean"]
    check(
        abs(gain - GAIN_VS_CONTINUE) <= GAIN_TOL,
        f"hơn continue {gain / 1e6:.2f} tr, cần {GAIN_VS_CONTINUE / 1e6:.2f} ± 0,1",
    )
    pauses = [r for r in run["results"] if r["action"]["kind"] in ("pause", "pause_until_restock")]
    check(pauses and all(not r["feasible"] for r in pauses), "tạm dừng không bị cấm")
    check(rec.get("zone_bar"), "không có ZoneBar")
    st.scenario_id, st.recommendation_id = run["scenario_id"], rec["recommendation_id"]
    # what the Compare screen shows: the campaign's active recommendation
    active = api.get("/recommendations", campaign_id="CD002", active="true")["data"]
    check(active, "không có đề xuất hiện hành CD002")
    a_best = active[0]["best"]
    check(
        a_best and a_best["kind"] == "schedule_hours",
        f"đề xuất hiện hành CD002 = {a_best}",
    )
    p = rec.get("p_best_beats_runner_up")
    timing = run.get("timings_ms", {}).get("api_total")
    return (
        f"{note}; {CD002_BEST}, hơn continue {gain / 1e6:.2f} tr, p={p:.2f}, pause cấm,"
        f" ZoneBar {rec['zone_bar'].get('verdict')}; cache_hit={run.get('cache_hit')}"
        + (f", {timing / 1000:.1f} s" if timing else "")
    )


def _ai_ok(turn: dict[str, Any], *, must: list[str]) -> str:
    g = turn.get("grounding") or {}
    check(turn.get("outcome") == "answered", f"outcome {turn.get('outcome')}: {turn.get('text')}")
    check(turn.get("replayed"), "không phải câu trả lời ghi sẵn (replayed=false)")
    check(g.get("ok"), f"grounding lỗi: {g.get('violations')}")
    check(turn.get("sources"), "không có chip nguồn")
    text = turn.get("text") or ""
    for m in must:
        check(m in text, f"câu trả lời thiếu '{m}'")
    return (
        f"{turn.get('replay_match')}, kiểm số {g.get('passed')}/{g.get('checked')},"
        f" {len(turn['sources'])} nguồn"
    )


def step_c(api: Api, st: State) -> str:
    ctx = {"campaign_id": "CD002", "sku": "NOICHIEN2L", "screen": "compare"}
    ex = api.post("/ai/chat", {"flow": "explain", "message": Q_EXPLAIN, "context": ctx})["data"]
    r1 = _ai_ok(ex, must=["khung giờ"])
    wi = api.post("/ai/chat", {"flow": "whatif", "message": Q_WHATIF, "context": ctx})["data"]
    r2 = _ai_ok(wi, must=WHATIF_MUST)
    return f"explain: {r1}; whatif: {r2}"


def step_d(api: Api, st: State) -> str:
    check(st.recommendation_id, "chưa có khuyến nghị từ bước b")
    d = api.post("/decisions", {"recommendation_id": st.recommendation_id}, OPERATOR)["data"]
    check(d["state"] == "draft", f"trạng thái sau tạo {d['state']}")
    d = api.post(f"/decisions/{d['id']}/submit", {}, OPERATOR)["data"]
    check(d["state"] == "pending_approval", f"sau gửi {d['state']}")
    d = api.post(f"/decisions/{d['id']}/approve", {"note": "Diễn tập"}, LEAD)["data"]
    check(d["state"] == "approved", f"sau duyệt {d['state']}")
    # a lead approving their own decision is refused (10 §1)
    own = api.post("/decisions", {"recommendation_id": st.recommendation_id}, LEAD2)["data"]
    api.post(f"/decisions/{own['id']}/submit", {}, LEAD2)
    r = api.c.request(
        "POST",
        f"/api/v1/decisions/{own['id']}/approve",
        headers={"X-User": LEAD2[0], "X-Role": LEAD2[1]},
        json={},
    )
    check(r.status_code == 403, f"lead tự duyệt → HTTP {r.status_code}, cần 403")
    reason = (r.json().get("error", {}).get("details") or {}).get("reason")
    api.post(f"/decisions/{own['id']}/cancel", {}, LEAD2)
    return f"{d['id']} approved bởi {LEAD[0]}; tự duyệt → 403 ({reason})"


def _jump(api: Api, hour: int) -> dict[str, Any]:
    out: dict[str, Any] = api.post(
        "/live/control", {"command": "jump", "hour": hour, "wait": True}, LEAD
    )["data"]
    return out


def _alerts(api: Api) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = api.get("/alerts", status="open,acknowledged,snoozed", limit=200)[
        "data"
    ]
    return data


def step_e(api: Api, st: State) -> str:
    s = _jump(api, 10)
    check(s["orders_until"] == "10:59", f"dữ liệu tới {s['orders_until']}, cần 10:59")
    bottle = [
        a
        for a in _alerts(api)
        if a["kind"] == "sales_above_forecast"
        and (a.get("group_key") == "BINHNUOC500" or "BINHNUOC500" in json.dumps(a.get("detail")))
    ]
    check(bottle, "không có cảnh báo bình nước bán vượt dự báo")
    a = bottle[0]
    hour = (a.get("detail") or {}).get("first_data_hour")
    return f"tới 10:59; '{a['title']}' ({a['severity']}, dữ liệu {hour})"


def step_f(api: Api, st: State) -> str:
    s = _jump(api, 13)
    check(s["orders_until"] == "13:59", f"dữ liệu tới {s['orders_until']}, cần 13:59")
    shirt = [
        a
        for a in _alerts(api)
        if a["kind"] == "stockout_risk"
        and a.get("target_id") == "AOGIUNHIET-M"
        and str(a.get("dedupe_key", "")).endswith(":out")
    ]
    check(shirt, "không có cảnh báo AOGIUNHIET-M hết hàng")
    a = shirt[0]
    check("CD005" in json.dumps(a, ensure_ascii=False), "cảnh báo không nhắc CD005 đang chạy")
    hour = (a.get("detail") or {}).get("first_data_hour")
    inv = api.post("/ai/chat", {"flow": "investigate", "message": Q_INVESTIGATE, "context": {}})[
        "data"
    ]
    r = _ai_ok(inv, must=["AOGIUNHIET-M", "hết hàng"])
    return f"'{a['title']}' (dữ liệu {hour}); investigate: {r}"


def step_g(api: Api, st: State) -> str:
    api.post("/live/control", {"command": "reset"}, LEAD)
    s = _jump(api, 0)
    check(s["orders_until"] == "00:59", f"sau reset+jump 0 dữ liệu tới {s['orders_until']}")
    fr = api.get("/freshness")["data"]
    check(_same_instant(fr["data_as_of"], CLOCK_0100), f"data_as_of = {fr['data_as_of']}")
    return "reset + jump 0 → data_as_of 01:00"


STEPS: list[tuple[str, str, Callable[[Api, State], str]]] = [
    ("a", "Độ mới + Tổng quan ca 01:00", step_a),
    ("b", "CD002 khuyến nghị khung giờ (So sánh)", step_b),
    ("c", "AI replay explain + whatif", step_c),
    ("d", "Quyết định: tạo, gửi, duyệt, tự duyệt 403", step_d),
    ("e", "Nhảy 10:59 → cảnh báo bình nước", step_e),
    ("f", "Nhảy 13:59 → áo size M hết hàng + AI investigate", step_f),
    ("g", "Reset về 01:00", step_g),
]


def rehearse(api: Api, runs: int) -> list[StepResult]:
    out: list[StepResult] = []
    for run in range(1, runs + 1):
        st = State()
        for sid, name, fn in STEPS:
            t0 = time.perf_counter()
            try:
                detail, ok = fn(api, st), True
            except StepFailed as e:
                detail, ok = str(e), False
            except Exception as e:  # report and go on with the next step
                detail, ok = f"{type(e).__name__}: {e}", False
            res = StepResult(run, sid, name, ok, round(time.perf_counter() - t0, 2), detail)
            out.append(res)
            _print_row(res)
    return out


# ---------------------------------------------------------------------- output
def _say(text: str) -> None:
    try:
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.flush()
    except (AttributeError, ValueError):
        print(text.encode("ascii", "backslashreplace").decode())


def _print_row(r: StepResult) -> None:
    _say(
        f"  lượt {r.run} [{r.step}] {r.name:48s} {'ĐẠT' if r.ok else 'LỖI'}"
        f" {r.seconds:7.1f} s  {r.detail}"
    )


def _summary(results: list[StepResult]) -> None:
    _say("\n| Lượt | Bước | Cảnh | Kết quả | Thời gian |")
    _say("|---|---|---|---|---|")
    for r in results:
        _say(f"| {r.run} | {r.step} | {r.name} | {'ĐẠT' if r.ok else 'LỖI'} | {r.seconds:.1f} s |")


# ---------------------------------------------------------------------- app sources
@contextmanager
def http_client(base_url: str) -> Iterator[Any]:
    import httpx

    with httpx.Client(base_url=base_url.rstrip("/"), timeout=900) as c:
        c.get("/api/v1/health").raise_for_status()
        yield c


def build_demo_db(out: Path) -> Path:
    """Same DB as ``scripts/demo.mjs``: ``app.demo_import --replay-hour 0 --models``
    (01:00 through the replay folder; backtest + fit + iROAS) into ``out`` (new directory)."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "app.demo_import",
            "--shop",
            str(SHOP),
            "--output",
            str(out),
            "--replay-hour",
            "0",
            "--models",
        ],
        cwd=BACKEND,
        check=True,
    )
    return out / "demo.duckdb"


@contextmanager
def inprocess_client(db: Path | None, keep: bool) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app
    from app.warehouse.db import Database

    tmp = Path(tempfile.mkdtemp(prefix="rehearsal_"))
    try:
        if db is None:
            path = build_demo_db(tmp / "pitch")
        else:
            path = tmp / "demo.duckdb"
            shutil.copy(db, path)
            wal = Path(str(db) + ".wal")
            if wal.exists():
                shutil.copy(wal, Path(str(path) + ".wal"))
            live = db.parent / "live_stream"  # the replay folder that goes with this DB
            if live.is_dir():
                shutil.copytree(live, tmp / "live_stream")
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            app_env="demo",
            duckdb_path=path,
            data_dir=SHOP,
            live_dir=tmp / "live_stream",
            live_enabled=True,
            ai_enabled=True,
            ai_mode="replay",
            demo_mode=True,
            log_json=True,
            log_level="WARNING",
        )
        database = Database(path)
        with TestClient(create_app(settings, db=database)) as c:
            yield c
    finally:
        if keep:
            _say(f"Giữ thư mục tạm: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Diễn tập demo MegaDay Twin qua API.")
    ap.add_argument("--base-url", default=None, help="server đang chạy, vd http://localhost:8100")
    ap.add_argument("--db", type=Path, default=None, help="DB demo 01:00 (được chép sang tạm)")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--report", type=Path, default=REPORT)
    ap.add_argument("--keep", action="store_true", help="giữ thư mục tạm (chế độ in-process)")
    args = ap.parse_args(argv)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    t0 = time.perf_counter()
    mode = "http" if args.base_url else "inprocess"
    src = http_client(args.base_url) if args.base_url else inprocess_client(args.db, args.keep)
    _say(f"Diễn tập demo ({mode}), {args.runs} lượt")
    with src as client:
        results = rehearse(Api(client), args.runs)
    _summary(results)
    ok = all(r.ok for r in results)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "base_url": args.base_url,
        "db": str(args.db) if args.db else None,
        "runs": args.runs,
        "ok": ok,
        "total_seconds": round(time.perf_counter() - t0, 1),
        "steps": [asdict(r) for r in results],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _say(f"\n{'ĐẠT' if ok else 'LỖI'} · {report['total_seconds']} s · {args.report}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
