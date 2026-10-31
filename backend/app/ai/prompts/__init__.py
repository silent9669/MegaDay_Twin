"""Vietnamese prompts (07 §5). ``system_prompt(flow, **context)`` = base + flow part;
``PROMPT_VERSION`` changes whenever a prompt file changes (part of the record/replay and
cache keys)."""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path
from typing import Any

PROMPT_DIR = Path(__file__).parent


@cache
def load(name: str) -> str:
    return (PROMPT_DIR / f"{name}.vi.md").read_text(encoding="utf-8").strip()


def _version() -> str:
    h = hashlib.sha256()
    for p in sorted(PROMPT_DIR.glob("*.vi.md")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


PROMPT_VERSION = _version()


class _Safe(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "chưa rõ"


def system_prompt(flow: str, **context: Any) -> str:
    base = load("system_base").format_map(_Safe(context))
    flow_file = PROMPT_DIR / f"flow_{flow}.vi.md"
    part = load(f"flow_{flow}") if flow_file.exists() else ""
    return base + ("\n\n" + part if part else "")
