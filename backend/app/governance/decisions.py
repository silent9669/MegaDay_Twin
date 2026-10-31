"""Decision state machine, snapshot and simulated actions (GOV-01, GOV-04 floor; 10 §2–3).

States and transitions (10 §2)::

    (create) → draft ─submit→ pending_approval ─approve→ approved ─send→ sent ─verify→ verified
                 │                 │  └reject→ rejected        └expire→ expired   └fail→ failed
                 └cancel→ cancelled ←cancel┘  (reopen: pending_approval → draft)

:data:`TRANSITIONS` declares ``event → (allowed from-states, to-state, permission)``. An event
from any other state raises ``StateConflict`` (HTTP 409); rank/ownership go through
:func:`app.governance.roles.check` (lead never approves their own decision → 403).

Snapshot (stored at creation so the decision can be explained and replayed, 10 §3–4):
``recommendation_id`` + ``recommended_action``, ``scenario_id``, ``input_hash``,
``data_as_of`` (scenario mark + every source's latest ``as_of``), ``goal_snapshot`` (goal row
+ version), ``parameters_snapshot`` (``{name: {version, value, scope}}``), ``model_versions``
(forecast ids → model, iROAS estimate), ``expected_outcome`` (the chosen action's simulated
result, p10/p50/p90), ``open_issues`` (data issues open on the campaign SKUs).

Freshness gate on submit and approve (10 §1–2): an open ``error`` data issue on the campaign
SKUs → 422 ``BLOCKING_DATA_ISSUE``; newer orders/inventory data than the scenario, a changed
parameter version or a replaced goal → 409 ``STALE_INPUT`` (run the simulation again).

Actions are **simulated only** (``mode='simulated'``). Verification floor (GOV-04): a person
marks the action "đã kiểm lại" (``verified_by_human = true``) or failed with a reason; an
observed status / budget, when given, is compared with the action.

Every function writes its ``audit_log`` row (``decision.create``, ``decision.submit``…) with
the actor's ``via`` (ui | ai | system). Publishing ``DecisionRecorded`` is the caller's job.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Literal

import duckdb
from pydantic import BaseModel

from app.core.errors import (
    ConstraintViolation,
    DataQualityError,
    Forbidden,
    NotFound,
    StaleDataError,
    StateConflict,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.timeutil import utcnow
from app.governance import roles
from app.governance.audit import write_audit
from app.governance.roles import Actor
from app.simulator.schemas import ActionSpec

DecisionState = Literal[
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "cancelled",
    "expired",
    "sent",
    "verified",
    "failed",
]
DecisionKind = Literal["ad_action", "goal_change", "handoff_note"]
Event = Literal[
    "submit", "reopen", "approve", "reject", "cancel", "expire", "send", "verify", "fail"
]

TRANSITIONS: dict[str, tuple[frozenset[str], str, str]] = {
    "submit": (frozenset({"draft"}), "pending_approval", "decision.submit"),
    "reopen": (frozenset({"pending_approval"}), "draft", "decision.reopen"),
    "approve": (frozenset({"pending_approval"}), "approved", "decision.approve"),
    "reject": (frozenset({"pending_approval"}), "rejected", "decision.reject"),
    "cancel": (frozenset({"draft", "pending_approval"}), "cancelled", "decision.cancel"),
    "expire": (frozenset({"approved"}), "expired", "decision.approve"),  # system only
    "send": (frozenset({"approved"}), "sent", "action.send"),
    "verify": (frozenset({"sent"}), "verified", "action.verify"),
    "fail": (frozenset({"sent"}), "failed", "action.verify"),
}
STATE_LABEL_VI: dict[str, str] = {
    "draft": "Bản nháp",
    "pending_approval": "Chờ duyệt",
    "approved": "Đã duyệt",
    "rejected": "Bị từ chối",
    "cancelled": "Đã hủy",
    "expired": "Hết hiệu lực",
    "sent": "Đã gửi (mô phỏng)",
    "verified": "Đã kiểm lại",
    "failed": "Không khớp / lỗi",
}
DECISION_VALIDITY_HOURS = 2  # 10 §2 default of `decision_validity_hours`
ACTION_MODE = "simulated"
SIMULATED_LABEL_VI = "Thao tác mô phỏng"
PAUSED_STATUSES = {"tạm dừng", "paused", "pause", "dừng", "đã dừng"}


class Decision(BaseModel):
    id: str
    kind: str
    target_kind: str | None = None
    target_id: str | None = None
    action: dict[str, Any] | None = None
    action_label: str | None = None
    state: str
    state_label: str | None = None
    rationale: str | None = None
    override_of_recommendation: bool | None = None
    recommendation_id: str | None = None
    recommended_action: dict[str, Any] | None = None
    scenario_id: str | None = None
    goal_snapshot: Any = None
    parameters_snapshot: Any = None
    model_versions: Any = None
    data_as_of: Any = None
    input_hash: str | None = None
    expected_outcome: Any = None
    open_issues: Any = None
    created_by: str | None = None
    created_via: str | None = None
    created_at: datetime | None = None
    submitted_at: datetime | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    approval_note: str | None = None
    reject_reason: str | None = None
    valid_until: datetime | None = None
    observed_outcome: Any = None
    evaluation: Any = None


class ActionRecord(BaseModel):
    id: str
    decision_id: str
    mode: str
    mode_label: str = SIMULATED_LABEL_VI
    payload: Any = None
    sent_by: str | None = None
    sent_at: datetime | None = None
    state: str
    verified_at: datetime | None = None
    verified_by: str | None = None
    verified_by_human: bool | None = None
    observed: Any = None
    failure_reason: str | None = None
    note: str | None = None


_DCOLS = [
    "id", "kind", "target_kind", "target_id", "action", "state", "rationale",
    "override_of_recommendation", "recommendation_id", "recommended_action", "scenario_id",
    "goal_snapshot", "parameters_snapshot", "model_versions", "data_as_of", "input_hash",
    "expected_outcome", "open_issues", "created_by", "created_via", "created_at",
    "submitted_at", "approved_by", "approved_at", "approval_note", "reject_reason",
    "valid_until", "observed_outcome", "evaluation",
]  # fmt: skip
_JSON_COLS = {
    "action", "recommended_action", "goal_snapshot", "parameters_snapshot", "model_versions",
    "data_as_of", "expected_outcome", "open_issues", "observed_outcome", "evaluation",
}  # fmt: skip
_ACOLS = [
    "id", "decision_id", "mode", "payload", "sent_by", "sent_at", "state", "verified_at",
    "verified_by", "verified_by_human", "observed", "failure_reason", "note",
]  # fmt: skip


def _load(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _dump(v: Any) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False, default=str)


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def _action_label(action: dict[str, Any] | None) -> str | None:
    if not action:
        return None
    try:
        return ActionSpec.model_validate(action).label_vi()
    except ValueError:
        return None


def _decision(r: tuple[Any, ...]) -> Decision:
    d = dict(zip(_DCOLS, r, strict=True))
    for k in _JSON_COLS:
        d[k] = _load(d[k])
    d["action_label"] = _action_label(d["action"])
    d["state_label"] = STATE_LABEL_VI.get(d["state"], d["state"])
    return Decision(**d)


def _action(r: tuple[Any, ...]) -> ActionRecord:
    d = dict(zip(_ACOLS, r, strict=True))
    d["payload"] = _load(d["payload"])
    d["observed"] = _load(d["observed"])
    return ActionRecord(**d)


# ---------------------------------------------------------------------- read
def get_decision(conn: duckdb.DuckDBPyConnection, decision_id: str) -> Decision:
    row = conn.execute(
        f"SELECT {', '.join(_DCOLS)} FROM decisions WHERE id = ?", [decision_id]
    ).fetchone()
    if row is None:
        raise NotFound("Không có quyết định này.", decision_id=decision_id)
    return _decision(row)


def list_decisions(
    conn: duckdb.DuckDBPyConnection,
    *,
    state: str | list[str] | None = None,
    target_id: str | None = None,
    created_by: str | None = None,
    kind: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Decision]:
    """Newest first. ``since`` inclusive, ``until`` exclusive (on ``created_at``)."""
    where: list[str] = []
    args: list[Any] = []
    if state is not None:
        states = [state] if isinstance(state, str) else list(state)
        where.append(f"state IN ({', '.join('?' for _ in states)})")
        args += states
    for col, val in (("target_id", target_id), ("created_by", created_by), ("kind", kind)):
        if val is not None:
            where.append(f"{col} = ?")
            args.append(val)
    if since is not None:
        where.append("created_at >= ?")
        args.append(since)
    if until is not None:
        where.append("created_at < ?")
        args.append(until)
    sql = f"SELECT {', '.join(_DCOLS)} FROM decisions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC"
    return [_decision(r) for r in conn.execute(sql, args).fetchall()]


def get_action(conn: duckdb.DuckDBPyConnection, action_id: str) -> ActionRecord:
    row = conn.execute(
        f"SELECT {', '.join(_ACOLS)} FROM actions WHERE id = ?", [action_id]
    ).fetchone()
    if row is None:
        raise NotFound("Không có thao tác này.", action_id=action_id)
    return _action(row)


def actions_of(conn: duckdb.DuckDBPyConnection, decision_id: str) -> list[ActionRecord]:
    rows = conn.execute(
        f"SELECT {', '.join(_ACOLS)} FROM actions WHERE decision_id = ? ORDER BY sent_at, id",
        [decision_id],
    ).fetchall()
    return [_action(r) for r in rows]


def list_actions(
    conn: duckdb.DuckDBPyConnection, *, state: str | None = None
) -> list[ActionRecord]:
    sql = f"SELECT {', '.join(_ACOLS)} FROM actions"
    args: list[Any] = []
    if state is not None:
        sql += " WHERE state = ?"
        args.append(state)
    sql += " ORDER BY sent_at DESC, id DESC"
    return [_action(r) for r in conn.execute(sql, args).fetchall()]


def expire_overdue(conn: duckdb.DuckDBPyConnection, *, now: datetime | None = None) -> list[str]:
    """``approved`` past ``valid_until`` → ``expired`` (system). Returns the expired ids."""
    ts = now or utcnow()
    ids = [
        str(r[0])
        for r in conn.execute(
            "UPDATE decisions SET state = 'expired' WHERE state = 'approved'"
            " AND valid_until IS NOT NULL AND valid_until <= ? RETURNING id",
            [ts],
        ).fetchall()
    ]
    for did in ids:
        write_audit(
            conn, roles.SYSTEM, "decision.expire", "decision", did, after={"state": "expired"}
        )
    return ids


# ---------------------------------------------------------------------- snapshot helpers
def _campaign_skus(conn: duckdb.DuckDBPyConnection, campaign_id: str) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT sku_id FROM campaign_skus WHERE campaign_id = ? ORDER BY sku_id", [campaign_id]
        ).fetchall()
    ]


def open_issues(
    conn: duckdb.DuckDBPyConnection, campaign_id: str | None, *, blocking_only: bool = False
) -> list[dict[str, Any]]:
    """Open data issues on the campaign SKUs (or the campaign itself)."""
    if not campaign_id:
        return []
    ids = [*_campaign_skus(conn, campaign_id), campaign_id]
    sev = "('error')" if blocking_only else "('error', 'warn')"
    rows = conn.execute(
        "SELECT issue_id, code, severity, entity_type, entity_id FROM data_quality_issues"
        f" WHERE status = 'open' AND severity IN {sev}"
        f" AND entity_id IN ({', '.join('?' for _ in ids)})"
        " ORDER BY CASE severity WHEN 'error' THEN 0 ELSE 1 END, created_at DESC LIMIT 50",
        ids,
    ).fetchall()
    return [
        {"issue_id": r[0], "code": r[1], "severity": r[2], "entity_type": r[3], "entity_id": r[4]}
        for r in rows
    ]


def _current_data_as_of(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT source_type, max(as_of) FROM ingest_batches WHERE status <> 'failed' GROUP BY 1"
    ).fetchall()
    return {str(r[0]): r[1] for r in rows}


def _orders_inventory_mark(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    row = conn.execute(
        "SELECT max(as_of) FROM ingest_batches WHERE source_type IN ('orders', 'inventory')"
        " AND status <> 'failed'"
    ).fetchone()
    v = row[0] if row else None
    return v if isinstance(v, datetime) else None


def _param_snapshot(
    conn: duckdb.DuckDBPyConnection, campaign_id: str, versions: dict[str, int]
) -> dict[str, Any]:
    from app.economics.parameters import load_param_set

    ps = load_param_set(conn, campaign_id=campaign_id)
    out: dict[str, Any] = {}
    for name, ver in sorted(versions.items()):
        key, _, sku = name.partition("@")
        try:
            r = ps.resolve(key, sku or None)
        except KeyError:
            out[name] = {"version": ver}
            continue
        item: dict[str, Any] = {"version": ver, "value": r.value, "scope": r.scope}
        if r.version != ver:
            item["current_version"] = r.version
        out[name] = item
    return out


def _changed_params(
    conn: duckdb.DuckDBPyConnection, campaign_id: str, versions: dict[str, int]
) -> list[str]:
    from app.economics.parameters import load_param_set

    ps = load_param_set(conn, campaign_id=campaign_id)
    changed = []
    for name, ver in versions.items():
        if ver < 0:  # per-run override
            continue
        key, _, sku = name.partition("@")
        try:
            if ps.resolve(key, sku or None).version != ver:
                changed.append(name)
        except KeyError:
            continue
    return sorted(changed)


def _goal_snapshot(conn: duckdb.DuckDBPyConnection, goal_id: str | None) -> dict[str, Any] | None:
    if not goal_id:
        return None
    cols = [
        "goal_id", "scope", "scope_id", "kind", "deadline", "target_units", "params",
        "confirmed_by", "created_at", "active", "version", "valid_from", "valid_to",
    ]  # fmt: skip
    row = conn.execute(
        f"SELECT {', '.join(cols)} FROM goals WHERE goal_id = ?", [goal_id]
    ).fetchone()
    if row is None:
        return {"goal_id": goal_id}
    d = dict(zip(cols, row, strict=True))
    d["params"] = _load(d["params"])
    return {k: _iso(v) for k, v in d.items()}


def _model_versions(conn: duckdb.DuckDBPyConnection, ref: dict[str, Any]) -> dict[str, Any]:
    fids = [str(f) for f in ref.get("forecast_ids") or []]
    models: dict[str, str] = {}
    if fids:
        rows = conn.execute(
            "SELECT forecast_id, sku_id, model_id FROM forecasts"
            f" WHERE forecast_id IN ({', '.join('?' for _ in fids)})",
            fids,
        ).fetchall()
        models = {str(r[0]): f"{r[1]}:{r[2]}" for r in rows}
    est_model = None
    est = ref.get("estimate_id")
    if est:
        row = conn.execute(
            "SELECT model_id FROM iroas_estimates WHERE estimate_id = ?", [est]
        ).fetchone()
        est_model = row[0] if row else None
    return {
        "forecast_ids": fids,
        "forecast_models": models,
        "iroas_estimate_id": est,
        "iroas_model": est_model,
        "iroas_source": ref.get("iroas_source"),
        "panel_version": ref.get("panel_version"),
    }


_OUTCOME_KEYS = (
    "objective", "contribution", "spend", "units_sold", "leftover_units", "leftover_value",
)  # fmt: skip


def _expected_outcome(r: dict[str, Any], scn: dict[str, Any]) -> dict[str, Any]:
    out = {k: _load(r[k]) for k in _OUTCOME_KEYS}
    out["stockout_hour_p50"] = _iso(r["stockout_hour_p50"])
    out["feasible"] = bool(r["feasible"])
    out["violations"] = list(r["violations"] or [])
    out["samples"] = scn["samples"]
    out["seed"] = scn["seed"]
    out["start_at"] = _iso(scn["start_at"])
    out["horizon_hours"] = scn["horizon_hours"]
    return out


def _fetch_one(conn: duckdb.DuckDBPyConnection, sql: str, args: list[Any]) -> dict[str, Any] | None:
    cur = conn.execute(sql, args)
    row = cur.fetchone()
    if row is None:
        return None
    names = [d[0] for d in cur.description or []]
    return dict(zip(names, row, strict=True))


def _to_action(action: ActionSpec | dict[str, Any] | None) -> ActionSpec | None:
    if action is None or isinstance(action, ActionSpec):
        return action
    try:
        return ActionSpec.model_validate(action)
    except ValueError as e:
        raise ValidationFailed(f"Hành động không hợp lệ: {e}") from e


# ---------------------------------------------------------------------- create
def create_decision(
    conn: duckdb.DuckDBPyConnection,
    actor: Actor,
    *,
    campaign_id: str | None = None,
    scenario_id: str | None = None,
    action: ActionSpec | dict[str, Any] | None = None,
    recommendation_id: str | None = None,
    rationale: str | None = None,
    kind: str = "ad_action",
    shop_id: str = "shop-demo",
) -> Decision:
    """New decision in state ``draft`` with its full snapshot.

    ``kind='ad_action'`` (default) needs ``scenario_id`` (a stored scenario without
    ``overrides``) and either ``action`` or ``recommendation_id`` (→ its best action). The
    action must be one of the scenario's results and feasible (else 409
    ``CONSTRAINT_VIOLATION``). ``override_of_recommendation`` is computed (chosen ≠ best).
    ``kind='handoff_note'`` needs only ``rationale`` (the note), no action.
    ``actor.via='ai'`` stores ``created_via='ai'``. Raises ``Forbidden`` below operator.
    """
    roles.check(actor, "decision.create")
    now = utcnow()
    did = new_id("dec")
    if kind == "handoff_note":
        if not (rationale and rationale.strip()):
            raise ValidationFailed("Ghi chú bàn giao cần nội dung.")
        conn.execute(
            "INSERT INTO decisions (id, kind, target_kind, target_id, action, state, rationale,"
            " data_as_of, open_issues, created_by, created_via, created_at)"
            " VALUES (?, 'handoff_note', ?, ?, NULL, 'draft', ?, ?, ?, ?, ?, ?)",
            [
                did,
                "campaign" if campaign_id else None,
                campaign_id,
                rationale.strip()[:4000],
                _dump({k: _iso(v) for k, v in _current_data_as_of(conn).items()}),
                _dump(open_issues(conn, campaign_id)),
                actor.id,
                actor.via,
                now,
            ],
        )
        d = get_decision(conn, did)
        write_audit(
            conn, actor, "decision.create", "decision", did, after=_brief(d), shop_id=shop_id
        )
        return d
    if kind != "ad_action":
        raise ValidationFailed(f"Loại quyết định chưa hỗ trợ: {kind}.", kind=kind)
    if not scenario_id and recommendation_id:  # "tạo quyết định từ đề xuất"
        row = conn.execute(
            "SELECT scenario_id FROM recommendations WHERE recommendation_id = ?",
            [recommendation_id],
        ).fetchone()
        if row is None:
            raise NotFound("Không có đề xuất này.", recommendation_id=recommendation_id)
        scenario_id = str(row[0])
    if not scenario_id:
        raise ValidationFailed("Cần scenario_id (quyết định phải dựa trên một kịch bản đã lưu).")
    scn = _fetch_one(
        conn,
        "SELECT scenario_id, campaign_id, goal_id, start_at, horizon_hours, param_version,"
        " overrides, inputs_ref, samples, seed, input_hash FROM scenarios WHERE scenario_id = ?",
        [scenario_id],
    )
    if scn is None:
        raise NotFound("Không có kịch bản này.", scenario_id=scenario_id)
    if campaign_id is not None and scn["campaign_id"] != campaign_id:
        raise ValidationFailed(
            "Kịch bản không thuộc chiến dịch này.",
            scenario_campaign=scn["campaign_id"],
            campaign_id=campaign_id,
        )
    campaign_id = str(scn["campaign_id"])
    if _load(scn["overrides"]) or {}:
        raise ValidationFailed(
            "Kịch bản có giả định (tham số tạm) nên không lưu thành quyết định;"
            " chạy lại mô phỏng không giả định hoặc dùng ghi chú cho lead.",
            scenario_id=scenario_id,
        )
    rec = None
    if recommendation_id:
        rec = _fetch_one(
            conn,
            "SELECT recommendation_id, scenario_id, campaign_id, best_action FROM"
            " recommendations WHERE recommendation_id = ?",
            [recommendation_id],
        )
        if rec is None:
            raise NotFound("Không có đề xuất này.", recommendation_id=recommendation_id)
        if rec["scenario_id"] != scenario_id:
            raise ValidationFailed(
                "Đề xuất không thuộc kịch bản này.",
                recommendation_scenario=rec["scenario_id"],
                scenario_id=scenario_id,
            )
    else:
        rec = _fetch_one(
            conn,
            "SELECT recommendation_id, scenario_id, campaign_id, best_action FROM"
            " recommendations WHERE scenario_id = ? ORDER BY created_at DESC LIMIT 1",
            [scenario_id],
        )
    best_raw = _load(rec["best_action"]) if rec else None
    best = _to_action(best_raw) if best_raw else None
    chosen = _to_action(action)
    if chosen is None:
        if best is None:
            raise ValidationFailed("Cần chọn hành động (đề xuất không có hành động tốt nhất).")
        chosen = best
    match = None
    cur = conn.execute(
        "SELECT action, feasible, violations, objective, contribution, spend, units_sold,"
        " leftover_units, leftover_value, stockout_hour_p50 FROM scenario_results"
        " WHERE scenario_id = ?",
        [scenario_id],
    )
    names = [d[0] for d in cur.description or []]
    for row in cur.fetchall():
        r = dict(zip(names, row, strict=True))
        if ActionSpec.model_validate(_load(r["action"])).key == chosen.key:
            match = r
            break
    if match is None:
        raise ValidationFailed(
            f"Hành động '{chosen.label_vi()}' không có trong kịch bản.", action=chosen.key
        )
    if not match["feasible"]:
        viol = list(match["violations"] or [])
        raise ConstraintViolation(
            f"Hành động '{chosen.label_vi()}' vi phạm ràng buộc: {', '.join(viol)}.",
            violations=viol,
        )
    ref = _load(scn["inputs_ref"]) or {}
    versions = {str(k): int(v) for k, v in (_load(scn["param_version"]) or {}).items()}
    sources = {k: _iso(v) for k, v in _current_data_as_of(conn).items()}
    data_marks = {"scenario": ref.get("data_as_of"), **sources}
    override = best is not None and best.key != chosen.key
    conn.execute(
        "INSERT INTO decisions (id, kind, target_kind, target_id, action, state, rationale,"
        " override_of_recommendation, recommendation_id, recommended_action, scenario_id,"
        " goal_snapshot, parameters_snapshot, model_versions, data_as_of, input_hash,"
        " expected_outcome, open_issues, created_by, created_via, created_at) VALUES"
        " (?, 'ad_action', 'campaign', ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            did,
            campaign_id,
            _dump(chosen.model_dump(mode="json")),
            (rationale or "").strip()[:4000] or None,
            override,
            rec["recommendation_id"] if rec else None,
            _dump(best.model_dump(mode="json")) if best else None,
            scenario_id,
            _dump(_goal_snapshot(conn, scn["goal_id"])),
            _dump(_param_snapshot(conn, campaign_id, versions)),
            _dump(_model_versions(conn, ref)),
            _dump(data_marks),
            scn["input_hash"],
            _dump(_expected_outcome(match, scn)),
            _dump(open_issues(conn, campaign_id)),
            actor.id,
            actor.via,
            now,
        ],
    )
    d = get_decision(conn, did)
    write_audit(conn, actor, "decision.create", "decision", did, after=_brief(d), shop_id=shop_id)
    return d


def _brief(d: Decision) -> dict[str, Any]:
    return {
        "state": d.state,
        "kind": d.kind,
        "target_id": d.target_id,
        "action": d.action,
        "scenario_id": d.scenario_id,
        "recommendation_id": d.recommendation_id,
        "override_of_recommendation": d.override_of_recommendation,
    }


# ---------------------------------------------------------------------- gates
def check_inputs(conn: duckdb.DuckDBPyConnection, d: Decision) -> None:
    """Freshness gate for submit / approve (10 §1–2). No-op for handoff notes."""
    if d.kind != "ad_action" or not d.scenario_id:
        return
    blocking = open_issues(conn, d.target_id, blocking_only=True)
    if blocking:
        raise DataQualityError(
            "Còn vấn đề dữ liệu chặn (blocking) trên chiến dịch này; xử lý xong rồi tính lại.",
            issues=[i["issue_id"] for i in blocking],
            codes=sorted({i["code"] for i in blocking}),
        )
    scn = _fetch_one(
        conn,
        "SELECT campaign_id, goal_id, param_version, inputs_ref, input_hash FROM scenarios"
        " WHERE scenario_id = ?",
        [d.scenario_id],
    )
    if scn is None:
        raise StaleDataError("Kịch bản của quyết định không còn.", scenario_id=d.scenario_id)
    ref = _load(scn["inputs_ref"]) or {}
    scn_mark = ref.get("data_as_of")
    cur_mark = _orders_inventory_mark(conn)
    if scn_mark and cur_mark is not None:
        sm = datetime.fromisoformat(str(scn_mark).replace("Z", "+00:00"))
        if sm < cur_mark:
            raise StaleDataError(
                "Dữ liệu đã cập nhật sau khi tính kịch bản; hãy chạy lại mô phỏng rồi tạo"
                " quyết định mới.",
                scenario_data_as_of=sm.isoformat(),
                current_data_as_of=cur_mark.isoformat(),
                input_hash=d.input_hash,
            )
    versions = {str(k): int(v) for k, v in (_load(scn["param_version"]) or {}).items()}
    changed = _changed_params(conn, str(scn["campaign_id"]), versions)
    if changed:
        raise StaleDataError(
            "Tham số đã đổi sau khi tính kịch bản; hãy chạy lại mô phỏng.", parameters=changed
        )
    goal = conn.execute(
        "SELECT active, valid_to FROM goals WHERE goal_id = ?", [scn["goal_id"]]
    ).fetchone()
    if goal is not None and (not goal[0] or goal[1] is not None):
        raise StaleDataError(
            "Mục tiêu đã đổi sau khi tính kịch bản; hãy chạy lại mô phỏng.",
            goal_id=scn["goal_id"],
        )


# ---------------------------------------------------------------------- transitions
def transition(
    conn: duckdb.DuckDBPyConnection,
    decision_id: str,
    event: str,
    actor: Actor,
    *,
    note: str | None = None,
    reason: str | None = None,
    now: datetime | None = None,
    shop_id: str = "shop-demo",
) -> Decision:
    """Apply ``event`` (submit | reopen | approve | reject | cancel | expire) to a decision.

    ``send`` / ``verify`` / ``fail`` go through :func:`send_action` / :func:`verify_action`
    (they also write ``actions``). Invalid state → ``StateConflict`` (409); role/ownership →
    ``Forbidden`` (403); stale inputs → ``StaleDataError`` (409 ``STALE_INPUT``)."""
    if event not in TRANSITIONS:
        raise ValidationFailed(f"Sự kiện không hợp lệ: {event}.", event=event)
    if event in ("send", "verify", "fail"):
        raise ValidationFailed(f"Dùng send_action/verify_action cho sự kiện {event}.")
    ts = now or utcnow()
    if event != "expire":
        expire_overdue(conn, now=ts)
    d = get_decision(conn, decision_id)
    froms, to, perm = TRANSITIONS[event]
    if event == "expire":
        if actor.via != "system":
            raise ValidationFailed("Chỉ hệ thống mới đánh dấu hết hiệu lực.")
    else:
        roles.check(actor, perm, owner_id=d.created_by)
    if d.state not in froms:
        raise StateConflict(
            f"Quyết định đang ở trạng thái '{STATE_LABEL_VI.get(d.state, d.state)}',"
            f" không thể {event}.",
            state=d.state,
            event=event,
            allowed_from=sorted(froms),
        )
    sets = ["state = ?"]
    args: list[Any] = [to]
    if event == "submit":
        check_inputs(conn, d)
        sets.append("submitted_at = ?")
        args.append(ts)
    elif event == "approve":
        check_inputs(conn, d)
        sets += ["approved_by = ?", "approved_at = ?", "approval_note = ?", "valid_until = ?"]
        args += [
            actor.id,
            ts,
            (note or "").strip()[:2000] or None,
            ts + timedelta(hours=DECISION_VALIDITY_HOURS),
        ]
    elif event == "reject":
        if not (reason and reason.strip()):
            raise ValidationFailed("Từ chối cần ghi lý do.", field="reason")
        sets += ["reject_reason = ?", "approved_by = ?", "approved_at = ?"]
        args += [reason.strip()[:2000], actor.id, ts]
    elif event == "reopen":
        sets.append("submitted_at = NULL")
    row = conn.execute(
        f"UPDATE decisions SET {', '.join(sets)} WHERE id = ? AND state = ? RETURNING id",
        [*args, decision_id, d.state],
    ).fetchone()
    if row is None:  # raced with another transition
        raise StateConflict("Quyết định vừa được người khác thay đổi; tải lại.", event=event)
    out = get_decision(conn, decision_id)
    write_audit(
        conn,
        actor,
        f"decision.{event}",
        "decision",
        decision_id,
        before={"state": d.state},
        after={
            "state": out.state,
            **({"note": out.approval_note} if out.approval_note and event == "approve" else {}),
            **({"reason": out.reject_reason} if event == "reject" else {}),
        },
        shop_id=shop_id,
    )
    return out


def submit(conn: duckdb.DuckDBPyConnection, decision_id: str, actor: Actor) -> Decision:
    return transition(conn, decision_id, "submit", actor)


def approve(
    conn: duckdb.DuckDBPyConnection, decision_id: str, actor: Actor, note: str | None = None
) -> Decision:
    return transition(conn, decision_id, "approve", actor, note=note)


def reject(
    conn: duckdb.DuckDBPyConnection, decision_id: str, actor: Actor, reason: str
) -> Decision:
    return transition(conn, decision_id, "reject", actor, reason=reason)


def cancel(conn: duckdb.DuckDBPyConnection, decision_id: str, actor: Actor) -> Decision:
    return transition(conn, decision_id, "cancel", actor)


def allowed_events(d: Decision, actor: Actor) -> list[str]:
    """Events this actor may try now (UI buttons); data freshness is checked on the call."""
    out = []
    for ev in ("submit", "reopen", "approve", "reject", "cancel", "send"):
        froms, _to, perm = TRANSITIONS[ev]
        if d.state not in froms:
            continue
        if ev == "send" and (d.kind != "ad_action" or not d.action):
            continue
        try:
            roles.check(actor, perm, owner_id=d.created_by)
        except Forbidden:
            continue
        out.append(ev)
    if d.state == "sent" and roles.allowed(actor.role, "action.verify"):
        out.append("verify")
    return out


# ---------------------------------------------------------------------- actions
def send_action(
    conn: duckdb.DuckDBPyConnection,
    decision_id: str,
    actor: Actor,
    *,
    mode: str = ACTION_MODE,
    now: datetime | None = None,
    shop_id: str = "shop-demo",
) -> tuple[Decision, ActionRecord]:
    """``approved`` → ``sent``: record a **simulated** action (``mode='simulated'`` only)."""
    if mode != ACTION_MODE:
        raise ValidationFailed(
            "Bản hackathon chỉ gửi thao tác mô phỏng (mode = simulated).", mode=mode
        )
    ts = now or utcnow()
    expire_overdue(conn, now=ts)
    d = get_decision(conn, decision_id)
    roles.check(actor, "action.send")
    if d.state == "expired":
        raise StateConflict(
            "Quyết định đã hết hiệu lực; cần chạy lại mô phỏng và duyệt lại.", state=d.state
        )
    if d.state != "approved":
        raise StateConflict(
            f"Chỉ gửi thao tác cho quyết định đã duyệt (đang là"
            f" '{STATE_LABEL_VI.get(d.state, d.state)}').",
            state=d.state,
        )
    if d.kind != "ad_action" or not d.action:
        raise StateConflict("Quyết định này không có thao tác để gửi.", kind=d.kind)
    if d.expected_outcome and not d.expected_outcome.get("feasible", True):
        raise ConstraintViolation(
            "Hành động vi phạm ràng buộc.", violations=d.expected_outcome.get("violations", [])
        )
    aid = new_id("act")
    payload = {
        "campaign_id": d.target_id,
        "action": d.action,
        "label": d.action_label,
        "simulated": True,
        "note_vi": SIMULATED_LABEL_VI,
    }
    row = conn.execute(
        "UPDATE decisions SET state = 'sent' WHERE id = ? AND state = 'approved' RETURNING id",
        [decision_id],
    ).fetchone()
    if row is None:
        raise StateConflict("Quyết định vừa được người khác thay đổi; tải lại.")
    conn.execute(
        "INSERT INTO actions (id, decision_id, mode, payload, sent_by, sent_at, state)"
        " VALUES (?, ?, ?, ?, ?, ?, 'sent')",
        [aid, decision_id, mode, _dump(payload), actor.id, ts],
    )
    out = get_decision(conn, decision_id)
    act = get_action(conn, aid)
    write_audit(
        conn,
        actor,
        "decision.send",
        "decision",
        decision_id,
        before={"state": d.state},
        after={"state": out.state, "action_id": aid, "mode": mode},
        shop_id=shop_id,
    )
    return out, act


def compare_observed(action: dict[str, Any], observed: dict[str, Any]) -> tuple[bool, str]:
    """Does an observed ads status / budget match the action? (10 §2 "Kiểm lại"). Returns
    ``(ok, reason)``; inconclusive input counts as a match only when nothing contradicts."""
    kind = action.get("kind")
    status = str(observed.get("observed_status") or "").strip().lower()
    budget = observed.get("observed_budget_vnd")
    spend = observed.get("observed_spend_vnd")
    if kind in ("pause", "pause_until_restock"):
        if status in PAUSED_STATUSES or spend == 0:
            return True, "Quảng cáo đã tạm dừng."
        if status or spend:
            return False, f"Quảng cáo chưa dừng (trạng thái '{status or '?'}', chi {spend})."
        return False, "Thiếu trạng thái quan sát."
    if kind == "reduce_budget":
        expected = observed.get("expected_budget_vnd")
        if budget is None or expected is None:
            return False, "Thiếu ngân sách quan sát / kỳ vọng."
        limit = (1 - float(action.get("pct") or 0)) * float(expected) * 1.15
        if float(budget) <= limit:
            return True, "Ngân sách đã giảm đúng mức."
        return False, f"Ngân sách {budget} cao hơn mức kỳ vọng {int(limit)} (dung sai 15%)."
    if kind == "continue":
        if status in PAUSED_STATUSES:
            return False, "Quảng cáo đang dừng trong khi quyết định là tiếp tục."
        return True, "Quảng cáo vẫn chạy."
    if kind == "schedule_hours":
        if status:
            return True, "Đã ghi nhận trạng thái theo khung giờ."
        return False, "Thiếu trạng thái quan sát."
    return False, "Không đối chiếu được hành động này."


def verify_action(
    conn: duckdb.DuckDBPyConnection,
    action_id: str,
    actor: Actor,
    *,
    result: Literal["verified", "failed"] | None = None,
    note: str | None = None,
    observed: dict[str, Any] | None = None,
    now: datetime | None = None,
    shop_id: str = "shop-demo",
) -> tuple[Decision, ActionRecord]:
    """``sent`` → ``verified | failed`` (action and its decision).

    * ``result`` given → a person marks it ("Đánh dấu đã kiểm lại"): ``verified_by_human`` =
      true unless ``actor.via == 'system'``. ``failed`` needs ``note`` (the reason).
    * ``result`` None and ``observed`` given → :func:`compare_observed` decides.
    """
    ts = now or utcnow()
    act = get_action(conn, action_id)
    d = get_decision(conn, act.decision_id)
    roles.check(actor, "action.verify")
    if act.state != "sent":
        raise StateConflict(
            f"Thao tác đang ở trạng thái '{act.state}', không kiểm lại được.", state=act.state
        )
    reason = None
    if result is None:
        if not observed:
            raise ValidationFailed("Cần chọn kết quả kiểm lại hoặc gửi trạng thái quan sát.")
        ok, why = compare_observed(d.action or {}, observed)
        result = "verified" if ok else "failed"
        reason = None if ok else why
        note = note or why
    elif result == "failed":
        if not (note and note.strip()):
            raise ValidationFailed("Đánh dấu không khớp cần ghi lý do.", field="note")
        reason = note.strip()[:2000]
    human = actor.via != "system"
    conn.execute(
        "UPDATE actions SET state = ?, verified_at = ?, verified_by = ?, verified_by_human = ?,"
        " observed = ?, failure_reason = ?, note = ? WHERE id = ?",
        [
            result,
            ts,
            actor.id,
            human,
            _dump(observed),
            reason,
            (note or "").strip()[:2000] or None,
            action_id,
        ],
    )
    if d.state == "sent":
        conn.execute(
            "UPDATE decisions SET state = ? WHERE id = ? AND state = 'sent'", [result, d.id]
        )
    out = get_decision(conn, d.id)
    act2 = get_action(conn, action_id)
    write_audit(
        conn,
        actor,
        f"decision.{'verify' if result == 'verified' else 'fail'}",
        "decision",
        d.id,
        before={"state": d.state, "action_state": act.state},
        after={
            "state": out.state,
            "action_id": action_id,
            "verified_by_human": human,
            "note": act2.note,
        },
        shop_id=shop_id,
    )
    return out, act2
