"""Python twin of ``mock-data/replay.js`` (one step): write the ``live_stream/``
folder as it looks after simulated hour ``hour`` of 11.11, without Node and without
touching ``mock-data/``. Used by the live-delta tests and ``eval_data`` timings.

Reads ``mock-data/out/<size>/live_11_11/_replay.json`` (input data of the replay,
not the answer key). Same rules as ``replay.js``: orders and stock up to ``hour``,
ads up to ``hour - adsLagHours``; each file is rewritten in full (BOM + header +
rows) and ``trang_thai.json`` records the marks.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from eval import mock_dir


@cache
def _replay(size: str) -> dict[str, Any]:
    path = mock_dir(size) / "live_11_11" / "_replay.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="")
    tmp.replace(path)


def write_live_stream(size: str, out_dir: Path, hour: int) -> dict[str, str]:
    """Write the state after hour ``hour`` (0–23) into ``out_dir``; returns the
    ``trang_thai.json`` payload."""
    replay = _replay(size)
    out_dir.mkdir(parents=True, exist_ok=True)
    ads_until = hour - int(replay["adsLagHours"])
    for key in ("orders", "ads", "inventory"):
        max_hour = ads_until if key == "ads" else hour
        lines = [line for h, line in replay["rows"][key] if h <= max_hour]
        body = "".join(f"{line}\n" for line in lines)
        _write_atomic(out_dir / replay["files"][key], "﻿" + replay["headers"][key] + "\n" + body)
    status = {
        "ngay": replay["ngay"],
        "don_hang_va_ton_den_het": f"{hour:02d}:59",
        "ads_den_het": f"{ads_until:02d}:59" if ads_until >= 0 else "chưa có",
        "cap_nhat_luc": datetime.now(UTC).isoformat(),
    }
    _write_atomic(out_dir / "trang_thai.json", json.dumps(status, ensure_ascii=False, indent=2))
    return status
