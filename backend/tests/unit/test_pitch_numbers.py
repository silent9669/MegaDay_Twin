"""eval.pitch_numbers: bảng so sánh và chế độ bỏ qua (không chạy phép đo nặng)."""

from __future__ import annotations

import json
from pathlib import Path

from eval import pitch_numbers as pn


def test_near_marks_ok_and_off_by_tolerance() -> None:
    b = pn.Board()
    b.near("x", "trong dung sai", 3.99e6, 4.05e6, 0.1e6, pn.tr, "src")
    b.near("x", "ngoài dung sai", 3.99e6, 4.2e6, 0.1e6, pn.tr, "src")
    assert [i.status for i in b.items] == [pn.OK, pn.OFF]
    assert b.items[0].pitch == "3,99 tr" and b.items[0].measured == "4,05 tr"
    assert pn.pct(0.9638) == "96,4%"


def test_run_with_everything_skipped_writes_json(tmp_path: Path) -> None:
    out = tmp_path / "pn.json"
    res = pn.run("demo", reuse_db=None, skip=set(pn.SECTIONS), live_hours=0, out=out)
    data = json.loads(out.read_text(encoding="utf-8"))
    n = len(pn.DATA_ITEMS + pn.FORECAST_ITEMS + pn.ADS_ITEMS + pn.WHATIF_ITEMS + pn.API_ITEMS)
    assert len(data["items"]) == n
    assert {i["status"] for i in data["items"]} == {pn.SKIP}
    assert res["passed"] and "Con số" in res["table"]
