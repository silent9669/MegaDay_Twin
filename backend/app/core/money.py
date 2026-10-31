"""Money parsing. All money in the system is ``int`` VND (01 §7).

Plan: docs/dev-plan/02-lop-du-lieu.md §2.4. ``parse_vnd`` returning ``None``
means the caller must raise a ``MONEY_FORMAT`` data-quality issue.
"""

from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from unidecode import unidecode

_THOUSANDS_DOT = re.compile(r"\d{1,3}(\.\d{3})+")
_THOUSANDS_COMMA = re.compile(r"\d{1,3}(,\d{3})+")
_NUMBER = re.compile(r"-?\d+(\.\d+)?")
_CURRENCY_SUFFIXES = ("vnd", "dong", "d")


def parse_vnd(value: str | int | float | None) -> int | None:
    """Parse a hand-typed VND amount into an integer number of đồng.

    Accepts ``"45000"``, ``"45.000"``, ``"45k"``, ``"45.000đ"``, ``"45 000 đ"``,
    ``"455.000đ"``, ``"3k"``, ``"1,5tr"``, ``"1.5tr"``, ``"45.000 VNĐ"``, ``"₫45.000"``.
    Vietnamese convention: ``.`` groups thousands, ``,`` is the decimal mark.
    A comma followed by exact 3-digit groups without a unit (``"45,000"``) is
    read as a thousands separator, since a fractional đồng is meaningless.
    Returns ``None`` when the text cannot be read.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return None if not math.isfinite(value) else _round(Decimal(str(value)))

    s = value.replace("₫", "d")
    s = unidecode(s).strip().lower()
    s = re.sub(r"\s+", "", s)
    for suffix in _CURRENCY_SUFFIXES:
        if s.endswith(suffix):
            s = s.removesuffix(suffix)
            break
    if s.startswith("d"):  # "₫45.000" / "d45.000"
        s = s[1:]
    if not s:
        return None

    mult = 1
    if s.endswith("k"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("tr"):
        mult, s = 1_000_000, s[:-2]
    if not s:
        return None

    sign = ""
    if s.startswith("-"):
        sign, s = "-", s[1:]

    if _THOUSANDS_DOT.fullmatch(s):
        s = s.replace(".", "")
    elif mult == 1 and _THOUSANDS_COMMA.fullmatch(s):
        s = s.replace(",", "")
    else:
        s = s.replace(",", ".")

    s = sign + s
    if not _NUMBER.fullmatch(s):
        return None
    try:
        return _round(Decimal(s) * mult)
    except InvalidOperation:  # pragma: no cover - regex already guards this
        return None


def _round(d: Decimal) -> int:
    return int(d.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_vnd(amount: int) -> str:
    """Vietnamese display: ``9280`` → ``"9.280đ"``."""
    sign = "-" if amount < 0 else ""
    return f"{sign}{abs(amount):,}đ".replace(",", ".")
