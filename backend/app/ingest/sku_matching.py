"""Ghép SKU 3 nguồn (ING-08). Plan: docs/dev-plan/02-lop-du-lieu.md §5.

Hàm thuần, không phụ thuộc adapter. Pipeline dựng :class:`AliasRecord` cho từng
nguồn (``orders`` / ``inventory`` / ``cost``) và :class:`CatalogSku` cho danh mục
SKU sàn (khóa chuẩn, lấy từ file đơn hàng), rồi gọi :func:`resolve`. Kết quả là
một :class:`AliasMatch` cho mỗi alias, đúng các cột của ``sku_aliases``, kèm top 3
ứng viên cho hàng chờ xác nhận. Ghi DB: :func:`write_aliases`; người dùng xác nhận:
:func:`apply_confirmation`; lần nhập sau dùng lại xác nhận cũ: :func:`load_known_aliases`.

Điểm của một cặp (alias ``a``, SKU ``c``) — xem docs/quyet-dinh/M1-ghep-sku.md:

* ``s_name = 0.40·token_set + 0.20·ratio + 0.20·jaccard_token + 0.20·s_variant``
  trên tên đã chuẩn hóa (:func:`norm_name`). ``s_variant`` = 1 nếu alias chứa đủ
  token biến thể của ``c``, 0,5 nếu alias không nói biến thể. Alias nhắc tới biến
  thể của **SKU anh em** (cùng ``product_id``) mà không phải của ``c`` → điểm 0
  (ràng buộc cứng: ghép nhầm biến thể làm sai tồn kho). Số mẫu khác nhau
  ("mẫu 2" ≠ "mẫu 3", hoặc alias ghi "mẫu 2" mà SKU không có) cũng loại cặp.
* Tên viết tắt mà sheet tồn dùng chung cho nhiều mã nội bộ (tên bị cắt cụt): ở
  nguồn không có mã (giá vốn), mọi SKU mà tên đó có thể chỉ tới nhận cùng điểm, nên
  chỉ loại trừ 1–1 mới tách được. Cùng (nguồn, mã, tên) mà khác giá → không tự động.
* Mã dòng ``row:<n>`` (file giá vốn, :data:`ROW_CODE_PREFIX`) chỉ để tách hai dòng
  cùng tên thành hai alias (khóa UNIQUE của ``sku_aliases``); khi chấm điểm nó được
  coi là "không có mã" (:func:`has_code`).
* Mã nội bộ ``<gốc>-<hậu tố>`` (sheet tồn): hậu tố khớp đầu mã biến thể sàn /
  tên biến thể → +0,05; không khớp trong khi một SKU anh em khớp → −0,15.
* Giá (file giá vốn): giá vốn > giá niêm yết → −0,20; < 15 % giá niêm yết → −0,10.
* Dòng xuất kho theo ngày (sheet tồn: tồn hôm trước − tồn hôm nay) so với số bán
  theo ngày của SKU (đơn hàng + phần combo ăn tồn): ``s_flow = 1 − 1,5·err``,
  ``err = Σ|x−y| / (Σx+Σy)``; ``score = (s_name + s_flow) / 2`` khi có đủ dữ liệu.

Gán 1–1 trong mỗi nguồn bằng Hungarian (``linear_sum_assignment`` trên ``1−score``).
Ngưỡng (02 §5.3): ``auto_accepted`` khi điểm ≥ 0,90 và hơn ứng viên thứ hai ≥ 0,05
(ứng viên thứ hai bỏ qua SKU đã bị alias khác *tự động* nhận — loại trừ lặp tới
điểm dừng); ``pending`` có gợi ý khi điểm ≥ 0,65; dưới 0,65 ``pending`` không gợi
ý (``sku_id`` NULL). ``rejected`` chỉ do người dùng đặt (alias không phải hàng
của shop). Các alias tồn kho cùng gốc mã mà trỏ về nhiều ``product_id`` bị hạ về
``pending``.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from itertools import pairwise
from typing import Any, Literal

import duckdb
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz import fuzz, process
from scipy.optimize import linear_sum_assignment
from unidecode import unidecode

from app.core.errors import NotFound, ValidationFailed
from app.warehouse import repo

SourceType = Literal["orders", "inventory", "cost"]
AliasStatus = Literal["auto_accepted", "pending", "confirmed", "rejected"]
MatchMethod = Literal["exact", "code_pattern", "fuzzy", "user"]

# 02 §5.1. Người dùng có thể bổ sung (parameters: scope shop, key alias_abbreviations).
ABBREV: dict[str, str] = {
    "gn": "giu nhiet",
    "kd": "khong dau",
    "tt": "trang tri",
    "tp": "thuc pham",
    "ct": "cam tay",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


class MatchConfig(BaseModel):
    """Ngưỡng và trọng số. Giá trị mặc định là quyết định của ING-08."""

    model_config = ConfigDict(frozen=True)

    auto_threshold: float = 0.90
    auto_margin: float = 0.05
    suggest_threshold: float = 0.65
    block_min_token_set: float = 0.50  # chặn ứng viên: token_set_ratio tối thiểu
    w_token_set: float = 0.40
    w_ratio: float = 0.20
    w_jaccard: float = 0.20
    w_variant: float = 0.20
    code_bonus: float = 0.05
    code_penalty: float = 0.15
    price_above_list_penalty: float = 0.20
    price_too_low_penalty: float = 0.10
    price_min_ratio: float = 0.15
    flow_weight: float = 1.0
    flow_err_scale: float = 1.5
    flow_min_units: float = 10.0
    flow_min_days: int = 14
    top_k: int = 3
    abbreviations: dict[str, str] = Field(default_factory=lambda: dict(ABBREV))


class CatalogSku(BaseModel):
    """SKU sàn (khóa chuẩn). ``daily_out``: số đơn vị rời kho theo ngày (ISO
    ``YYYY-MM-DD``) = số bán của SKU + phần combo ăn tồn (:func:`add_bundle_consumption`)."""

    model_config = ConfigDict(frozen=True)

    sku_id: str
    product_id: str
    product_name: str
    variant_name: str = ""
    list_price: int | None = None
    is_bundle: bool = False
    daily_out: dict[str, float] | None = None


ROW_CODE_PREFIX = "row:"


def has_code(raw_code: str) -> bool:
    """A real source code (stock-sheet internal code, platform SKU). ``''`` and the
    cost file's line codes ``row:<n>`` are "no code" for scoring."""
    return bool(raw_code) and not raw_code.startswith(ROW_CODE_PREFIX)


class AliasRecord(BaseModel):
    """Một tên/mã thô cần ghép. ``raw_code`` = '' khi nguồn không có mã (B2,
    INF-backend.md), ``row:<n>`` cho dòng giá vốn trùng tên (chỉ để tách khóa, coi
    như không có mã). ``price``: giá vốn (nguồn cost). ``daily_out``: xuất kho theo
    ngày (nguồn inventory, :func:`stock_flow`)."""

    model_config = ConfigDict(frozen=True)

    source_type: SourceType
    raw_code: str = ""
    raw_name: str
    price: int | None = None
    daily_out: dict[str, float] | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.source_type, self.raw_code, self.raw_name)


class Candidate(BaseModel):
    sku_id: str
    score: float


class AliasMatch(BaseModel):
    """Một dòng ``sku_aliases`` + thông tin cho hàng chờ (không lưu DB)."""

    alias_id: str
    source_type: SourceType
    raw_code: str
    raw_name: str
    norm_name: str
    sku_id: str | None
    score: float | None
    method: MatchMethod | None
    status: AliasStatus
    confirmed_by: str | None = None
    candidates: list[Candidate] = Field(default_factory=list)
    reason: str = ""

    def to_row(self) -> dict[str, Any]:
        return {
            "alias_id": self.alias_id,
            "source_type": self.source_type,
            "raw_code": self.raw_code,
            "raw_name": self.raw_name,
            "norm_name": self.norm_name,
            "sku_id": self.sku_id,
            "score": self.score,
            "method": self.method,
            "status": self.status,
            "confirmed_by": self.confirmed_by,
        }

    @property
    def is_resolved(self) -> bool:
        """Dùng được làm ánh xạ alias → SKU (auto hoặc người dùng xác nhận)."""
        return self.status in ("auto_accepted", "confirmed") and self.sku_id is not None


class MatchResult(BaseModel):
    matches: list[AliasMatch]

    @property
    def queue(self) -> list[AliasMatch]:
        """Hàng chờ người xác nhận (``UNMATCHED_SKU``)."""
        return [m for m in self.matches if m.status == "pending"]

    def mapping(self, source_type: SourceType) -> dict[tuple[str, str], str]:
        """(raw_code, raw_name) → sku_id cho các alias đã chốt của một nguồn."""
        return {
            (m.raw_code, m.raw_name): m.sku_id
            for m in self.matches
            if m.source_type == source_type and m.is_resolved and m.sku_id is not None
        }

    def stats(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for m in self.matches:
            out[m.source_type][m.status] += 1
            out[m.source_type]["total"] += 1
        return {k: dict(v) for k, v in out.items()}


# ================================================================ normalisation
def norm_name(text: str, abbreviations: Mapping[str, str] | None = None) -> str:
    """unidecode → lowercase → bỏ ký tự thừa → mở rộng viết tắt → gộp khoảng trắng."""
    abbrev = ABBREV if abbreviations is None else abbreviations
    s = _NON_ALNUM.sub(" ", unidecode(text).lower())
    return " ".join(" ".join(abbrev.get(t, t) for t in s.split()).split())


def alias_id_for(source_type: str, raw_code: str, raw_name: str) -> str:
    """Id tất định theo khóa tự nhiên, nên nạp lại cùng alias ra cùng id."""
    digest = hashlib.sha1(f"{source_type}\x1f{raw_code}\x1f{raw_name}".encode()).hexdigest()
    return f"sal-{digest[:20]}"


def stock_flow(snapshots: Mapping[str, int | None]) -> dict[str, float]:
    """Xuất kho theo ngày từ tồn cuối ngày: ``tồn(d−1) − tồn(d)``. Bỏ ngày có tồn
    sai (None) hoặc tồn tăng (nhập hàng che mất số bán)."""
    out: dict[str, float] = {}
    prev: int | None = None
    for day in sorted(snapshots):
        cur = snapshots[day]
        if prev is not None and cur is not None and prev - cur >= 0:
            out[day] = float(prev - cur)
        prev = cur
    return out


def add_bundle_consumption(
    daily_sales: Mapping[str, Mapping[str, float]],
    bundles: Mapping[str, Sequence[tuple[str, int]]],
) -> dict[str, dict[str, float]]:
    """Cộng phần combo ăn tồn vào thành phần: ``out[comp][d] += qty × bán(combo, d)``."""
    out = {sku: dict(days) for sku, days in daily_sales.items()}
    for bundle, comps in bundles.items():
        for day, units in daily_sales.get(bundle, {}).items():
            for comp, qty in comps:
                row = out.setdefault(comp, {})
                row[day] = row.get(day, 0.0) + qty * units
    return out


# ================================================================ scoring
class _Sku:
    __slots__ = ("c", "keys", "models", "norm", "prod", "sibling_vars", "var", "var_set")

    def __init__(self, c: CatalogSku, abbrev: Mapping[str, str]) -> None:
        self.c = c
        self.norm = norm_name(f"{c.product_name} {c.variant_name}", abbrev)
        self.prod: Counter[str] = Counter(norm_name(c.product_name, abbrev).split())
        self.models = _model_numbers(norm_name(c.product_name, abbrev))
        self.var: list[str] = norm_name(c.variant_name, abbrev).split()
        self.var_set = set(self.var)
        self.sibling_vars: set[str] = set()
        suffix = c.sku_id.split("-", 1)[1].lower() if "-" in c.sku_id and not c.is_bundle else ""
        self.keys = {k for k in [suffix, "".join(self.var), *self.var] if k}


def _code_suffix(raw_code: str) -> str:
    if not has_code(raw_code):
        return ""
    return raw_code.rsplit("-", 1)[1].lower() if "-" in raw_code else ""


def _code_base(raw_code: str) -> str:
    return raw_code.rsplit("-", 1)[0] if "-" in raw_code else raw_code


def _model_numbers(norm: str) -> set[str]:
    """Số mẫu trong tên: "Cốc tự khuấy (mẫu 2)" → {"2"}."""
    toks = norm.split()
    return {b for a, b in pairwise(toks) if a == "mau" and b.isdigit()}


def _variant_score(tokens: Counter[str], models: set[str], sku: _Sku) -> float | None:
    """1 / 0,5 / None (= mâu thuẫn biến thể hoặc số mẫu, loại cặp)."""
    if models and models != sku.models:
        return None
    residual = tokens - sku.prod
    mentioned = set(residual) & sku.sibling_vars - sku.var_set
    if mentioned:
        return None
    if not sku.var:
        return 1.0
    # Chỉ đòi token biến thể không nằm sẵn trong tên sản phẩm ("mẫu" của "Mẫu B" khi
    # tên đã có "(mẫu 2)").
    need = Counter(sku.var) - sku.prod
    return 1.0 if not (need - residual) else 0.5


def _flow_matrix(
    rows: Sequence[Mapping[str, float] | None], days: list[str]
) -> tuple[np.ndarray, np.ndarray]:
    idx = {d: i for i, d in enumerate(days)}
    values = np.zeros((len(rows), len(days)))
    present = np.zeros((len(rows), len(days)), dtype=bool)
    for r, row in enumerate(rows):
        for d, v in (row or {}).items():
            values[r, idx[d]] = v
            present[r, idx[d]] = True
    return values, present


def _score_source(
    aliases: list[AliasRecord],
    skus: list[_Sku],
    cfg: MatchConfig,
) -> np.ndarray:
    """Ma trận điểm alias × SKU (0 = không phải ứng viên)."""
    n_a, n_c = len(aliases), len(skus)
    scores = np.zeros((n_a, n_c))
    if n_a == 0 or n_c == 0:
        return scores
    a_norm = [norm_name(a.raw_name, cfg.abbreviations) for a in aliases]
    c_norm = [s.norm for s in skus]
    tset = process.cdist(a_norm, c_norm, scorer=fuzz.token_set_ratio, workers=-1) / 100.0
    ratio = process.cdist(a_norm, c_norm, scorer=fuzz.ratio, workers=-1) / 100.0

    use_flow = any(s.c.daily_out for s in skus)
    days = sorted(
        {d for a in aliases for d in (a.daily_out or {})}
        | {d for s in skus for d in (s.c.daily_out or {})}
    )
    a_flow, a_present = _flow_matrix([a.daily_out for a in aliases], days)
    c_flow, _ = _flow_matrix([s.c.daily_out for s in skus], days)

    for i, a in enumerate(aliases):
        tokens = Counter(a_norm[i].split())
        models = _model_numbers(a_norm[i])
        token_set = set(tokens)
        suffix = _code_suffix(a.raw_code)
        mask = a_present[i]
        flow_ok = (
            use_flow
            and int(mask.sum()) >= cfg.flow_min_days
            and float(a_flow[i, mask].sum()) >= cfg.flow_min_units
        )
        for j in np.nonzero(tset[i] >= cfg.block_min_token_set)[0]:
            sku = skus[j]
            if not token_set & set(sku.prod):  # chặn: phải chung ≥ 1 token sản phẩm
                continue
            s_var = _variant_score(tokens, models, sku)
            if s_var is None:
                continue
            c_tokens = Counter(sku.norm.split())
            union = sum((tokens | c_tokens).values())
            jacc = sum((tokens & c_tokens).values()) / union if union else 0.0
            if a_norm[i] == sku.norm:
                s = 1.0
            else:
                s = (
                    cfg.w_token_set * tset[i, j]
                    + cfg.w_ratio * ratio[i, j]
                    + cfg.w_jaccard * jacc
                    + cfg.w_variant * s_var
                )
            if suffix and sku.keys:
                if any(k.startswith(suffix) for k in sku.keys):
                    s += cfg.code_bonus
                else:
                    s -= cfg.code_penalty
            if a.price is not None and sku.c.list_price:
                r = a.price / sku.c.list_price
                if r > 1.0:
                    s -= cfg.price_above_list_penalty
                elif r < cfg.price_min_ratio:
                    s -= cfg.price_too_low_penalty
            s = min(1.0, max(0.0, s))
            if flow_ok:
                x = a_flow[i, mask]
                y = c_flow[j, mask]
                denom = float(x.sum() + y.sum())
                err = float(np.abs(x - y).sum()) / denom if denom else 1.0
                s_flow = max(0.0, 1.0 - cfg.flow_err_scale * err)
                s = (s + cfg.flow_weight * s_flow) / (1.0 + cfg.flow_weight)
            scores[i, j] = max(s, 1e-6)
    return scores


# ================================================================ resolve
def resolve(
    aliases: Iterable[AliasRecord],
    catalog: Iterable[CatalogSku],
    *,
    known: Iterable[AliasMatch] = (),
    config: MatchConfig | None = None,
) -> MatchResult:
    """Ghép mọi alias vào SKU sàn. ``known``: alias người dùng đã ``confirmed`` /
    ``rejected`` ở lần trước (:func:`load_known_aliases`), được dùng lại nguyên trạng
    và giữ chỗ SKU trong gán 1–1."""
    cfg = config or MatchConfig()
    cat = list({c.sku_id: c for c in catalog}.values())
    skus = [_Sku(c, cfg.abbreviations) for c in cat]
    by_product: dict[str, list[_Sku]] = defaultdict(list)
    for s in skus:
        by_product[s.c.product_id].append(s)
    for group in by_product.values():
        for s in group:
            s.sibling_vars = {t for o in group if o is not s for t in o.var} - s.var_set
    # SKU có biến thể: alias nhắc một giá trị biến thể bất kỳ của danh mục (vd "hồng")
    # mà SKU này không có thì cũng là mâu thuẫn (SKU anh em có thể chưa có đơn nào).
    vocab = {t for s in skus for t in s.var}
    for s in skus:
        if s.var:
            s.sibling_vars |= vocab - s.var_set
    sku_ids = {c.sku_id for c in cat}
    product_of = {c.sku_id: c.product_id for c in cat}
    known_map: dict[tuple[str, str, str], AliasMatch] = {
        (k.source_type, k.raw_code, k.raw_name): k
        for k in known
        if k.status in ("confirmed", "rejected")
    }

    all_aliases = list(aliases)
    uniq = list({a.key: a for a in all_aliases}.values())
    # Cùng (nguồn, mã, tên) nhưng khác giá → nhiều dòng thật bị gộp làm một alias
    # (UNIQUE của sku_aliases): không được tự động.
    prices: dict[tuple[str, str, str], set[int | None]] = defaultdict(set)
    for a in all_aliases:
        prices[a.key].add(a.price)
    duplicated = {k for k, v in prices.items() if len(v) > 1}
    out: list[AliasMatch] = []
    by_source: dict[str, list[AliasRecord]] = defaultdict(list)
    held: dict[str, set[str]] = defaultdict(set)  # SKU đã có alias chốt trong nguồn

    def make(a: AliasRecord, **kw: Any) -> AliasMatch:
        return AliasMatch(
            alias_id=alias_id_for(*a.key),
            source_type=a.source_type,
            raw_code=a.raw_code,
            raw_name=a.raw_name,
            norm_name=norm_name(a.raw_name, cfg.abbreviations),
            **kw,
        )

    for a in uniq:
        prev = known_map.get(a.key)
        if prev is not None:
            out.append(
                make(
                    a,
                    sku_id=prev.sku_id,
                    score=prev.score,
                    method=prev.method or "user",
                    status=prev.status,
                    confirmed_by=prev.confirmed_by,
                    reason="known_user_decision",
                )
            )
            if prev.sku_id:
                held[a.source_type].add(prev.sku_id)
        elif has_code(a.raw_code) and a.raw_code in sku_ids:
            out.append(
                make(
                    a,
                    sku_id=a.raw_code,
                    score=1.0,
                    method="exact",
                    status="auto_accepted",
                    candidates=[Candidate(sku_id=a.raw_code, score=1.0)],
                    reason="exact_code",
                )
            )
            held[a.source_type].add(a.raw_code)
        else:
            by_source[a.source_type].append(a)

    # Nguồn có mã (sheet tồn) chạy trước: tên viết tắt dùng chung cho nhiều mã nội bộ
    # cho biết tên đó mơ hồ, và các SKU nó có thể chỉ tới (xem _resolve_source).
    shorthand: dict[str, set[str]] = {}
    ordered = sorted(by_source.items(), key=lambda kv: not any(has_code(a.raw_code) for a in kv[1]))
    for source, items in ordered:
        pool = [s for s in skus if s.c.sku_id not in held[source]]
        res = _resolve_source(items, pool, product_of, cfg, make, shorthand, duplicated)
        out.extend(res)
        if any(has_code(a.raw_code) for a in items):
            names: dict[str, set[str]] = defaultdict(set)
            codes: dict[str, set[str]] = defaultdict(set)
            for m in res:
                codes[m.norm_name].add(m.raw_code)
                if m.sku_id:
                    names[m.norm_name].add(m.sku_id)
                names[m.norm_name].update(c.sku_id for c in m.candidates[:1])
            for name, ids in names.items():
                if len(codes[name]) > 1 and len(ids) > 1:
                    shorthand.setdefault(name, set()).update(ids)
    order = {a.key: n for n, a in enumerate(uniq)}
    out.sort(key=lambda m: order[(m.source_type, m.raw_code, m.raw_name)])
    return MatchResult(matches=out)


def _resolve_source(
    items: list[AliasRecord],
    pool: list[_Sku],
    product_of: Mapping[str, str],
    cfg: MatchConfig,
    make: Callable[..., AliasMatch],
    shorthand: Mapping[str, set[str]],
    duplicated: set[tuple[str, str, str]],
) -> list[AliasMatch]:
    scores = _score_source(items, pool, cfg)
    n_a = len(items)
    col = {s.c.sku_id: j for j, s in enumerate(pool)}
    for i, a in enumerate(items):
        # Tên viết tắt mà sheet tồn dùng cho nhiều SKU: không SKU nào trong nhóm được
        # hơn SKU khác nhờ tên (vd "Túi zip bảo quản TP - gói 1" trùng tên đủ của
        # mẫu gốc lẫn tên cụt của mẫu 2..6). Chỉ loại trừ 1–1 mới tách được.
        group = [col[k] for k in shorthand.get(norm_name(a.raw_name, cfg.abbreviations), ())
                 if k in col]  # fmt: skip
        if len(group) > 1 and not has_code(a.raw_code):
            scores[i, group] = scores[i, group].max()
    assigned = np.full(n_a, -1)
    if pool:
        rows, cols = linear_sum_assignment(1.0 - scores)
        for r, c in zip(rows, cols, strict=True):
            if scores[r, c] > 0:
                assigned[r] = c

    auto = np.zeros(n_a, dtype=bool)
    demoted = np.zeros(n_a, dtype=bool)
    second = np.zeros(n_a)
    groups: dict[str, list[int]] = defaultdict(list)
    for i, a in enumerate(items):
        if has_code(a.raw_code):
            groups[_code_base(a.raw_code)].append(i)

    for _ in range(100):  # đơn điệu trừ khi bị hạ; chặn trên cho chắc
        claimed = np.zeros(len(pool), dtype=bool)
        claimed[assigned[auto]] = True
        new_auto = np.zeros(n_a, dtype=bool)
        for i in range(n_a):
            j = assigned[i]
            if j < 0 or demoted[i] or items[i].key in duplicated:
                continue
            row = scores[i].copy()
            others = claimed.copy()
            if auto[i]:
                others[j] = False
            row[others] = 0.0
            row[j] = 0.0
            second[i] = float(row.max()) if len(row) else 0.0
            s = scores[i, j]
            new_auto[i] = s >= cfg.auto_threshold and s - second[i] >= cfg.auto_margin
        # Cùng gốc mã nội bộ mà trỏ nhiều product_id → hạ về pending (không tin).
        for members in groups.values():
            prods = {product_of[pool[assigned[i]].c.sku_id] for i in members if new_auto[i]}
            if len(prods) > 1:
                for i in members:
                    demoted[i] = True
                    new_auto[i] = False
        if np.array_equal(new_auto, auto):
            break
        auto = new_auto

    result: list[AliasMatch] = []
    for i, a in enumerate(items):
        order: list[int] = [int(j) for j in np.argsort(-scores[i])[: cfg.top_k]] if pool else []
        cands = [
            Candidate(sku_id=pool[j].c.sku_id, score=round(float(scores[i, j]), 4))
            for j in order
            if scores[i, j] > 0
        ]
        j = assigned[i]
        method: MatchMethod = "code_pattern" if _code_suffix(a.raw_code) else "fuzzy"
        if j < 0:
            result.append(
                make(a, sku_id=None, score=None, method=None, status="pending",
                     candidates=cands, reason="no_candidate")
            )  # fmt: skip
            continue
        s = round(float(scores[i, j]), 4)
        sku_id = pool[j].c.sku_id
        if auto[i]:
            status: AliasStatus = "auto_accepted"
            reason = "auto"
        else:
            status = "pending"
            if items[i].key in duplicated:
                reason = "duplicate_raw_name"
            elif demoted[i]:
                reason = "code_group_conflict"
            elif s < cfg.suggest_threshold:
                reason = "low_score"
            elif s < cfg.auto_threshold:
                reason = "below_auto_threshold"
            else:
                reason = "low_margin"
        keep = status == "auto_accepted" or s >= cfg.suggest_threshold
        result.append(
            make(
                a,
                sku_id=sku_id if keep else None,
                score=s,
                method=method,
                status=status,
                candidates=cands,
                reason=reason,
            )
        )
    return result


# ================================================================ persistence
_ALIAS_KEY = ("source_type", "raw_code", "raw_name")


def write_aliases(
    conn: duckdb.DuckDBPyConnection,
    result: MatchResult | Sequence[AliasMatch],
    catalog: Iterable[CatalogSku] = (),
) -> int:
    """Upsert ``sku_aliases`` theo khóa tự nhiên. Không ghi đè alias người dùng đã
    ``confirmed``/``rejected`` bằng kết quả máy. SKU được tham chiếu mà chưa có trong
    ``skus`` thì tạo dòng giữ chỗ (``repo.ensure_sku``)."""
    matches = result.matches if isinstance(result, MatchResult) else list(result)
    if not matches:
        return 0
    cat = {c.sku_id: c for c in catalog}
    decided = {
        (r["source_type"], r["raw_code"], r["raw_name"])
        for r in repo.fetch_all(
            conn,
            "SELECT source_type, raw_code, raw_name FROM sku_aliases "
            "WHERE status IN ('confirmed', 'rejected')",
        )
    }
    have = {str(r[0]) for r in conn.execute("SELECT sku_id FROM skus").fetchall()}
    rows = []
    for m in matches:
        if m.status not in ("confirmed", "rejected") and (
            (m.source_type, m.raw_code, m.raw_name) in decided
        ):
            continue
        if m.sku_id is not None and m.sku_id not in have:
            c = cat.get(m.sku_id)
            repo.ensure_sku(
                conn,
                m.sku_id,
                is_bundle=c.is_bundle if c else False,
                product_id=c.product_id if c else None,
            )
            have.add(m.sku_id)
        rows.append(m.to_row())
    if not rows:
        return 0
    # Ghi theo lô (INSERT … SELECT FROM df): executemany từng dòng chậm trên demo.
    # Kiểu cột ghi rõ vì DuckDB đoán kiểu cột object từ mẫu đầu bảng (cột toàn NULL).
    df = pd.DataFrame(rows)
    for col in df.columns:
        df[col] = df[col].astype("float64" if col == "score" else "string")
    # alias_id là hàm của khóa tự nhiên nên upsert theo UNIQUE cũng giữ nguyên id.
    return repo.upsert_df(conn, "sku_aliases", df, key=_ALIAS_KEY)


def load_known_aliases(
    conn: duckdb.DuckDBPyConnection, source_type: SourceType | None = None
) -> list[AliasMatch]:
    """Alias người dùng đã quyết (``confirmed``/``rejected``) để truyền vào ``resolve``."""
    sql = (
        "SELECT alias_id, source_type, raw_code, raw_name, norm_name, sku_id, score, method, "
        "status, confirmed_by FROM sku_aliases WHERE status IN ('confirmed', 'rejected')"
    )
    params: list[Any] = []
    if source_type is not None:
        sql += " AND source_type = ?"
        params.append(source_type)
    return [AliasMatch.model_validate(r) for r in repo.fetch_all(conn, sql, params)]


def apply_confirmation(
    conn: duckdb.DuckDBPyConnection,
    alias_id: str,
    sku_id: str | None,
    *,
    user: str,
) -> AliasMatch:
    """Người dùng chốt alias: ``sku_id`` có giá trị → ``confirmed`` (method ``user``,
    score 1); ``None`` → ``rejected`` (không phải hàng của shop / bỏ qua)."""
    row = repo.fetch_one(
        conn,
        "SELECT alias_id, source_type, raw_code, raw_name, norm_name, sku_id, score, method, "
        "status, confirmed_by FROM sku_aliases WHERE alias_id = ?",
        [alias_id],
    )
    if row is None:
        raise NotFound(alias_id=alias_id)
    if sku_id is not None:
        if repo.fetch_one(conn, "SELECT 1 AS ok FROM skus WHERE sku_id = ?", [sku_id]) is None:
            raise ValidationFailed(sku_id=sku_id, alias_id=alias_id)
        clash = repo.fetch_one(
            conn,
            "SELECT alias_id FROM sku_aliases WHERE source_type = ? AND sku_id = ? "
            "AND alias_id <> ? AND status IN ('confirmed', 'auto_accepted')",
            [row["source_type"], sku_id, alias_id],
        )
        if clash is not None and row["source_type"] != "orders":
            # 1–1 trong nguồn: alias cũ đang giữ SKU này quay về hàng chờ.
            conn.execute(
                "UPDATE sku_aliases SET status = 'pending' WHERE alias_id = ?",
                [clash["alias_id"]],
            )
    status: AliasStatus = "confirmed" if sku_id is not None else "rejected"
    conn.execute(
        "UPDATE sku_aliases SET sku_id = ?, score = ?, method = 'user', status = ?, "
        "confirmed_by = ? WHERE alias_id = ?",
        [sku_id, 1.0 if sku_id is not None else None, status, user, alias_id],
    )
    row.update(sku_id=sku_id, score=1.0 if sku_id else None, method="user", status=status,
               confirmed_by=user)  # fmt: skip
    return AliasMatch.model_validate(row)
