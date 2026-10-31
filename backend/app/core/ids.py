"""Identifiers. Internal ids are ULID strings (01 §7); natural keys (platform
SKU, order id, campaign id) are kept as-is.

``new_id("dec")`` gives ``"dec-01J…"`` — the ``<prefix>-<…>`` form used in the
API (08 §1). ULIDs sort by creation time, which keeps DuckDB zone maps useful.
"""

from __future__ import annotations

from ulid import ULID


def new_id(prefix: str | None = None) -> str:
    raw = str(ULID())
    return f"{prefix}-{raw}" if prefix else raw


def is_ulid(text: str) -> bool:
    tail = text.rsplit("-", 1)[-1]
    try:
        ULID.from_str(tail)
    except ValueError:
        return False
    return True
