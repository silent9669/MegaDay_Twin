"""Parameters and goals (API-05 + ECO-02, 08 §3.7; 05 §3–§4; 10 §1).

* ``GET /parameters`` — every catalogue key resolved for the asked scope
  (``session`` > ``sku``/``campaign`` > ``shop`` > catalogue default), with stored values
  and pending AI suggestions.
* ``PUT /parameters/{key}`` — new version; ``editable_by`` checked against ``X-Role``
  (operator keys: operator+, lead keys: lead). Emits ``ParameterChanged``.
* ``POST /parameters/{key}/confirm`` — a person confirms an ``ai_suggested`` value.
* ``GET /goals``, ``GET /goals/{goal_id}``, ``POST /goals`` and ``PUT /goals/{scope}/{scope_id}``
  (lead). ``confirmed_by`` is always the calling user (never the AI). A new goal replaces the
  active one of the same scope (old row kept, ``active = false``, ``version + 1``).
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any, Literal

import duckdb
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.api.common import MAX_LIMIT, paginate
from app.api.deps import Conn, Lead, Operator, User, Viewer, audit, publish
from app.api.schemas import Envelope, LocalDatetime, Meta, Page
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.core.events import GoalChanged, ParameterChanged
from app.core.timeutil import to_local, utcnow
from app.economics.goals import GOAL_LABEL_VI, Goal, GoalKind, create_goal, get_goal
from app.economics.parameters import (
    CATALOGUE,
    ParamDef,
    ParamSet,
    StoredParam,
    can_edit,
    confirm_suggestion,
    get_def,
    latest_effective,
    set_parameter,
)

router = APIRouter(tags=["goals-parameters"])

ParamScope = Literal["shop", "sku", "campaign", "session"]


# ---------------------------------------------------------------------- parameters
class StoredValue(BaseModel):
    scope: str
    scope_id: str
    value: float | list[float] | str | None
    version: int
    set_by: str
    set_at: LocalDatetime
    reason: str | None


class ParamItem(BaseModel):
    key: str
    label: str
    unit: str
    kind: str
    value: float | list[float] | str | None  # effective value for the asked scope
    default: float | list[float] | None
    default_rule: str | None
    is_default: bool
    min: float | None  # None = unbounded or bounded by the SKU cost (see ``min_rule``)
    max: float | None  # None = unbounded or bounded by the SKU price (see ``max_rule``)
    max_rule: str | None
    min_rule: str | None = None
    scope: str  # where ``value`` comes from: default|shop|sku|campaign|session
    scope_id: str
    allowed_scopes: list[str]
    editable_by: str  # lowest role allowed: operator|lead|system
    can_edit: bool  # for the calling user
    source: str  # seller|goal|kịch bản|system
    is_scenario: bool
    version: int  # 0 = catalogue default
    set_by: str
    set_at: LocalDatetime | None
    reason: str | None
    stored: list[StoredValue]  # every effective stored value of the key (all scopes)
    pending_suggestions: list[StoredValue]  # ai_suggested, not effective until confirmed


def _min_role(d: ParamDef) -> str:
    for r in ("operator", "lead", "system"):
        if r in d.editable_by:
            return r
    return "system"


def _num(v: float | None) -> float | None:
    return None if v is None or not math.isfinite(v) else v


def _stored(p: StoredParam) -> StoredValue:
    return StoredValue(
        scope=p.scope,
        scope_id=p.scope_id,
        value=p.parsed,
        version=p.version,
        set_by=p.set_by,
        set_at=p.set_at,
        reason=p.reason,
    )


def sku_price(conn: duckdb.DuckDBPyConnection, sku: str) -> float | None:
    """Selling price bound of ``leftover_value_per_unit`` (05 §3): list price, else the last
    effective price."""
    r = conn.execute("SELECT list_price FROM skus WHERE sku_id = ?", [sku]).fetchone()
    if r is None:
        raise NotFound(f"Không có SKU {sku}.", sku=sku)
    if r[0] is not None:
        return float(r[0])
    e = conn.execute(
        "SELECT eff_price FROM price_events WHERE sku_id = ? ORDER BY day_local DESC LIMIT 1",
        [sku],
    ).fetchone()
    return float(e[0]) if e and e[0] is not None else None


def _unit_cost(conn: duckdb.DuckDBPyConnection, sku: str) -> float | None:
    r = conn.execute(
        "SELECT unit_cost FROM cost_items WHERE sku_id = ? ORDER BY valid_from DESC LIMIT 1",
        [sku],
    ).fetchone()
    return float(r[0]) if r else None


def _pending(conn: duckdb.DuckDBPyConnection) -> dict[str, list[StoredValue]]:
    """``ai_suggested`` rows newer than the latest effective version of the same key/scope."""
    rows = conn.execute(
        "SELECT p.key, p.scope, p.scope_id, p.value, p.value_json, p.version, p.set_by,"
        " p.set_at, p.reason FROM parameters p WHERE p.set_by = 'ai_suggested'"
        " AND p.version > coalesce((SELECT max(q.version) FROM parameters q WHERE q.key = p.key"
        " AND q.scope = p.scope AND q.scope_id = p.scope_id AND q.set_by <> 'ai_suggested'), 0)"
        " ORDER BY p.key, p.version"
    ).fetchall()
    from app.economics.parameters import _row

    out: dict[str, list[StoredValue]] = {}
    for r in rows:
        p = _row(r)
        out.setdefault(p.key, []).append(_stored(p))
    return out


def resolve_items(
    conn: duckdb.DuckDBPyConnection,
    user: User,
    *,
    sku: str | None = None,
    campaign_id: str | None = None,
    session_id: str | None = None,
    keys: list[str] | None = None,
) -> list[ParamItem]:
    stored = latest_effective(conn)
    ps = ParamSet(stored=stored, campaign_id=campaign_id, session_id=session_id)
    pending = _pending(conn)
    price = sku_price(conn, sku) if sku else None
    out: list[ParamItem] = []
    for key, d in CATALOGUE.items():
        if keys and key not in keys:
            continue
        r = ps.resolve(key, sku if "sku" in d.scopes else None)
        value = r.value
        default: float | list[float] | None = (
            list(d.default_list) if d.kind == "list" else d.default
        )
        sp = stored.get((key, r.scope, r.scope_id)) if r.version > 0 else None
        mx = _num(d.max)
        mn = _num(d.min)
        max_rule = None
        min_rule = None
        if key == "leftover_value_per_unit":
            # net of cost (05 §3): −cost (worthless) ≤ V ≤ selling price
            max_rule = "giá bán của SKU"
            min_rule = "−giá vốn của SKU (hàng dư mất trắng)"
            mx = price
            cost = _unit_cost(conn, sku) if sku else None
            mn = -cost if cost is not None else None
        out.append(
            ParamItem(
                key=key,
                label=d.label_vi,
                unit=d.unit,
                kind=d.kind,
                value=value,
                default=default,
                default_rule=d.default_rule,
                is_default=r.version == 0,
                min=mn,
                max=mx,
                max_rule=max_rule,
                min_rule=min_rule,
                scope=r.scope,
                scope_id=r.scope_id,
                allowed_scopes=sorted(d.scopes),
                editable_by=_min_role(d),
                can_edit=can_edit(key, user.role) if user.role != "viewer" else False,
                source=d.source_hint,
                is_scenario=d.is_scenario,
                version=r.version,
                set_by=r.set_by,
                set_at=sp.set_at if sp else None,
                reason=sp.reason if sp else None,
                stored=[_stored(p) for (k, _s, _i), p in sorted(stored.items()) if k == key],
                pending_suggestions=pending.get(key, []),
            )
        )
    return out


def _scope_args(scope: str | None, scope_id: str | None) -> dict[str, str | None]:
    if scope is None or scope == "shop":
        return {}
    if not scope_id or scope_id == "*":
        raise ValidationFailed("Cần scope_id khi scope khác shop.", scope=scope)
    name = {"sku": "sku", "campaign": "campaign_id", "session": "session_id"}.get(scope)
    if name is None:
        raise ValidationFailed(f"scope không hợp lệ: {scope}.", scope=scope)
    return {name: scope_id}


@router.get("/parameters", response_model=Page[ParamItem], summary="Danh mục tham số")
def list_parameters(
    conn: Conn,
    user: Viewer,
    scope: ParamScope | None = None,
    scope_id: str | None = None,
    sku: str | None = None,
    campaign_id: str | None = None,
    session_id: str | None = None,
    key: Annotated[list[str] | None, Query()] = None,
) -> Page[ParamItem]:
    args: dict[str, Any] = {"sku": sku, "campaign_id": campaign_id, "session_id": session_id}
    args.update(_scope_args(scope, scope_id))
    items = resolve_items(conn, user, keys=key, **args)
    return Page[ParamItem](data=items, meta=Meta(computed_at=utcnow(), compute_state="fresh"))


class ParamWrite(BaseModel):
    scope: ParamScope = "shop"
    scope_id: str = "*"
    value: float | list[float] | str
    reason: str | None = Field(default=None, max_length=500)


def _item_for(
    conn: duckdb.DuckDBPyConnection, user: User, key: str, scope: str, scope_id: str
) -> ParamItem:
    args = _scope_args(scope, scope_id)
    return resolve_items(conn, user, keys=[key], **args)[0]


@router.put("/parameters/{key}", response_model=Envelope[ParamItem], summary="Sửa tham số")
def put_parameter(
    key: str, body: ParamWrite, conn: Conn, user: Operator, request: Request
) -> Envelope[ParamItem]:
    d = get_def(key)
    if body.scope != "shop" and body.scope_id == "*":
        raise ValidationFailed("Cần scope_id khi scope khác shop.", scope=body.scope)
    if body.scope == "shop" and body.scope_id != "*":
        raise ValidationFailed("Phạm vi shop dùng scope_id = '*'.", scope_id=body.scope_id)
    upper = lower = None
    if key == "leftover_value_per_unit" and body.scope == "sku":
        upper = sku_price(conn, body.scope_id)
        cost = _unit_cost(conn, body.scope_id)
        lower = -cost if cost is not None else None
    if not can_edit(key, user.role):
        raise Forbidden(
            f"Vai trò '{user.role}' không được sửa '{d.label_vi}' (cần {_min_role(d)}).",
            key=key,
            role=user.role,
            required=_min_role(d),
        )
    before = latest_effective(conn, [key]).get((key, body.scope, body.scope_id))
    p = set_parameter(
        conn,
        key,
        body.value,
        scope=body.scope,
        scope_id=body.scope_id,
        set_by=user.role,
        reason=body.reason,
        upper=upper,
        lower=lower,
    )
    audit(
        conn,
        request,
        user,
        "parameter.update",
        "parameter",
        f"{key}:{body.scope}:{body.scope_id}",
        before=before.model_dump(mode="json") if before else None,
        after=p.model_dump(mode="json"),
    )
    publish(request, ParameterChanged(scope=body.scope, ids=[body.scope_id], version=p.version))
    return Envelope[ParamItem](
        data=_item_for(conn, user, key, body.scope, body.scope_id),
        meta=Meta(computed_at=p.set_at, compute_state="fresh"),
    )


class ParamConfirm(BaseModel):
    scope: ParamScope = "shop"
    scope_id: str = "*"
    version: int = Field(ge=1)
    reason: str | None = None


@router.post(
    "/parameters/{key}/confirm",
    response_model=Envelope[ParamItem],
    summary="Xác nhận giá trị AI gợi ý",
)
def confirm_parameter(
    key: str, body: ParamConfirm, conn: Conn, user: Operator, request: Request
) -> Envelope[ParamItem]:
    d = get_def(key)
    if not can_edit(key, user.role):
        raise Forbidden(
            f"Vai trò '{user.role}' không được xác nhận '{d.label_vi}' (cần {_min_role(d)}).",
            key=key,
            role=user.role,
        )
    p = confirm_suggestion(
        conn,
        key,
        scope=body.scope,
        scope_id=body.scope_id,
        version=body.version,
        reason=body.reason or f"Xác nhận bởi {user.id}",
    )
    audit(
        conn,
        request,
        user,
        "parameter.confirm",
        "parameter",
        f"{key}:{body.scope}:{body.scope_id}",
        after=p.model_dump(mode="json"),
    )
    publish(request, ParameterChanged(scope=body.scope, ids=[body.scope_id], version=p.version))
    return Envelope[ParamItem](
        data=_item_for(conn, user, key, body.scope, body.scope_id),
        meta=Meta(computed_at=p.set_at, compute_state="fresh"),
    )


# ---------------------------------------------------------------------- goals
class GoalOut(BaseModel):
    goal_id: str
    scope: str
    scope_id: str
    kind: str
    label: str  # "Lợi nhuận tới 00:00 12/11"
    deadline: LocalDatetime
    target_units: int | None
    params: dict[str, Any]
    confirmed_by: str
    created_at: LocalDatetime
    active: bool
    version: int


def goal_label(g: Goal) -> str:
    base = GOAL_LABEL_VI.get(g.kind, g.kind).capitalize()
    if g.kind == "sell_target" and g.target_units:
        base += f" {g.target_units} sp"
    return f"{base} tới {to_local(g.deadline):%H:%M %d/%m}"


def goal_out(g: Goal) -> GoalOut:
    return GoalOut(**g.model_dump(), label=goal_label(g))


_GOAL_COLS = (
    "goal_id, scope, scope_id, kind, deadline, target_units, params, confirmed_by,"
    " created_at, active, version"
)


@router.get("/goals", response_model=Page[GoalOut], summary="Mục tiêu")
def list_goals(
    conn: Conn,
    _user: Viewer,
    scope: Literal["campaign", "sku"] | None = None,
    scope_id: str | None = None,
    active: bool | None = True,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    cursor: str | None = None,
) -> Page[GoalOut]:
    from app.economics.goals import _goal

    cond: list[str] = ["TRUE"]
    params: list[Any] = []
    if scope:
        cond.append("scope = ?")
        params.append(scope)
    if scope_id:
        cond.append("scope_id = ?")
        params.append(scope_id)
    if active is not None:
        cond.append("active = ?")
        params.append(active)
    rows = conn.execute(
        f"SELECT {_GOAL_COLS} FROM goals WHERE {' AND '.join(cond)}"
        " ORDER BY scope_id, created_at DESC",
        params,
    ).fetchall()
    goals = [goal_out(_goal(r)) for r in rows]
    page, nxt = paginate(goals, limit, cursor)
    return Page[GoalOut](data=page, meta=Meta(computed_at=utcnow()), next_cursor=nxt)


@router.get("/goals/{goal_id}", response_model=Envelope[GoalOut], summary="Một mục tiêu")
def read_goal(goal_id: str, conn: Conn, _user: Viewer) -> Envelope[GoalOut]:
    return Envelope[GoalOut](
        data=goal_out(get_goal(conn, goal_id)), meta=Meta(computed_at=utcnow())
    )


class GoalBody(BaseModel):
    kind: GoalKind
    deadline: datetime
    target_units: int | None = Field(default=None, ge=1)
    params: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)  # 08 §3.7 shape
    reason: str | None = Field(default=None, max_length=500)


class GoalIn(GoalBody):
    scope: Literal["campaign", "sku"] = "campaign"
    scope_id: str


def _save_goal(
    conn: duckdb.DuckDBPyConnection,
    request: Request,
    user: User,
    scope: Literal["campaign", "sku"],
    scope_id: str,
    body: GoalBody,
) -> Goal:
    table, col = ("campaigns", "campaign_id") if scope == "campaign" else ("skus", "sku_id")
    if conn.execute(f"SELECT 1 FROM {table} WHERE {col} = ?", [scope_id]).fetchone() is None:
        raise NotFound(f"Không có {scope} {scope_id}.", scope=scope, scope_id=scope_id)
    if body.deadline.tzinfo is None:
        raise ValidationFailed("deadline phải có múi giờ (vd +07:00).")
    cons = dict(body.constraints)
    target = body.target_units or cons.pop("target_units", None)
    params = {**cons, **body.params}
    if body.reason:
        params["reason"] = body.reason
    prev = conn.execute(
        "SELECT max(version) FROM goals WHERE scope = ? AND scope_id = ?", [scope, scope_id]
    ).fetchone()
    old_active = conn.execute(
        "SELECT goal_id FROM goals WHERE scope = ? AND scope_id = ? AND active",
        [scope, scope_id],
    ).fetchall()
    g = create_goal(
        conn,
        scope_id=scope_id,
        kind=body.kind,
        deadline=body.deadline,
        confirmed_by=user.id,
        scope=scope,
        target_units=int(target) if target is not None else None,
        params=params,
    )
    version = int(prev[0]) + 1 if prev and prev[0] is not None else 1
    now = utcnow()
    if old_active:
        conn.execute(
            "UPDATE goals SET active = FALSE, valid_to = ? WHERE list_contains(?, goal_id)",
            [now, [str(r[0]) for r in old_active]],
        )
    conn.execute("UPDATE goals SET version = ? WHERE goal_id = ?", [version, g.goal_id])
    g = g.model_copy(update={"version": version})
    audit(
        conn,
        request,
        user,
        "goal.update",
        scope,
        scope_id,
        before={"goal_ids": [str(r[0]) for r in old_active]},
        after=g.model_dump(mode="json"),
    )
    publish(request, GoalChanged(scope=scope, ids=[scope_id], version=version))
    return g


@router.post("/goals", response_model=Envelope[GoalOut], status_code=201, summary="Đặt mục tiêu")
def post_goal(body: GoalIn, conn: Conn, user: Lead, request: Request) -> Envelope[GoalOut]:
    g = _save_goal(conn, request, user, body.scope, body.scope_id, body)
    return Envelope[GoalOut](data=goal_out(g), meta=Meta(computed_at=g.created_at))


@router.put(
    "/goals/{scope}/{scope_id}", response_model=Envelope[GoalOut], summary="Đặt mục tiêu (08)"
)
def put_goal(
    scope: Literal["campaign", "sku"],
    scope_id: str,
    body: GoalBody,
    conn: Conn,
    user: Lead,
    request: Request,
) -> Envelope[GoalOut]:
    g = _save_goal(conn, request, user, scope, scope_id, body)
    return Envelope[GoalOut](data=goal_out(g), meta=Meta(computed_at=g.created_at))
