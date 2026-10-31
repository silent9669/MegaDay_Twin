"""Prompt-injection guard (AI-05 / AI-09, plan 07 §7).

Rules
-----
* Every free text that comes from seller / buyer data (buyer notes, product names, stock
  notes, file cells) is **data, not instructions**. Before it reaches the LLM it is wrapped
  in ``<du_lieu_khong_tin_cay nguon="...">…</du_lieu_khong_tin_cay>`` with ``<``, ``>`` and
  ``&`` escaped inside, so the text can never close the block or open a fake tag.
* :func:`scan` flags instruction-like content (``possible_instruction``). It reuses the
  ingest patterns (``app.ingest.pii.SUSPICIOUS_PATTERNS``) and adds the keyword list of
  07 §7 for short imperative sentences.
* Write / draft tools never run because of data content: they need a request from the
  user's **own** message (:func:`user_requests_draft`) or an explicit UI confirmation; the
  registry enforces it (``tools/registry.py``).
* :func:`check_output_policy` blocks an answer that pushes a sweeping ads action ("tắt toàn
  bộ quảng cáo") that no simulation result in the turn supports (07 §7 "Đầu ra").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from unidecode import unidecode

# ``app.ingest.pii`` and ``app.ingest.adapters`` import each other: load the package first.
from app.ingest import adapters as _adapters  # noqa: F401
from app.ingest.pii import suspicious_patterns

UNTRUSTED_TAG = "du_lieu_khong_tin_cay"
FLAG_INSTRUCTION = "possible_instruction"

# Sources (``nguon=``) used by the tools.
SRC_BUYER_NOTE = "ghi_chu_nguoi_mua"
SRC_PRODUCT_NAME = "ten_san_pham"
SRC_STOCK_NOTE = "ghi_chu_ton_kho"
SRC_COST_NOTE = "ghi_chu_gia_von"
SRC_FILE_CELL = "noi_dung_file"
SRC_COMMITMENT_TEXT = "dieu_khoan_chuong_trinh"

_SRC_RE = re.compile(r"[^a-z0-9_]")
_BLOCK_RE = re.compile(
    rf"<{UNTRUSTED_TAG}(?:\s+nguon=\"[^\"]*\")?>(.*?)</{UNTRUSTED_TAG}>", re.DOTALL
)

# 07 §7 keyword list, matched on unaccented lower-case text. A single keyword ("đề xuất")
# is common in honest notes, so a note is flagged when it hits an ingest pattern, or two
# keywords, or one strong keyword ("bỏ qua", "ignore", "system", "tắt toàn bộ").
_STRONG_KEYWORDS: tuple[str, ...] = (
    r"\bbo qua\b",
    r"\bignore\b",
    r"\bsystem\b",
    r"\btat (toan bo|het|tat ca)\b",
    r"\bprompt\b",
    r"\bassistant\b",
    r"\bdeveloper\b",
)
_WEAK_KEYWORDS: tuple[str, ...] = (
    r"\bhuong dan\b",
    r"\bchi dan\b",
    r"\bde xuat\b",
    r"\bra lenh\b",
    r"\bngay lap tuc\b",
    r"\bquang cao\b",
    r"\bads\b",
)


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", unidecode(text).lower())


@dataclass
class ScanResult:
    flags: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)

    @property
    def suspicious(self) -> bool:
        return FLAG_INSTRUCTION in self.flags


def scan(text: str | None) -> ScanResult:
    """Flag instruction-like content in a piece of untrusted text."""
    if not text:
        return ScanResult()
    hits = list(suspicious_patterns(text))
    key = _key(text)
    strong = [p for p in _STRONG_KEYWORDS if re.search(p, key)]
    weak = [p for p in _WEAK_KEYWORDS if re.search(p, key)]
    if hits or strong or len(weak) >= 2:
        return ScanResult(flags=[FLAG_INSTRUCTION], patterns=hits + strong + weak)
    return ScanResult()


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wrap(text: str | None, source: str) -> str | None:
    """``<du_lieu_khong_tin_cay nguon="source">escaped</du_lieu_khong_tin_cay>``."""
    if text is None:
        return None
    src = _SRC_RE.sub("_", source.lower()) or "khong_ro"
    return f'<{UNTRUSTED_TAG} nguon="{src}">{escape(text)}</{UNTRUSTED_TAG}>'


def wrap_scan(text: str | None, source: str) -> tuple[str | None, list[str]]:
    """Wrapped text and its flags (empty list when clean)."""
    return wrap(text, source), scan(text).flags


def strip_untrusted(text: str) -> str:
    """Remove every untrusted block (used by grounding: numbers inside data written by
    outsiders never count as tool-backed numbers)."""
    return _BLOCK_RE.sub(" ", text)


def untrusted_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _BLOCK_RE.finditer(text)]


def contains_untrusted(obj: Any) -> bool:
    """True if any string inside ``obj`` holds an untrusted block."""
    if isinstance(obj, str):
        return f"<{UNTRUSTED_TAG}" in obj
    if isinstance(obj, dict):
        return any(contains_untrusted(v) for v in obj.values())
    if isinstance(obj, list | tuple):
        return any(contains_untrusted(v) for v in obj)
    return False


def flagged_count(obj: Any) -> int:
    """Number of ``possible_instruction`` flags in a tool payload."""
    if isinstance(obj, dict):
        own = 1 if FLAG_INSTRUCTION in (obj.get("flags") or []) else 0
        return own + sum(flagged_count(v) for k, v in obj.items() if k != "flags")
    if isinstance(obj, list | tuple):
        return sum(flagged_count(v) for v in obj)
    return 0


# ---------------------------------------------------------------------- user intent
# Draft tools run only when the USER asked for a draft / save / submission in their own
# words (or the UI sends an explicit flag). Content of tool results is never consulted.
_DRAFT_INTENT = tuple(
    re.compile(p)
    for p in (
        r"\b(tao|luu|soan|viet|lam) (giup |cho minh |cho toi |giup minh )?(ban )?nhap\b",
        r"\bnhap quyet dinh\b",
        r"\bluu (thanh|lai|quyet dinh|de xuat)\b",
        r"\bgui (len )?(lead )?duyet\b",
        r"\btrinh duyet\b",
        r"\bghi chu ban giao\b",
        r"\bban giao ca\b",
        r"\b(ghep|map) (cot|sku)\b",
        r"\bgoi y (gia tri )?tham so\b",
        r"\bde xuat (gia tri )?tham so\b",
    )
)


def user_requests_draft(user_message: str) -> bool:
    key = _key(user_message)
    return any(p.search(key) for p in _DRAFT_INTENT)


# ---------------------------------------------------------------------- output policy
_SWEEPING_ACTION = tuple(
    re.compile(p)
    for p in (
        r"\b(nen|hay|can|de xuat|khuyen) .{0,30}\btat (toan bo|het|tat ca|moi)\b"
        r".{0,20}\b(quang cao|ads|chien dich)\b",
        r"\btat (toan bo|het|tat ca|moi) (cac )?(quang cao|ads|chien dich)\b.{0,20}"
        r"\b(ngay|luon|di)\b",
        r"\bdung (toan bo|het|tat ca) (cac )?(quang cao|ads|chien dich)\b",
        r"\bpause (all|every|toan bo)\b",
    )
)
# Negated mentions ("không đề xuất tắt toàn bộ quảng cáo") are fine.
_NEGATION = re.compile(r"\b(khong|chua|dung co|dung nen|bo qua yeu cau)\b[^.!?\n]{0,40}$")


@dataclass
class PolicyReport:
    ok: bool
    violations: list[str] = field(default_factory=list)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?\n])\s+", text) if s.strip()]


def check_output_policy(answer: str, tool_payloads: list[Any]) -> PolicyReport:
    """Block a sweeping "turn off all ads" recommendation unless a simulation in this turn
    recommends ``pause`` (07 §7)."""
    supported = _pause_supported(tool_payloads)
    violations: list[str] = []
    for sent in _sentences(answer):
        key = _key(strip_untrusted(sent))
        for p in _SWEEPING_ACTION:
            m = p.search(key)
            if m is None:
                continue
            if _NEGATION.search(key[: m.start()]):
                continue
            if not supported:
                violations.append(
                    "Câu trả lời đề xuất tắt toàn bộ quảng cáo nhưng không có kết quả mô phỏng"
                    f" nào ủng hộ: “{sent.strip()[:120]}”"
                )
            break
    return PolicyReport(ok=not violations, violations=violations)


def _pause_supported(payloads: list[Any]) -> bool:
    for p in payloads:
        for rec in _find_key(p, "recommendation"):
            if isinstance(rec, dict):
                best = rec.get("best") or {}
                if isinstance(best, dict) and best.get("kind") in ("pause", "pause_until_restock"):
                    return True
    return False


def _find_key(obj: Any, key: str) -> list[Any]:
    out: list[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            out.extend(_find_key(v, key))
    elif isinstance(obj, list | tuple):
        for v in obj:
            out.extend(_find_key(v, key))
    return out
