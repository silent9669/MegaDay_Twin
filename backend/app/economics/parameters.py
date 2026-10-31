"""Parameter catalogue and versioned values (ECO-02, plan 05 §3).

Definitions live in code (:data:`CATALOGUE`); the ``parameters`` table only stores values
that somebody set. Every write creates a new ``version`` of ``(key, scope, scope_id)``, so a
scenario that records ``{key: version}`` stays reproducible.

* ``set_by``: ``system | operator | lead | ai_suggested | ai_suggested_confirmed``. A value
  written with ``ai_suggested`` is stored but **not effective** until a person confirms it
  (:func:`confirm_suggestion` writes a new version with ``ai_suggested_confirmed``).
* Values outside ``[min, max]`` raise :class:`ParamOutOfRange` with a Vietnamese message
  (the API maps it to 400/422).
* Scenario parameters (:data:`SCENARIO_KEYS`) ≠ 0 cap the result label at ``reference``.

Resolution order for one value: ``session`` > ``sku`` / ``campaign`` > ``shop`` > default of
the catalogue (``version = 0``). Per-run overrides (``SimRequest.param_overrides``) win over
everything and are never written.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, Field

from app.core.errors import Forbidden, ParamOutOfRange, ValidationFailed
from app.core.timeutil import utcnow

Scope = Literal["shop", "sku", "campaign", "session"]
SetBy = Literal["system", "operator", "lead", "ai_suggested", "ai_suggested_confirmed"]
ParamKind = Literal["scalar", "list", "timestamp"]

SCOPES: tuple[Scope, ...] = ("shop", "sku", "campaign", "session")
EFFECTIVE_SET_BY: frozenset[str] = frozenset(
    {"system", "operator", "lead", "ai_suggested_confirmed"}
)
_ROLE_RANK = {"operator": 1, "lead": 2, "system": 3}


class ParamDef(BaseModel):
    key: str
    label_vi: str
    unit: str
    default: float | None  # None = rule-based default (see ``default_rule``)
    min: float
    max: float | None  # None = depends on the SKU (e.g. the selling price)
    scopes: set[str]
    editable_by: set[str]
    source_hint: str  # seller | goal | kịch bản | system
    kind: ParamKind = "scalar"
    default_rule: str | None = None
    default_list: list[float] = Field(default_factory=list)
    is_scenario: bool = False  # 05 §3: ≠ 0 → label ≤ reference


def _p(**kw: object) -> ParamDef:
    return ParamDef.model_validate(kw)


INF = math.inf
CATALOGUE: dict[str, ParamDef] = {
    d.key: d
    for d in (
        _p(key="platform_fee_rate", label_vi="Phí sàn", unit="tỷ lệ", default=0.12, min=0.0,
           max=0.3, scopes={"shop"}, editable_by={"lead"}, source_hint="seller"),
        # V = NET value of one leftover unit vs its cost (05 §3/§4): what it brings back after
        # the deadline minus its cost and the cost of selling it later. Same base as m, which
        # already subtracts the cost. 0 = sold later at break-even (regularly restocked
        # items); < 0 = recovered below cost (seasonal / dead stock, ≥ −cost); ≤ selling price.
        _p(key="leftover_value_per_unit",
           label_vi="Giá trị ròng 1 sp còn lại sau hạn, so với giá vốn (V)", unit="đ/sp",
           default=0.0, min=-INF, max=None, scopes={"sku", "session"},
           editable_by={"operator"}, source_hint="seller",
           default_rule="0đ = bán sau vẫn hòa vốn; âm nếu thu hồi dưới giá vốn (≥ −giá vốn)"),
        _p(key="horizon_end", label_vi="Hạn quyết định", unit="thời điểm", default=None,
           default_rule="hết ngày hiện tại", min=0.0, max=None, scopes={"session"},
           editable_by={"operator"}, source_hint="goal", kind="timestamp"),
        _p(key="safety_stock", label_vi="Tồn an toàn không bán", unit="sp", default=0.0,
           min=0.0, max=INF, scopes={"sku"}, editable_by={"operator"}, source_hint="seller"),
        _p(key="stockout_penalty_per_unit",
           label_vi="Phạt mỗi sp thiếu hàng (hủy đơn, uy tín)", unit="đ/sp", default=0.0,
           min=0.0, max=INF, scopes={"shop"}, editable_by={"lead"}, source_hint="kịch bản",
           is_scenario=True),
        _p(key="rank_loss_after_pause",
           label_vi="Đơn tự nhiên mất thêm sau khi pause (thứ hạng)", unit="tỷ lệ/ngày",
           default=0.0, min=0.0, max=0.3, scopes={"campaign"}, editable_by={"lead"},
           source_hint="kịch bản", is_scenario=True),
        _p(key="restock_assumed_hour", label_vi="Giờ hàng về trong ngày dự kiến",
           unit="giờ VN", default=10.0, min=0.0, max=23.0, scopes={"shop"},
           editable_by={"operator"}, source_hint="seller"),
        _p(key="restock_delay_days", label_vi="Hàng về trễ (kịch bản)", unit="ngày",
           default=0.0, min=0.0, max=7.0, scopes={"sku"}, editable_by={"operator"},
           source_hint="kịch bản", is_scenario=True),
        _p(key="reduce_budget_options", label_vi="Mức giảm ngân sách để thử",
           unit="danh sách", default=None, default_list=[0.3, 0.5], min=0.1, max=0.9,
           scopes={"shop"}, editable_by={"operator"}, source_hint="system", kind="list"),
        _p(key="min_marginal_return",
           label_vi="Đóng góp tăng thêm tối thiểu / 1đ chi (mục tiêu efficiency)",
           unit="tỷ lệ", default=1.0, min=0.0, max=5.0, scopes={"campaign"},
           editable_by={"lead"}, source_hint="seller"),
        _p(key="max_loss_vnd", label_vi="Lỗ tối đa chấp nhận khi xả hàng", unit="đ",
           default=0.0, min=0.0, max=INF, scopes={"session"}, editable_by={"lead"},
           source_hint="seller"),
        _p(key="decision_min_prob", label_vi="Xác suất tối thiểu để đề xuất", unit="tỷ lệ",
           default=0.7, min=0.5, max=0.95, scopes={"shop"}, editable_by={"lead"},
           source_hint="system"),
        _p(key="decision_min_gain_vnd", label_vi="Lợi ích tối thiểu để đề xuất", unit="đ",
           default=200_000.0, min=0.0, max=INF, scopes={"shop"}, editable_by={"lead"},
           source_hint="system"),
        _p(key="sell_target_prob", label_vi="Xác suất cần đạt mục tiêu số lượng",
           unit="tỷ lệ", default=0.8, min=0.5, max=0.99, scopes={"session"},
           editable_by={"operator"}, source_hint="system"),
        _p(key="mc_samples", label_vi="Số mẫu Monte Carlo", unit="mẫu", default=1000.0,
           min=200.0, max=5000.0, scopes={"shop"}, editable_by={"system"},
           source_hint="system"),
    )
}  # fmt: skip
SCENARIO_KEYS: frozenset[str] = frozenset(k for k, d in CATALOGUE.items() if d.is_scenario)

ParamValue = float | list[float] | str


def get_def(key: str) -> ParamDef:
    d = CATALOGUE.get(key)
    if d is None:
        raise ParamOutOfRange(f"Không có tham số '{key}' trong danh mục.", key=key)
    return d


def _fmt(x: float) -> str:
    return f"{x:,.10g}".replace(",", "X").replace(".", ",").replace("X", ".")


def validate_value(
    key: str, value: object, *, upper: float | None = None, lower: float | None = None
) -> ParamValue:
    """Check type and range. ``upper`` / ``lower`` bound parameters whose range depends on
    the SKU (``leftover_value_per_unit``: −cost ≤ V ≤ selling price). Returns the normalised
    value."""
    d = get_def(key)
    if d.kind == "timestamp":
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str):
            try:
                datetime.fromisoformat(value)
            except ValueError as e:
                raise ParamOutOfRange(f"'{d.label_vi}' phải là thời điểm ISO 8601.") from e
            return value
        raise ParamOutOfRange(f"'{d.label_vi}' phải là thời điểm ISO 8601.", key=key)
    if d.kind == "list":
        if not isinstance(value, list | tuple) or not value:
            raise ParamOutOfRange(f"'{d.label_vi}' phải là danh sách số.", key=key)
        out = [_check_num(d, v, None) for v in value]
        return sorted(set(out))
    return _check_num(d, value, upper, lower)


def _check_num(
    d: ParamDef, value: object, upper: float | None, lower: float | None = None
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ParamOutOfRange(f"'{d.label_vi}' phải là số.", key=d.key)
    v = float(value)
    hi = d.max if d.max is not None else upper
    lo = d.min if lower is None else max(d.min, lower)
    if not math.isfinite(v) or v < lo or (hi is not None and v > hi):
        lo_f = lo if math.isfinite(lo) else None
        hi_f = hi if hi is not None and math.isfinite(hi) else None
        if lo_f is not None and hi_f is not None:
            rng = f"{_fmt(lo_f)}–{_fmt(hi_f)}"
        elif lo_f is not None:
            rng = f"từ {_fmt(lo_f)} trở lên"
        elif hi_f is not None:
            rng = f"tối đa {_fmt(hi_f)}"
        else:
            rng = "số hữu hạn"
        raise ParamOutOfRange(
            f"'{d.label_vi}' = {_fmt(v)} {d.unit} nằm ngoài khoảng cho phép ({rng}).",
            key=d.key,
            value=v,
            min=lo_f,
            max=hi_f,
        )
    return v


def can_edit(key: str, role: str) -> bool:
    """``editable_by`` holds the lowest role allowed; ``lead`` may edit ``operator`` keys and
    ``system`` may edit everything. ``ai_suggested`` is a proposal, always allowed."""
    if role == "ai_suggested":
        return True
    rank = _ROLE_RANK.get("lead" if role == "ai_suggested_confirmed" else role, 0)
    need = min(_ROLE_RANK.get(r, 3) for r in get_def(key).editable_by)
    return rank >= need


# ---------------------------------------------------------------------- storage
class StoredParam(BaseModel):
    key: str
    scope: str
    scope_id: str
    value: float | None
    value_json: list[float] | str | None
    version: int
    set_by: str
    set_at: datetime
    reason: str | None

    @property
    def effective(self) -> bool:
        return self.set_by in EFFECTIVE_SET_BY

    @property
    def parsed(self) -> ParamValue:
        if self.value_json is not None:
            return self.value_json
        return float(self.value if self.value is not None else 0.0)


def _row(r: Sequence[Any]) -> StoredParam:
    vj = r[4]
    parsed = json.loads(vj) if isinstance(vj, str) else vj
    return StoredParam(
        key=str(r[0]),
        scope=str(r[1]),
        scope_id=str(r[2]),
        value=None if r[3] is None else float(r[3]),
        value_json=parsed,
        version=int(r[5]),
        set_by=str(r[6]),
        set_at=r[7],
        reason=None if r[8] is None else str(r[8]),
    )


_COLS = "key, scope, scope_id, value, value_json, version, set_by, set_at, reason"


def set_parameter(
    conn: duckdb.DuckDBPyConnection,
    key: str,
    value: object,
    *,
    scope: str = "shop",
    scope_id: str = "*",
    set_by: str = "operator",
    reason: str | None = None,
    upper: float | None = None,
    lower: float | None = None,
    at: datetime | None = None,
) -> StoredParam:
    """Write a new version. Raises ``ParamOutOfRange`` / ``ValidationFailed`` /
    ``Forbidden``."""
    d = get_def(key)
    if scope not in d.scopes:
        raise ValidationFailed(
            f"'{d.label_vi}' chỉ đặt được ở phạm vi: {', '.join(sorted(d.scopes))}.",
            key=key,
            scope=scope,
        )
    if set_by not in (*EFFECTIVE_SET_BY, "ai_suggested"):
        raise ValidationFailed(f"set_by không hợp lệ: {set_by}")
    if not can_edit(key, set_by):
        raise Forbidden(f"Vai trò '{set_by}' không được sửa '{d.label_vi}'.", key=key, role=set_by)
    v = validate_value(key, value, upper=upper, lower=lower)
    row = conn.execute(
        "SELECT coalesce(max(version), 0) FROM parameters WHERE key = ? AND scope = ?"
        " AND scope_id = ?",
        [key, scope, scope_id],
    ).fetchone()
    version = int(row[0]) + 1 if row else 1
    scalar = v if isinstance(v, float) else None
    vjson = None if isinstance(v, float) else json.dumps(v)
    set_at = at or utcnow()
    conn.execute(
        f"INSERT INTO parameters ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [key, scope, scope_id, scalar, vjson, version, set_by, set_at, reason],
    )
    return StoredParam(
        key=key,
        scope=scope,
        scope_id=scope_id,
        value=scalar,
        value_json=None if isinstance(v, float) else v,
        version=version,
        set_by=set_by,
        set_at=set_at,
        reason=reason,
    )


def confirm_suggestion(
    conn: duckdb.DuckDBPyConnection,
    key: str,
    *,
    scope: str,
    scope_id: str = "*",
    version: int,
    reason: str | None = None,
) -> StoredParam:
    """A person confirms an ``ai_suggested`` value: new version ``ai_suggested_confirmed``."""
    row = conn.execute(
        f"SELECT {_COLS} FROM parameters WHERE key = ? AND scope = ? AND scope_id = ?"
        " AND version = ?",
        [key, scope, scope_id, version],
    ).fetchone()
    if row is None:
        raise ValidationFailed("Không tìm thấy giá trị AI đề xuất.", key=key, version=version)
    sug = _row(row)
    if sug.set_by != "ai_suggested":
        raise ValidationFailed("Giá trị này không phải đề xuất của AI.", key=key)
    return set_parameter(
        conn,
        key,
        sug.parsed,
        scope=scope,
        scope_id=scope_id,
        set_by="ai_suggested_confirmed",
        reason=reason or sug.reason,
    )


def history(
    conn: duckdb.DuckDBPyConnection, key: str, scope: str | None = None, scope_id: str | None = None
) -> list[StoredParam]:
    sql = f"SELECT {_COLS} FROM parameters WHERE key = ?"
    args: list[object] = [key]
    if scope is not None:
        sql += " AND scope = ?"
        args.append(scope)
    if scope_id is not None:
        sql += " AND scope_id = ?"
        args.append(scope_id)
    return [
        _row(r) for r in conn.execute(sql + " ORDER BY scope, scope_id, version", args).fetchall()
    ]


def latest_effective(
    conn: duckdb.DuckDBPyConnection, keys: Iterable[str] | None = None
) -> dict[tuple[str, str, str], StoredParam]:
    """Latest effective version of every ``(key, scope, scope_id)`` (``ai_suggested``
    rows are skipped)."""
    rows = conn.execute(
        f"SELECT {_COLS} FROM parameters WHERE set_by <> 'ai_suggested'"
        " QUALIFY row_number() OVER (PARTITION BY key, scope, scope_id ORDER BY version DESC) = 1"
    ).fetchall()
    wanted = set(keys) if keys is not None else None
    out: dict[tuple[str, str, str], StoredParam] = {}
    for r in rows:
        p = _row(r)
        if wanted is None or p.key in wanted:
            out[(p.key, p.scope, p.scope_id)] = p
    return out


# ---------------------------------------------------------------------- resolution
@dataclass
class ResolvedParam:
    key: str
    value: ParamValue | None  # None = rule-based default, the caller applies the rule
    version: int  # 0 = catalogue default; -1 = per-run override
    scope: str
    scope_id: str
    set_by: str


@dataclass
class ParamSet:
    """Parameters resolved for one simulation. ``get(key, sku=…)`` returns the value for a
    SKU when the key has a ``sku`` scope; ``versions`` is what ``scenarios.param_version``
    records."""

    stored: dict[tuple[str, str, str], StoredParam]
    campaign_id: str | None = None
    session_id: str | None = None
    overrides: dict[str, ParamValue] = field(default_factory=dict)
    used: dict[str, int] = field(default_factory=dict)

    def resolve(self, key: str, sku: str | None = None) -> ResolvedParam:
        d = get_def(key)
        okey = f"{key}@{sku}" if sku is not None else None
        if okey is not None and okey in self.overrides:
            return self._mark(
                ResolvedParam(key, self.overrides[okey], -1, "override", sku or "*", "user")
            )
        if key in self.overrides:
            return self._mark(ResolvedParam(key, self.overrides[key], -1, "override", "*", "user"))
        chain: list[tuple[str, str]] = []
        if self.session_id:
            chain.append(("session", self.session_id))
        if sku is not None:
            chain.append(("sku", sku))
        if self.campaign_id is not None:
            chain.append(("campaign", self.campaign_id))
        chain.append(("shop", "*"))
        for scope, sid in chain:
            if scope not in d.scopes:
                continue
            p = self.stored.get((key, scope, sid))
            if p is not None:
                return self._mark(ResolvedParam(key, p.parsed, p.version, scope, sid, p.set_by))
        default: ParamValue | None = list(d.default_list) if d.kind == "list" else d.default
        return self._mark(ResolvedParam(key, default, 0, "default", "*", "system"))

    def _mark(self, r: ResolvedParam) -> ResolvedParam:
        name = (
            r.key
            if r.scope_id in ("*", self.campaign_id, self.session_id)
            else (f"{r.key}@{r.scope_id}")
        )
        self.used[name] = r.version
        return r

    def num(self, key: str, sku: str | None = None, default: float | None = None) -> float:
        v = self.resolve(key, sku).value
        if v is None:
            if default is None:
                raise ValueError(f"parameter {key} needs a rule-based default")
            return float(default)
        if isinstance(v, list | str):
            raise TypeError(f"parameter {key} is not scalar")
        return float(v)

    def num_list(self, key: str) -> list[float]:
        v = self.resolve(key).value
        if isinstance(v, list):
            return [float(x) for x in v]
        raise TypeError(f"parameter {key} is not a list")

    def scenario_active(self) -> list[str]:
        """Scenario keys that are ≠ 0 somewhere in this run (overrides or stored)."""
        out = set()
        for k, v in self.overrides.items():
            base = k.split("@", 1)[0]
            if base in SCENARIO_KEYS and isinstance(v, float | int) and v != 0:
                out.add(base)
        for (k, _s, _sid), p in self.stored.items():
            if k in SCENARIO_KEYS and p.value not in (None, 0.0):
                out.add(k)
        return sorted(out)

    @property
    def versions(self) -> dict[str, int]:
        return dict(sorted(self.used.items()))


def parse_overrides(
    overrides: Mapping[str, object], *, upper: Mapping[str, float] | None = None
) -> dict[str, ParamValue]:
    """Validate ``SimRequest.param_overrides``: key ``name`` or ``name@SKU``. Unknown keys
    or values out of range raise ``ParamOutOfRange``."""
    out: dict[str, ParamValue] = {}
    for raw, v in overrides.items():
        key, _, sku = raw.partition("@")
        up = (upper or {}).get(sku) if sku else None
        if key == "restock_delay_days":
            # what-if "hàng về sớm d ngày": negative values allowed in a run (05 §10)
            if isinstance(v, bool) or not isinstance(v, int | float) or not -7 <= v <= 7:
                raise ParamOutOfRange(
                    "'Hàng về trễ (kịch bản)' phải trong khoảng −7–7 ngày.", key=key
                )
            out[raw] = float(v)
            continue
        out[raw] = validate_value(key, v, upper=up)
    return out


def load_param_set(
    conn: duckdb.DuckDBPyConnection,
    *,
    campaign_id: str | None = None,
    session_id: str | None = None,
    overrides: Mapping[str, object] | None = None,
) -> ParamSet:
    return ParamSet(
        stored=latest_effective(conn),
        campaign_id=campaign_id,
        session_id=session_id,
        overrides=parse_overrides(overrides or {}),
    )
