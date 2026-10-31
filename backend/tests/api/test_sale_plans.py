"""Preview isolation, explicit activation and stale-input / goal concurrency guards."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.helpers import LEAD, OPS, VIEWER, err, ok

V1 = "/api/v1"
START = "2026-11-11T01:00:00+07:00"
END = "2026-11-11T05:00:00+07:00"


def set_goal(api: TestClient, **extra: Any) -> dict[str, Any]:
    return ok(
        api.post(
            f"{V1}/goals",
            headers=LEAD,
            json={
                "scope_id": "CD002",
                "kind": "profit",
                "deadline": "2026-11-12T00:00:00+07:00",
                **extra,
            },
        ),
        201,
    )["data"]


def current(api: TestClient) -> dict[str, Any]:
    return ok(api.get(f"{V1}/sale-plans/context", params={"campaign_id": "CD002"}, headers=VIEWER))[
        "data"
    ]


def body(api: TestClient, **extra: Any) -> dict[str, Any]:
    return {
        "campaign_id": "CD002",
        "goal": {"kind": "profit", "deadline": END},
        "start": START,
        "horizon_hours": 4,
        "samples": 50,
        "seed": 43,
        "expected_goal": current(api)["base_goal"],
        **extra,
    }


def preview(api: TestClient, **extra: Any) -> dict[str, Any]:
    return ok(api.post(f"{V1}/sale-plans/preview", json=body(api, **extra), headers=OPS))["data"]


def apply_body(plan: dict[str, Any]) -> dict[str, Any]:
    base = plan["base_goal"]
    return {
        "confirmed": True,
        "expected_goal": {"goal_id": base["goal_id"], "version": base["version"]} if base else None,
    }


def test_preview_preserves_active_goal_and_recommendation(api: TestClient) -> None:
    base = set_goal(api, params={"max_spend_vnd": 5_000_000})
    standard = ok(
        api.post(
            f"{V1}/scenarios/run",
            json={
                "campaign_id": "CD002",
                "goal_id": base["goal_id"],
                "start": START,
                "horizon_hours": 4,
                "samples": 50,
            },
            headers=OPS,
        )
    )["data"]
    plan = preview(api)
    assert plan["effective_goal"]["active"] is False
    assert plan["base_goal"]["goal_id"] == base["goal_id"]
    assert current(api)["active_goal"] == base
    assert plan["scenario"]["recommendation"]["what_if"] is True
    assert plan["scenario"]["recommendation"]["active"] is False
    active = ok(api.get(f"{V1}/recommendations", params={"campaign_id": "CD002"}, headers=VIEWER))[
        "data"
    ]
    assert [r["scenario_id"] for r in active] == [standard["scenario_id"]]
    reread = ok(api.get(f"{V1}/sale-plans/{plan['preview_id']}", headers=VIEWER))["data"]
    assert reread["scenario"]["calculation_trace"] == plan["scenario"]["calculation_trace"]
    assert reread["effective_goal"] == plan["effective_goal"]
    rerun = ok(
        api.post(
            f"{V1}/scenarios/run",
            json={
                "campaign_id": "CD002",
                "goal_id": plan["effective_goal"]["goal_id"],
                "start": START,
                "horizon_hours": 4,
                "samples": 50,
            },
            headers=OPS,
        )
    )["data"]
    assert rerun["recommendation"]["what_if"] is True
    assert rerun["recommendation"]["active"] is False


def test_apply_is_explicit_idempotent_and_keeps_simulation_immutable(api: TestClient) -> None:
    set_goal(api)
    plan = preview(api)
    assert plan["can_apply"]
    path = f"{V1}/sale-plans/{plan['preview_id']}/apply"
    err(api.post(path, json=apply_body(plan), headers=OPS), 403, "FORBIDDEN")
    err(
        api.post(path, json={**apply_body(plan), "confirmed": False}, headers=LEAD),
        400,
        "VALIDATION_ERROR",
    )
    applied = ok(api.post(path, json=apply_body(plan), headers=LEAD))["data"]
    assert applied["already_applied"] is False
    assert applied["goal"]["active"]
    assert current(api)["active_goal"]["goal_id"] == plan["effective_goal"]["goal_id"]
    again = ok(api.post(path, json=apply_body(plan), headers=LEAD))["data"]
    assert again["already_applied"] is True
    assert again["goal"] == applied["goal"]
    reread = ok(api.get(f"{V1}/sale-plans/{plan['preview_id']}", headers=VIEWER))["data"]
    assert reread["scenario"]["calculation_trace"] == plan["scenario"]["calculation_trace"]
    assert reread["scenario"]["recommendation"]["what_if"]
    set_goal(api)
    err(api.post(path, json=apply_body(plan), headers=LEAD), 409, "STATE_CONFLICT")


def test_changed_goal_blocks_preview_and_apply(api: TestClient) -> None:
    set_goal(api)
    request = body(api)
    plan = preview(api)
    latest = set_goal(api, kind="clear_stock")
    err(api.post(f"{V1}/sale-plans/preview", json=request, headers=OPS), 409, "STATE_CONFLICT")
    err(
        api.post(
            f"{V1}/sale-plans/{plan['preview_id']}/apply", json=apply_body(plan), headers=LEAD
        ),
        409,
        "STATE_CONFLICT",
    )
    assert current(api)["active_goal"]["goal_id"] == latest["goal_id"]


def test_sku_inherits_tighter_campaign_limits(api: TestClient) -> None:
    set_goal(api, params={"max_spend_vnd": 1_000_000, "max_loss_vnd": 0})
    plan = preview(
        api,
        focus_sku="NOICHIEN2L",
        goal={
            "kind": "sell_target",
            "target_units": 1,
            "deadline": END,
            "params": {
                "max_spend_vnd": 5_000_000,
                "max_loss_vnd": 500_000,
                "sell_target_prob": 0.01,
            },
        },
    )
    params = plan["effective_goal"]["params"]
    assert params["max_spend_vnd"] == 1_000_000 and params["max_loss_vnd"] == 0
    assert params["focus_sku"] == "NOICHIEN2L"
    assert params["planner_parent_goal_kind"] == "profit"
    assert plan["scenario"]["calculation_trace"]["goal"]["params"]["max_spend_vnd"] == 1_000_000
    assert current(api)["active_goal"]["kind"] == "profit"


def test_conflicting_sku_priority_is_preview_only(api: TestClient) -> None:
    base = set_goal(api)
    plan = preview(api, focus_sku="NOICHIEN2L", goal={"kind": "clear_stock", "deadline": END})
    assert plan["can_apply"] is False
    assert any("ưu tiên" in notice for notice in plan["notices"])
    err(
        api.post(
            f"{V1}/sale-plans/{plan['preview_id']}/apply", json=apply_body(plan), headers=LEAD
        ),
        409,
        "STATE_CONFLICT",
    )
    assert current(api)["active_goal"] == base


def test_same_timestamp_cost_change_requires_new_preview(api: TestClient) -> None:
    base = set_goal(api)
    plan = preview(api)
    conn = api.app.state.db.write  # type: ignore[union-attr]
    conn.execute("UPDATE cost_items SET unit_cost=unit_cost+1 WHERE sku_id='NOICHIEN2L'")
    try:
        err(
            api.post(
                f"{V1}/sale-plans/{plan['preview_id']}/apply", json=apply_body(plan), headers=LEAD
            ),
            409,
            "STALE_INPUT",
        )
        assert current(api)["active_goal"] == base
    finally:
        conn.execute("UPDATE cost_items SET unit_cost=unit_cost-1 WHERE sku_id='NOICHIEN2L'")


def test_apply_rolls_back_on_write_failure(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import sale_plans

    base = set_goal(api)
    plan = preview(api)

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected write failure")

    monkeypatch.setattr(sale_plans, "audit", broken)
    with pytest.raises(RuntimeError, match="injected write failure"):
        api.post(f"{V1}/sale-plans/{plan['preview_id']}/apply", json=apply_body(plan), headers=LEAD)
    assert current(api)["active_goal"] == base
    assert (
        ok(api.get(f"{V1}/sale-plans/{plan['preview_id']}", headers=VIEWER))["data"][
            "applied_goal_id"
        ]
        is None
    )


def test_failed_preview_never_replaces_goal(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import sale_plans

    base = set_goal(api)

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("simulation unavailable")

    monkeypatch.setattr(sale_plans, "run_simulation", broken)
    with pytest.raises(RuntimeError, match="simulation unavailable"):
        preview(api)
    assert current(api)["active_goal"] == base


def test_validation_and_permissions(api: TestClient) -> None:
    set_goal(api)
    request = body(api)
    err(api.post(f"{V1}/sale-plans/preview", json=request, headers=VIEWER), 403, "FORBIDDEN")
    err(
        api.post(
            f"{V1}/sale-plans/preview",
            json={k: v for k, v in request.items() if k != "expected_goal"},
            headers=OPS,
        ),
        400,
        "VALIDATION_ERROR",
    )


def test_unset_parent_efficiency_uses_actual_model_default(api: TestClient) -> None:
    from app.economics.parameters import load_param_set

    set_goal(api, kind="efficiency", params={"focus_sku": "NOICHIEN2L"})
    plan = preview(
        api,
        focus_sku="NOICHIEN2L",
        goal={"kind": "efficiency", "deadline": END, "params": {"min_marginal_return": 0}},
    )
    conn = api.app.state.db.write  # type: ignore[union-attr]
    expected = load_param_set(conn, campaign_id="CD002").num("min_marginal_return")
    assert expected > 0
    assert plan["effective_goal"]["params"]["min_marginal_return"] == expected


def test_null_base_token_cannot_overwrite_a_new_goal(api: TestClient) -> None:
    conn = api.app.state.db.write  # type: ignore[union-attr]
    conn.execute("UPDATE goals SET active=FALSE WHERE scope='campaign' AND scope_id='CD002'")
    assert current(api)["base_goal"] is None
    plan = preview(api)
    assert plan["base_goal"] is None
    latest = set_goal(api)
    err(
        api.post(
            f"{V1}/sale-plans/{plan['preview_id']}/apply", json=apply_body(plan), headers=LEAD
        ),
        409,
        "STATE_CONFLICT",
    )
    assert current(api)["active_goal"] == latest


def test_invalid_parent_start_blocks_apply_without_crashing(api: TestClient) -> None:
    set_goal(api, params={"planner_start": "2026-11-11T01:00:00"})
    plan = preview(api, focus_sku="NOICHIEN2L")
    assert plan["can_apply"] is False
    assert any("thời điểm bắt đầu" in notice for notice in plan["notices"])


def test_preview_rejects_wrong_membership_and_window(api: TestClient) -> None:
    set_goal(api)
    request = body(api)
    err(
        api.post(
            f"{V1}/sale-plans/preview", json={**request, "focus_sku": "COCMEME-A"}, headers=OPS
        ),
        400,
        "VALIDATION_ERROR",
    )
    err(
        api.post(f"{V1}/sale-plans/preview", json={**request, "horizon_hours": 3}, headers=OPS),
        400,
        "VALIDATION_ERROR",
    )


def test_cached_active_goal_cannot_return_active_recommendation_after_deactivation(
    api: TestClient,
) -> None:
    base = set_goal(api)
    request = {
        "campaign_id": "CD002",
        "goal_id": base["goal_id"],
        "start": START,
        "horizon_hours": 4,
        "samples": 50,
        "seed": 47,
    }
    original = ok(api.post(f"{V1}/scenarios/run", json=request, headers=OPS))["data"]
    cached = ok(api.post(f"{V1}/scenarios/run", json=request, headers=OPS))["data"]
    assert cached["cache_hit"] and cached["scenario_id"] == original["scenario_id"]
    replacement = set_goal(api)
    latest = ok(
        api.post(
            f"{V1}/scenarios/run", json={**request, "goal_id": replacement["goal_id"]}, headers=OPS
        )
    )["data"]
    historical = ok(api.post(f"{V1}/scenarios/run", json=request, headers=OPS))["data"]
    assert historical["scenario_id"] != original["scenario_id"]
    assert historical["cache_hit"] is False
    assert historical["recommendation"]["what_if"] is True
    assert historical["recommendation"]["active"] is False
    assert historical["input_hash"] != original["input_hash"]
    again = ok(api.post(f"{V1}/scenarios/run", json=request, headers=OPS))["data"]
    assert again["cache_hit"] and again["recommendation"]["what_if"]
    active = ok(api.get(f"{V1}/recommendations", params={"campaign_id": "CD002"}, headers=VIEWER))[
        "data"
    ]
    assert [row["scenario_id"] for row in active] == [latest["scenario_id"]]
