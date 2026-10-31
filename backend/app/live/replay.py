"""Python twin of ``mock-data/replay.js`` (one step), for the demo controls (LIVE-01).

``write_live_stream(source, out_dir, hour)`` writes the ``live_stream/`` folder as it looks
after simulated hour ``hour`` of 11.11: orders and stock up to ``hour``, ads up to
``hour - adsLagHours``, each CSV rewritten in full (BOM + header + rows) through a temporary
file and a rename, then ``trang_thai.json``. Same rules and bytes as ``replay.js``, so the
watcher cannot tell them apart.

``source`` is ``live_11_11/_replay.json`` of a generated shop (the replay input, not the
answer key, which the app never reads).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

REPLAY_FILE = "_replay.json"
STATUS_FILE = "trang_thai.json"
BOM = "﻿"


def replay_source(shop_dir: Path) -> Path:
    """``<shop>/live_11_11/_replay.json``."""
    return Path(shop_dir) / "live_11_11" / REPLAY_FILE


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return data


def load_replay(source: Path) -> dict[str, Any]:
    p = Path(source)
    if not p.is_file():
        raise FileNotFoundError(f"Không thấy {p}. Chạy 'node generate.js' trước.")
    return _load(str(p.resolve()), p.stat().st_mtime)


def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="")
    tmp.replace(path)


def write_live_stream(source: Path, out_dir: Path, hour: int) -> dict[str, str]:
    """Write the state after hour ``hour`` (0–23) into ``out_dir``; returns the
    ``trang_thai.json`` payload."""
    if not 0 <= hour <= 23:
        raise ValueError("hour phải trong 0–23")
    replay = load_replay(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    ads_until = hour - int(replay["adsLagHours"])
    for key in ("orders", "ads", "inventory"):
        max_hour = ads_until if key == "ads" else hour
        lines = [line for h, line in replay["rows"][key] if h <= max_hour]
        body = "".join(f"{line}\n" for line in lines)
        _write_atomic(out_dir / replay["files"][key], BOM + replay["headers"][key] + "\n" + body)
    status = {
        "ngay": replay["ngay"],
        "don_hang_va_ton_den_het": f"{hour:02d}:59",
        "ads_den_het": f"{ads_until:02d}:59" if ads_until >= 0 else "chưa có",
        "cap_nhat_luc": datetime.now(UTC).isoformat(),
    }
    _write_atomic(out_dir / STATUS_FILE, json.dumps(status, ensure_ascii=False, indent=2))
    return status


def clear_live_stream(out_dir: Path) -> int:
    """Remove the CSVs and ``trang_thai.json`` written by a replay (reset)."""
    n = 0
    if not out_dir.is_dir():
        return 0
    for p in [*out_dir.glob("*.csv"), out_dir / STATUS_FILE, *out_dir.glob("*.tmp")]:
        if p.is_file():
            p.unlink()
            n += 1
    return n
