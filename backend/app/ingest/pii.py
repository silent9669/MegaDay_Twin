"""Personal data handling at read time (02 §2.1, 10 §9).

* :func:`split_pii_columns` decides which header columns are personal data.
  Adapters pass the kept list to ``read_csv(usecols=...)`` so buyer name,
  phone and address are never loaded into memory.
* :func:`mask_contacts` hides phone numbers and e-mails inside free text.
* :func:`scan_note` = mask + prompt-injection scan. Buyer notes and stock notes
  are kept (they carry useful signals) but are **untrusted data**: the AI layer
  wraps them as ``<du_lieu_khong_tin_cay>`` (07 §1, §9) and a match on an
  instruction pattern raises ``SUSPICIOUS_NOTE``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel
from unidecode import unidecode

from app.ingest.adapters.base import clean_header

# Exact headers seen in Seller Center exports (02 §2.1) …
PII_COLUMNS: frozenset[str] = frozenset({"Người mua", "Số điện thoại", "Địa chỉ nhận hàng"})

# … and a keyword fallback (unaccented, lowercase) for other exports. Matching
# is on whole header text, so "Ghi chú của người mua" is NOT personal data.
_PII_HEADER_PATTERNS = [
    re.compile(p)
    for p in (
        r"^(ten )?(nguoi mua|nguoi nhan|khach hang)$",
        r"^ho( va)? ten",
        r"^ten (nguoi mua|nguoi nhan|khach hang|dang nhap)",
        r"(so )?dien thoai|^sdt|phone",
        r"dia chi",
        r"address",
        r"e-?mail",
        r"^(buyer|customer|recipient)( name)?$",
        r"username|tai khoan nguoi mua",
        r"cmnd|cccd|so can cuoc",
    )
]

MASK = "***"

# VN phone: 0xxxxxxxxx / +84xxxxxxxxx / 84xxxxxxxxx, with optional . - space.
_PHONE = re.compile(r"(?<!\d)(?:\+?84|0)(?:[\s.\-]?\d){8,10}(?!\d)")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")

# 02 §3.1 (after unidecode + lowercase), plus a few close variants.
SUSPICIOUS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"bo qua (moi|tat ca|cac|toan bo)? ?(cac )?(huong dan|chi dan|lenh)",
        r"ignore (all|any|previous|prior|the above)( previous| prior)? (instructions|prompts?)",
        r"disregard (all|previous|prior|the above)",
        r"tat (toan bo|het|tat ca) (cac )?(quang cao|chien dich|ads)",
        r"de xuat .* ngay lap tuc",
        r"system prompt",
        r"(ban la|you are now) (mot )?(tro ly|assistant|ai)",
    )
)


def is_pii_column(name: str) -> bool:
    header = clean_header(name)
    if header in PII_COLUMNS:
        return True
    key = unidecode(header).lower().strip()
    if "ghi chu" in key or "note" in key:
        return False
    return any(p.search(key) for p in _PII_HEADER_PATTERNS)


def split_pii_columns(columns: Iterable[str]) -> tuple[list[str], list[str]]:
    """Return ``(kept, dropped)`` header names (cleaned), order preserved."""
    kept: list[str] = []
    dropped: list[str] = []
    for c in columns:
        header = clean_header(c)
        (dropped if is_pii_column(header) else kept).append(header)
    return kept, dropped


def mask_contacts(text: str) -> tuple[str, int]:
    """Replace phone numbers and e-mails with ``***``. Returns (text, n_masked)."""
    out, n_email = _EMAIL.subn(MASK, text)
    out, n_phone = _PHONE.subn(MASK, out)
    return out, n_email + n_phone


def suspicious_patterns(text: str) -> list[str]:
    """Instruction-like patterns found in a free-text note (empty = none)."""
    key = re.sub(r"\s+", " ", unidecode(text).lower())
    return [p.pattern for p in SUSPICIOUS_PATTERNS if p.search(key)]


class ScannedNote(BaseModel):
    """A free-text note ready to store. ``trust`` is always ``untrusted``."""

    text: str | None  # masked; None when the source cell was empty
    masked: int = 0
    flagged: bool = False
    patterns: list[str] = []
    trust: str = "untrusted"


def scan_note(raw: str | None) -> ScannedNote:
    if raw is None or not raw.strip():
        return ScannedNote(text=None)
    text, n = mask_contacts(raw.strip())
    hits = suspicious_patterns(text)
    return ScannedNote(text=text, masked=n, flagged=bool(hits), patterns=hits)
