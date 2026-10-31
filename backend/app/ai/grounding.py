"""Grounding check: "AI không bịa số" (AI-04, plan 07 §6).

Runs on **every** answer before it reaches the UI.

1. :func:`extract_mentions` finds every number in the answer, Vietnamese style:
   ``690.000``, ``22,5``, ``4,05 triệu``, ``1,2 tỷ``, ``45k``, ``16.900đ``, ``30%``,
   ``22,5 sp/giờ``, times ``13:00`` / ``15h`` / ``3 giờ chiều``, dates ``12/11`` /
   ``11.11`` / ``ngày 12``. Values are normalised (``4,05 triệu`` → 4 050 000, unit
   ``vnd``). Skipped: numbers glued to codes (``CD002``, ``NOICHIEN2L``, ``500ml``,
   ``AOGIUNHIET-M``), list ordinals, ``[[src:…]]`` markers, untrusted blocks.
2. :func:`collect_tool_numbers` walks the JSON of every tool result of the turn (what the
   LLM saw) and keeps only **structured** values: JSON numbers, ISO timestamps / dates,
   strings that are just a number or a clock time. Each keeps ``call_id → json_path``, the
   source ids, the entities (SKU, campaign) of the enclosing objects and the unit its key
   names (:func:`key_unit`: ``p_target`` → pct, ``spend`` → vnd, ``window_hours`` → hour).
   Numbers inside free text (``reasons``, ``label``, ``note``, ``goal_note``…) and inside
   ``<du_lieu_khong_tin_cay>`` are never sources (M4-ai.md "Siết grounding").
3. :func:`check` matches each mention against one structured value of a fitting unit
   (tolerances of 07 §6, plus the display precision: "4,1 triệu" covers ±50 000đ; a
   percentage only matches a probability / ratio / pct key, never money or a quantity).
   The only derived numbers: the difference, sum or ratio of two values of the **same
   metric in the same tool call** (two options of one simulation), when the sentence says
   so ("chênh", "tổng", "gấp", "lợi hơn"…). It also accepts a number the user wrote in the
   question, checks that a sentence naming a SKU / campaign uses that entity's numbers,
   and attaches a citation (auto-adding or correcting the ``[[src:id]]`` chip).

Any mention without a match → ``ok = False``; :func:`rewrite_instruction` is the message
sent back to the LLM (max 2 rewrites, then a fallback answer, see ``agent.py``).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.timeutil import to_local

Unit = Literal["vnd", "units", "pct", "number", "hour", "days", "minutes", "time", "date", "year"]
Status = Literal["matched", "arith", "user_quoted", "unmatched", "entity_mismatch"]

SRC_MARK = re.compile(r"\[\[src:([^\]\s]+)\]\]")
DROP_MARK = "␟"  # placeholder for a dropped chip (never in model text)
DROP_RE = re.compile(r"[ \t]*␟")

# ---------------------------------------------------------------------- tokens
_MINUS = "-−–"
_NUM = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+,\d+|\d+(?:\.\d{1,2}(?=\s*(?:lần|%)))?|\d+"
_SCALE = {
    "tỷ": 1e9,
    "tỉ": 1e9,
    "triệu": 1e6,
    "tr": 1e6,
    "nghìn": 1e3,
    "ngàn": 1e3,
    "k": 1e3,
}
_CURRENCY = ("đồng", "vnđ", "vnd", "đ")
_UNIT_WORDS: tuple[tuple[str, Unit], ...] = (
    ("điểm phần trăm", "pct"),
    ("điểm %", "pct"),
    ("phần trăm", "pct"),
    ("%", "pct"),
    ("sản phẩm", "units"),
    ("đơn hàng", "units"),
    ("sp", "units"),
    ("cái", "units"),
    ("chiếc", "units"),
    ("đơn", "units"),
    ("nồi", "units"),
    ("áo", "units"),
    ("bình", "units"),
    ("cốc", "units"),
    ("hộp", "units"),
    ("món", "units"),
    ("lượt", "units"),
    ("mẫu", "units"),
    ("mục", "units"),
    ("biến thể", "units"),
    ("chiến dịch", "units"),
    ("tiếng", "hour"),
    ("giờ", "hour"),
    ("phút", "minutes"),
    ("ngày", "days"),
    ("lần", "number"),
)
_UNIT_RE = "|".join(re.escape(w) for w, _ in _UNIT_WORDS)
_SCALE_RE = "|".join(sorted((re.escape(k) for k in _SCALE), key=len, reverse=True))
_CUR_RE = "|".join(re.escape(c) for c in _CURRENCY)
_WORD_END = r"(?![0-9A-Za-zÀ-ỹđĐ])"

NUMBER_RE = re.compile(
    rf"(?:(?<![\w.,/:{_MINUS}])(?P<sign>[{_MINUS}])\s?(?=\d)|(?<![\w.,/:{_MINUS}]))"
    rf"(?P<num>{_NUM})"
    rf"(?:\s?(?P<scale>{_SCALE_RE}){_WORD_END})?"
    rf"(?:\s?(?P<cur>{_CUR_RE}){_WORD_END})?"
    rf"(?:\s?(?P<unit>{_UNIT_RE})(?:{_WORD_END}|(?<=%)))?",
    re.IGNORECASE,
)
TIME_RE = re.compile(
    r"(?<![\w.,:/])(?P<h>[01]?\d|2[0-4])(?::(?P<m>[0-5]\d)|h(?P<m2>[0-5]\d)?)(?!\w)"
)
TIME_WORD_RE = re.compile(
    r"(?<![\w.,])(?P<h>1[0-2]|0?\d) giờ (?P<p>sáng|trưa|chiều|tối|đêm)\b", re.IGNORECASE
)
DATE_RE = re.compile(
    r"(?<![\w.,/:])(?P<d>[0-3]?\d)(?P<sep>[/.])(?P<mo>1[0-2]|0?[1-9])(?:(?P=sep)(?P<y>\d{4}))?"
    r"(?![\w/]|\.\d|,\d|\s*(?:lần|%))"
)
DAY_ONLY_RE = re.compile(r"\bngày (?P<d>[0-3]?\d)(?![\w/]|[.,:]\d)", re.IGNORECASE)
YEAR_RE = re.compile(r"\bnăm (?P<y>20\d{2}|19\d{2})\b", re.IGNORECASE)
ORDINAL_RE = re.compile(r"(?m)^\s*(?:\d{1,2}[.)]|[-*•])\s")
APPROX_RE = re.compile(r"(khoảng|tầm|chừng|gần|xấp xỉ|ước|~|cỡ|hơn|trên|dưới)\s*$", re.IGNORECASE)
OP_RE = re.compile(
    r"\b(chênh|chênh lệch|tổng|cộng|gấp|hơn|kém|lợi hơn|tốn hơn|tăng thêm|giảm|còn lại|trừ|"
    r"bớt|thêm|so với)\b",
    re.IGNORECASE,
)
ENTITY_RE = re.compile(r"(?<![\w-])([A-Z][A-Z0-9]{1,}(?:-[A-Z0-9]+)*)(?![\w-])")
_ENTITY_STOP = frozenset(
    {"SKU", "ROAS", "IROAS", "VND", "VNĐ", "AI", "OK", "GMV", "CPC", "CTR", "ADS", "PII", "UI"}
)
ENTITY_KEYS = frozenset({"sku", "sku_id", "campaign_id", "product_id", "target_id", "skus"})
SKIP_KEYS = frozenset(
    {
        "source",
        "other_sources",
        "seed",
        "samples",
        "input_hash",
        "panel_version",
        "version",
        "href",
        "flags",
        "patterns",
    }
)
# a sentence ends at . ! ? followed by a space / end of text, or at a newline; dots inside
# numbers (1.180.000) or ids do not end it
SENTENCE_RE = re.compile(r"(?:[^.!?\n]|[.!?]+(?=[^\s.!?]))+(?:[.!?]+(?=\s|$)|\n|$)")


@dataclass
class Mention:
    raw: str
    value: float
    unit: Unit | None  # None = bare number
    start: int
    end: int
    decimals: int = 0
    scale: float = 1.0
    approx: bool = False
    signed: bool = False

    @property
    def half_ulp(self) -> float:
        return 0.5 * (10.0 ** (-self.decimals)) * self.scale


def _parse_num(text: str) -> tuple[float, int]:
    """Vietnamese number → (value, decimals shown). ``1.234,5`` → (1234.5, 1);
    ``4.3`` (only before "lần"/"%") → (4.3, 1)."""
    if "," in text:
        whole, frac = text.split(",", 1)
        return float(whole.replace(".", "") + "." + frac), len(frac)
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
        return float(text.replace(".", "")), 0
    if "." in text:
        return float(text), len(text.split(".", 1)[1])
    return float(text), 0


def _unit_of(word: str | None) -> Unit | None:
    if not word:
        return None
    w = word.lower()
    for k, u in _UNIT_WORDS:
        if w == k:
            return u
    return None


def _masked(text: str) -> str:
    """Blank out spans that never hold checkable numbers (same length, spans stay)."""
    out = list(text)

    def blank(a: int, b: int) -> None:
        for i in range(a, b):
            if out[i] != "\n":
                out[i] = " "

    for m in SRC_MARK.finditer(text):
        blank(m.start(), m.end())
    for m in re.finditer(r"<du_lieu_khong_tin_cay.*?</du_lieu_khong_tin_cay>", text, re.DOTALL):
        blank(m.start(), m.end())
    for m in ORDINAL_RE.finditer(text):
        blank(m.start(), m.end())
    return "".join(out)


def extract_mentions(text: str) -> list[Mention]:
    """Every checkable number in ``text`` (see module doc)."""
    src = _masked(text)
    taken: list[tuple[int, int]] = []
    out: list[Mention] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    def approx_before(a: int) -> bool:
        return bool(APPROX_RE.search(src[max(0, a - 12) : a]))

    for m in TIME_WORD_RE.finditer(src):
        h = int(m.group("h")) % 12
        part = m.group("p").lower()
        if part in ("chiều", "tối") or (part == "trưa" and h < 11):
            h += 12
        out.append(Mention(m.group(0), float(h * 60), "time", m.start(), m.end()))
        taken.append((m.start(), m.end()))
    for m in DATE_RE.finditer(src):
        d, mo = int(m.group("d")), int(m.group("mo"))
        if not (1 <= d <= 31) or not free(m.start(), m.end()):
            continue
        y = int(m.group("y")) if m.group("y") else 0
        out.append(Mention(m.group(0), float(y * 10000 + mo * 100 + d), "date", m.start(), m.end()))
        taken.append((m.start(), m.end()))
    for m in TIME_RE.finditer(src):
        if not free(m.start(), m.end()):
            continue
        h = int(m.group("h"))
        mins = int(m.group("m") or m.group("m2") or 0)
        if h == 24 and mins:
            continue
        out.append(Mention(m.group(0), float(h * 60 + mins), "time", m.start(), m.end()))
        taken.append((m.start(), m.end()))
    for m in DAY_ONLY_RE.finditer(src):
        a, b = m.start("d"), m.end("d")
        if not free(a, b):
            continue
        out.append(Mention(m.group("d"), float(int(m.group("d"))), "date", a, b))
        taken.append((a, b))
    for m in YEAR_RE.finditer(src):
        a, b = m.start("y"), m.end("y")
        if free(a, b):
            out.append(Mention(m.group("y"), float(m.group("y")), "year", a, b))
            taken.append((a, b))
    for m in NUMBER_RE.finditer(src):
        a = m.start("num")
        b = m.end()
        if not free(a, b):
            continue
        # glued to letters → product spec / code (2L, 500ml, x2): skip
        if b < len(src) and re.match(r"[A-Za-zÀ-ỹđĐ]", src[b]) and m.end() == m.end("num"):
            continue
        value, decimals = _parse_num(m.group("num"))
        scale = _SCALE.get((m.group("scale") or "").lower(), 1.0)
        unit: Unit | None = _unit_of(m.group("unit"))
        if m.group("cur") or (scale != 1.0 and unit is None):
            unit = "vnd"
        signed = bool(m.group("sign"))
        start = m.start("sign") if signed else a
        out.append(
            Mention(
                raw=text[start:b].strip(),
                value=value * scale,
                unit=unit,
                start=start,
                end=b,
                decimals=decimals,
                scale=scale,
                approx=approx_before(a),
                signed=signed,
            )
        )
        taken.append((a, b))
    out.sort(key=lambda x: x.start)
    return out


# ---------------------------------------------------------------------- tool numbers
@dataclass
class ToolNumber:
    value: float
    kind: Literal["num", "time", "date", "hour_int", "year"]
    call_id: str
    tool: str
    path: str
    source_ids: tuple[str, ...]
    entities: frozenset[str]
    unit_hint: Unit | None = None  # unit named by the JSON key (key_unit), None = generic


@dataclass
class ToolOutput:
    """One tool result as the grounding check sees it."""

    call_id: str
    tool: str
    payload: Any  # JSON-like (what the LLM saw)
    source_ids: tuple[str, ...] = ()
    args: Mapping[str, Any] = field(default_factory=dict)


_ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
_ISO_D = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _date_value(d: date) -> list[float]:
    return [float(d.year * 10000 + d.month * 100 + d.day), float(d.month * 100 + d.day)]


def _entities_of(obj: Mapping[str, Any]) -> set[str]:
    out: set[str] = set()
    for k, v in obj.items():
        if k in ENTITY_KEYS:
            if isinstance(v, str):
                out.add(v)
            elif isinstance(v, list):
                out.update(x for x in v if isinstance(x, str))
        elif k == "campaign" and isinstance(v, dict) and isinstance(v.get("id"), str):
            out.add(v["id"])
    return out


# Unit of a structured tool number, read from its JSON key (``p_target`` → pct,
# ``spend`` → vnd, ``window_hours`` → hour, ``sold_3h_units`` → units). Tokens of the key are
# read from the end: the last one that names a unit wins (``budget_exhausted_hours_24h`` →
# hour, ``contribution_per_unit_vnd`` → vnd; the word after ``per`` is skipped). Quantile /
# statistic keys (``mean``, ``p50``, ``interval``…) take the unit of their parent key. A key
# that names nothing stays generic (``None``): it can ground money, quantities, durations
# and bare numbers, but a percentage only as a 0–1 ratio (×100).
_KEY_TOKENS: dict[str, Unit] = {
    **dict.fromkeys(
        ("p", "prob", "probability", "pct", "percent", "share", "rate", "ratio", "confidence"),
        "pct",
    ),
    **dict.fromkeys(("minute", "minutes"), "minutes"),
    **dict.fromkeys(("hour", "hours", "window", "windows"), "hour"),
    **dict.fromkeys(("day", "days"), "days"),
    **dict.fromkeys(
        (
            "vnd",
            "spend",
            "budget",
            "price",
            "cost",
            "revenue",
            "gmv",
            "profit",
            "objective",
            "gain",
            "loss",
            "fee",
            "contribution",
            "penalty",
            "cpc",
            "aov",
        ),
        "vnd",
    ),
    **dict.fromkeys(
        (
            "units",
            "unit",
            "qty",
            "stock",
            "sold",
            "orders",
            "demand",
            "available",
            "hand",
            "change",
            "skus",
            "n",
            "count",
            "total",
        ),
        "units",
    ),
}
_STAT_KEY = re.compile(
    r"^(?:mean|median|p\d{1,2}|lo|hi|low|high|min|max|std|interval|cum_p\d{1,2})$"
)
_NUMERIC_STR = re.compile(r"^-?\d+(?:\.\d+)?$")
_CLOCK_STR = re.compile(r"^(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)$")


def key_unit(name: str) -> Unit | None:
    """Unit named by a JSON key (see ``_KEY_TOKENS``); ``None`` = generic."""
    toks = [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]
    for i in range(len(toks) - 1, -1, -1):
        if i > 0 and toks[i - 1] == "per":
            continue
        u = _KEY_TOKENS.get(toks[i])
        if u is not None:
            return u
    return None


def collect_tool_numbers(outputs: Iterable[ToolOutput]) -> list[ToolNumber]:
    """Every **structured** number of the tool results: JSON numbers, ISO timestamps / dates,
    strings that are only a number or a clock time. Numbers inside free text (``reasons``,
    ``label``, ``note``, ``goal_note``…) are not collected: a sentence written by the system
    from a value is grounded by that value itself, and a number that exists only in text
    (or in ``<du_lieu_khong_tin_cay>``) never grounds an answer."""
    found: list[ToolNumber] = []
    for o in outputs:
        base_entities = {
            str(v) for k, v in o.args.items() if k in ENTITY_KEYS and isinstance(v, str) and v
        }

        def add(
            value: float,
            kind: Literal["num", "time", "date", "hour_int", "year"],
            path: str,
            ents: set[str],
            hint: Unit | None = None,
            _o: ToolOutput = o,
        ) -> None:
            found.append(
                ToolNumber(
                    value, kind, _o.call_id, _o.tool, path, _o.source_ids, frozenset(ents), hint
                )
            )

        def walk(obj: Any, path: str, ents: set[str], key: str) -> None:
            if isinstance(obj, bool) or obj is None:
                return
            if isinstance(obj, dict):
                here = ents | _entities_of(obj)
                for k, v in obj.items():
                    if k in SKIP_KEYS or k == "id" or k.endswith("_id") or k.endswith("_ids"):
                        continue
                    walk(v, f"{path}.{k}", here, key if (key and _STAT_KEY.match(k)) else k)
                return
            if isinstance(obj, list | tuple):
                for i, v in enumerate(obj):
                    walk(v, f"{path}[{i}]", ents, key)
                return
            if isinstance(obj, int | float):
                v = float(obj)
                if not math.isfinite(v):
                    return
                add(v, "num", path, ents, key_unit(key))
                raw_key = path.rsplit(".", 1)[-1]
                if ("window" in path or "hour" in raw_key) and v == int(v) and 0 <= v <= 24:
                    add(v * 60, "hour_int", path, ents)
                return
            if isinstance(obj, str):
                s = obj.strip()
                if _ISO_DT.match(s):
                    try:
                        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                    except ValueError:
                        return
                    loc = to_local(dt) if dt.tzinfo is not None else dt
                    add(float(loc.hour * 60 + loc.minute), "time", path, ents)
                    # "dữ liệu tới 13:59" when as_of is 14:00 (exclusive mark)
                    if loc.minute == 0:
                        add(float(((loc.hour - 1) % 24) * 60 + 59), "time", path, ents)
                    for dv in _date_value(loc.date()):
                        add(dv, "date", path, ents)
                    add(float(loc.year), "year", path, ents)
                    return
                if _ISO_D.match(s):
                    d = date.fromisoformat(s)
                    for dv in _date_value(d):
                        add(dv, "date", path, ents)
                    add(float(d.year), "year", path, ents)
                    return
                if _NUMERIC_STR.match(s):
                    add(float(s), "num", path, ents, key_unit(key))
                    return
                cm = _CLOCK_STR.match(s)
                if cm:
                    add(float(int(cm.group("h")) * 60 + int(cm.group("m"))), "time", path, ents)
                # any other string is free text: its numbers are not sources

        walk(o.payload, "$", set(base_entities), "")
    return found


# ---------------------------------------------------------------------- matching
class NumberCheck(BaseModel):
    raw: str
    value: float
    unit: str | None
    sentence: int
    status: Status
    tool: str | None = None
    call_id: str | None = None
    path: str | None = None
    tool_value: float | None = None
    source_id: str | None = None
    citation: Literal["ok", "added", "corrected"] | None = None
    reason: str | None = None


class Citation(BaseModel):
    source_id: str
    kind: str | None = None
    label: str | None = None
    as_of: str | None = None
    tool: str | None = None
    href: str | None = None
    mentions: list[str] = Field(default_factory=list)


class GroundingReport(BaseModel):
    ok: bool
    numbers: list[NumberCheck]
    citations: list[Citation]
    violations: list[str]
    text: str  # answer with ``[[src:id]]`` chips added / corrected
    rewrite_count: int = 0

    @property
    def checked(self) -> int:
        return len(self.numbers)

    @property
    def passed(self) -> int:
        return sum(1 for n in self.numbers if n.status in ("matched", "arith", "user_quoted"))

    @property
    def badge(self) -> str:
        """UI badge (07 §6): "Đã kiểm số: 7/7"."""
        return f"Đã kiểm số: {self.passed}/{self.checked}"


def _tol(m: Mention, t: float) -> float:
    rel = 0.005 * abs(t)
    hu = m.half_ulp * (2.0 if m.approx else 1.0)
    if m.unit == "vnd":
        # the 1.000đ floor of 07 §6 is for amounts, never for a small number (0,5 / 80)
        return max(rel, hu, 1000.0 if m.scale == 1.0 and abs(t) >= 1e4 else 0.0)
    if m.unit in ("units", "minutes"):
        tens = 5.0 if (m.approx and m.value % 10 == 0 and m.value >= 20) else 0.0
        return max(hu, 0.5, tens)
    if m.unit == "pct":
        return max(0.5, hu)
    return max(hu, min(rel, 0.05) if abs(t) < 100 else rel)


# which tool-number units (``ToolNumber.unit_hint``, None = generic key) can ground a
# mention of each unit; a percentage is handled apart (probability / ratio / pct keys only)
_UNIT_OK: dict[Unit | None, frozenset[Unit | None]] = {
    "vnd": frozenset({"vnd", None}),
    "units": frozenset({"units", None}),
    "hour": frozenset({"hour", None}),
    "minutes": frozenset({"minutes", None}),
    "days": frozenset({"days", None}),
    "number": frozenset({None}),
}


def _scaled(m: Mention, tn: ToolNumber) -> list[float]:
    """Values of ``tn`` comparable with ``m`` in m's unit; [] = wrong unit.

    A percentage matches a pct/probability/ratio key (as 0–1 → ×100 or as 0–100), or a
    generic key holding a 0–1 ratio (×100); never money, a quantity or a duration."""
    tv = tn.value
    if m.unit == "pct":
        if tn.unit_hint == "pct":
            return [tv, tv * 100] if abs(tv) <= 1.5 else [tv]
        if tn.unit_hint is None and abs(tv) <= 1.0:
            return [tv * 100]
        return []
    if m.unit is None:  # bare number: the raw structured value, any key
        return [tv]
    if tn.unit_hint not in _UNIT_OK.get(m.unit, frozenset({None})):
        return []
    return [tv]


def _num_candidates(m: Mention, pool: Sequence[ToolNumber]) -> list[tuple[ToolNumber, float]]:
    out: list[tuple[ToolNumber, float]] = []
    v = abs(m.value)
    for tn in pool:
        if tn.kind != "num":
            if m.unit == "hour" and tn.kind == "hour_int" and abs(tn.value - v * 60) < 0.5:
                out.append((tn, tn.value / 60))
            continue
        for c in _scaled(m, tn):
            if abs(abs(c) - v) <= _tol(m, abs(c)):
                out.append((tn, c))
                break
    return out


def _time_candidates(m: Mention, pool: Sequence[ToolNumber]) -> list[tuple[ToolNumber, float]]:
    out = []
    for tn in pool:
        if tn.kind in ("time", "hour_int") and abs(tn.value % 1440 - m.value % 1440) < 0.5:
            out.append((tn, tn.value))
    return out


def _date_candidates(m: Mention, pool: Sequence[ToolNumber]) -> list[tuple[ToolNumber, float]]:
    out = []
    v = int(m.value)
    for tn in pool:
        if tn.kind != "date":
            continue
        tv = int(tn.value)
        if v < 100:  # "ngày 12"
            if tv % 100 == v:
                out.append((tn, tn.value))
        elif v < 10000:  # 12/11
            if tv % 10000 == v:
                out.append((tn, tn.value))
        elif tv == v:
            out.append((tn, tn.value))
    return out


_IDX = re.compile(r"\[\d+\]")


def _arith(m: Mention, pool: Sequence[ToolNumber]) -> tuple[ToolNumber, ToolNumber, str] | None:
    """The only derived numbers accepted (sentence must name the operation, see ``OP_RE``):
    the difference, sum or ratio of **two values of the same metric in the same tool call**,
    e.g. ``results[1].objective.mean − results[0].objective.mean`` (two options of one
    simulation), ``variants[0].units + variants[2].units``, ``p_target`` gap in điểm %.
    Same metric = same tool call, same JSON path up to list indices, a unit that fits the
    mention. Never two numbers of different kinds, never a 0 operand; a ratio only for
    "gấp … lần" / bare numbers. Unit conversions (tr ↔ đ, ratio ↔ %) are not arithmetic:
    the direct match already does them."""
    if m.unit is None and abs(m.value) < 10:
        return None
    target = abs(m.value)
    groups: dict[tuple[str, str], list[tuple[ToolNumber, float]]] = {}
    for tn in pool:
        if tn.kind != "num" or tn.value == 0:
            continue
        if m.unit == "pct":
            if tn.unit_hint != "pct":
                continue
            val = tn.value * 100 if abs(tn.value) <= 1.5 else tn.value
        elif m.unit in (None, "number"):
            if tn.unit_hint == "pct":
                continue
            val = tn.value
        else:
            if not _scaled(m, tn):
                continue
            val = tn.value
        groups.setdefault((tn.call_id, _IDX.sub("[]", tn.path)), []).append((tn, val))
    tol = _tol(m, target)
    for (_cid, gpath), items in groups.items():
        if "[]" not in gpath or len(items) < 2:
            continue
        for i, (ta, a) in enumerate(items):
            for tb, b in items[i + 1 :]:
                if abs(abs(a - b) - target) <= tol:
                    return (ta, tb, "−") if a >= b else (tb, ta, "−")
                if abs(abs(a + b) - target) <= tol:
                    return ta, tb, "+"
                if m.unit in (None, "number") and target > 0:
                    for x, y, tx, ty in ((a, b, ta, tb), (b, a, tb, ta)):
                        if y and abs(x / y - target) <= max(tol, 0.01 * target):
                            return tx, ty, "/"
    return None


def _sentences(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in SENTENCE_RE.finditer(text) if m.group(0).strip()]


def _sentence_of(spans: list[tuple[int, int]], pos: int) -> int:
    for i, (a, b) in enumerate(spans):
        if a <= pos < b:
            return i
    return max(len(spans) - 1, 0)


def _known_entities(outputs: Sequence[ToolOutput]) -> set[str]:
    out: set[str] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            out.update(_entities_of(obj))
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list | tuple):
            for v in obj:
                walk(v)

    for o in outputs:
        walk(o.payload)
        out.update(str(v) for k, v in o.args.items() if k in ENTITY_KEYS and isinstance(v, str))
    return out


def _all_strings(outputs: Sequence[ToolOutput]) -> str:
    return "\n".join(json.dumps(o.payload, ensure_ascii=False, default=str) for o in outputs)


def check(
    answer: str,
    outputs: Sequence[ToolOutput],
    *,
    user_question: str = "",
    sources: Mapping[str, Mapping[str, Any]] | None = None,
) -> GroundingReport:
    """Grounding report for ``answer`` against the tool outputs of the turn.

    ``sources`` maps ``source_id`` → source dict (``kind``, ``label``, ``as_of``, ``tool``,
    ``href``) for the citations."""
    sources = sources or {}
    pool = collect_tool_numbers(outputs)
    user_mentions = extract_mentions(user_question) if user_question else []
    known = _known_entities(outputs)
    blob = _all_strings(outputs) + "\n" + user_question
    spans = _sentences(answer)
    marks: dict[int, list[tuple[str, int, int]]] = {}
    for mk in SRC_MARK.finditer(answer):
        marks.setdefault(_sentence_of(spans, mk.start()), []).append(
            (mk.group(1), mk.start(), mk.end())
        )
    checks: list[NumberCheck] = []
    violations: list[str] = []
    inserts: list[tuple[int, str, int | None, int | None]] = []  # (pos, mark, del_a, del_b)

    sent_entities: dict[int, set[str]] = {}
    for i, (a, b) in enumerate(spans):
        sent = SRC_MARK.sub(" ", answer[a:b])
        ents = {e for e in ENTITY_RE.findall(sent) if e not in _ENTITY_STOP}
        codes = {e for e in ents if any(c.isdigit() for c in e) or "-" in e or len(e) >= 6}
        unknown = sorted(e for e in codes if e not in known and e not in blob)
        for e in unknown:
            violations.append(
                f"Đối tượng “{e}” (câu {i + 1}) không có trong kết quả công cụ của lượt này."
            )
        sent_entities[i] = {e for e in codes if e in known}

    for mt in extract_mentions(answer):
        si = _sentence_of(spans, mt.start)
        sent_text = answer[spans[si][0] : spans[si][1]] if spans else answer
        if mt.unit == "time":
            cands = _time_candidates(mt, pool)
        elif mt.unit == "date":
            cands = _date_candidates(mt, pool)
        elif mt.unit == "year":
            cands = [(t, t.value) for t in pool if t.kind == "year" and t.value == mt.value]
        else:
            cands = _num_candidates(mt, pool)
        nc = NumberCheck(raw=mt.raw, value=mt.value, unit=mt.unit, sentence=si, status="unmatched")
        ents = sent_entities.get(si, set())
        if cands and ents:
            scoped = [c for c in cands if not c[0].entities or (c[0].entities & ents)]
            if not scoped:
                nc.status = "entity_mismatch"
                nc.reason = (
                    f"Số {mt.raw} có trong kết quả công cụ nhưng không thuộc"
                    f" {', '.join(sorted(ents))}"
                )
                checks.append(nc)
                continue
            cands = scoped
        if cands:
            # prefer a candidate whose source is already cited in the sentence
            cited = {sid for sid, _, _ in marks.get(si, [])}
            best = next((c for c in cands if set(c[0].source_ids) & cited), cands[0])
            tn = best[0]
            nc.status = "matched"
            nc.tool, nc.call_id, nc.path, nc.tool_value = tn.tool, tn.call_id, tn.path, tn.value
            nc.source_id = next(
                (s for s in tn.source_ids if s in cited),
                tn.source_ids[0] if tn.source_ids else None,
            )
            if nc.source_id is not None:
                if nc.source_id in cited:
                    nc.citation = "ok"
                else:
                    wrong = marks.get(si)
                    nc.citation = "corrected" if wrong else "added"
                    inserts.append((mt.end, f" [[src:{nc.source_id}]]", None, None))
            checks.append(nc)
            continue
        if any(
            abs(u.value - mt.value) <= max(u.half_ulp, mt.half_ulp, 1e-9)
            and (u.unit == mt.unit or u.unit is None or mt.unit is None)
            for u in user_mentions
        ):
            nc.status = "user_quoted"
            nc.reason = "Số do người dùng nêu trong câu hỏi"
            checks.append(nc)
            continue
        if mt.unit not in ("time", "date", "year") and OP_RE.search(sent_text):
            ar = _arith(mt, pool)
            if ar is not None:
                ta, tb, op = ar
                nc.status = "arith"
                nc.tool, nc.call_id, nc.path = ta.tool, ta.call_id, ta.path
                nc.reason = f"Phép tính một bước: {ta.value:g} {op} {tb.value:g}"
                nc.source_id = ta.source_ids[0] if ta.source_ids else None
                checks.append(nc)
                continue
        nc.reason = "Không có trong kết quả công cụ của lượt này"
        checks.append(nc)

    for nc in checks:
        if nc.status == "unmatched":
            violations.append(f"{nc.raw} (câu {nc.sentence + 1})")
        elif nc.status == "entity_mismatch":
            violations.append(f"{nc.raw} (câu {nc.sentence + 1}): {nc.reason}")

    text = answer
    for pos, mark, _a, _b in sorted(inserts, key=lambda x: -x[0]):
        text = text[:pos] + mark + text[pos:]
    if sources:  # drop chips that point at no source of this turn (stale replay ids, typos)
        drop = DROP_MARK
        cleaned = SRC_MARK.sub(lambda m: m.group(0) if m.group(1) in sources else drop, text)
        if drop in cleaned:
            text = DROP_RE.sub("", cleaned)

    cites: dict[str, Citation] = {}
    for nc in checks:
        if nc.source_id is None:
            continue
        src = sources.get(nc.source_id, {})
        c = cites.setdefault(
            nc.source_id,
            Citation(
                source_id=nc.source_id,
                kind=src.get("kind"),
                label=src.get("label"),
                as_of=src.get("as_of") or src.get("data_as_of"),
                tool=src.get("tool"),
                href=src.get("href"),
            ),
        )
        c.mentions.append(nc.raw)
    return GroundingReport(
        ok=not violations,
        numbers=checks,
        citations=list(cites.values()),
        violations=violations,
        text=text,
    )


def rewrite_instruction(report: GroundingReport) -> str:
    """Message sent back to the LLM when the check fails (07 §6)."""
    items = "; ".join(report.violations[:12])
    return (
        f"Các số hoặc đối tượng sau không có trong kết quả công cụ: {items}. "
        "Viết lại câu trả lời, chỉ dùng số từ kết quả công cụ của lượt này (ghi [[src:id]] ngay"
        " sau mỗi số) hoặc gọi thêm công cụ. Chỉ lấy số ở các trường số; số nằm trong câu chữ"
        " (lý do, ghi chú, nhãn) không được tính. Không tự tính, không ước đoán."
    )


def raw_numbers_table(outputs: Sequence[ToolOutput], limit: int = 12) -> str:
    """Fallback after failed rewrites: the main numbers straight from the tools."""
    lines = []
    for o in outputs:
        sid = o.source_ids[0] if o.source_ids else o.call_id
        nums = [t for t in collect_tool_numbers([o]) if t.kind == "num"][:limit]
        if not nums:
            continue
        shown = ", ".join(f"{t.path.removeprefix('$.')} = {_fmt_vi(t.value)}" for t in nums[:6])
        lines.append(f"- {o.tool} [[src:{sid}]]: {shown}")
    return "\n".join(lines)


def _fmt_vi(x: float) -> str:
    if float(x).is_integer():
        return f"{int(x):,}".replace(",", ".")
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
