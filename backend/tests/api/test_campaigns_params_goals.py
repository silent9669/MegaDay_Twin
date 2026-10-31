"""API-04 (campaigns, iROAS) + API-05/ECO-02 (parameters, goals) + GOV-02 role checks."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.economics.parameters import set_parameter
from tests.api.helpers import LEAD, OPS, VIEWER, err, is_local_iso, ok

V1 = "/api/v1"


@pytest.fixture(scope="module")
def with_ads(api: TestClient) -> Iterator[TestClient]:
    """Fitted iROAS for every campaign of the copy (ADS-04)."""
    from app.ads_effect.iroas import estimate_all, write_estimates

    conn = api.app.state.db.write  # type: ignore[attr-defined]
    write_estimates(conn, estimate_all(conn).estimates)
    yield api


# ---------------------------------------------------------------- campaigns
def test_campaign_list_and_detail(with_ads: TestClient) -> None:
    api = with_ads
    body = ok(api.get(f"{V1}/campaigns", headers=VIEWER))
    cd2 = next(c for c in body["data"] if c["campaign_id"] == "CD002")
    assert set(cd2["skus"]) == {"NOICHIEN2L", "GIAYLOT50", "COMBO-NOICHIEN"}
    assert cd2["sku_level_control"] is False and cd2["daily_budget_vnd"] > 0
    assert cd2["iroas"] is not None and cd2["iroas"]["p10"] <= cd2["iroas"]["p90"]
    by_sku = ok(api.get(f"{V1}/campaigns", params={"sku": "GIAYLOT50"}, headers=VIEWER))["data"]
    assert [c["campaign_id"] for c in by_sku] == ["CD002"]
    d = ok(api.get(f"{V1}/campaigns/CD002", headers=VIEWER))["data"]
    assert d["name"].startswith("[Tìm kiếm] Nồi chiên")
    assert d["price_max_vnd"] == 690_000  # V slider max: selling price of the nồi
    assert {s["sku"] for s in d["sku_detail"]} == {"NOICHIEN2L", "GIAYLOT50", "COMBO-NOICHIEN"}
    must = [c for c in d["commitments"] if c["kind"] == "campaign_must_run"]
    assert [c["windows_local"] for c in must] in ([[[0, 2]], [[12, 14]]], [])
    err(api.get(f"{V1}/campaigns/CD999", headers=VIEWER), 404, "NOT_FOUND")


def test_iroas(with_ads: TestClient) -> None:
    body = ok(with_ads.get(f"{V1}/campaigns/CD002/iroas", headers=VIEWER))
    d = body["data"]
    assert d["iroas"]["p10"] <= d["iroas"]["p50"] <= d["iroas"]["p90"]
    assert d["platform_roas"] is not None and d["inflation"] is not None
    assert is_local_iso(d["window"]["from"]) and is_local_iso(d["window"]["to"])
    assert body["meta"]["confidence"] == d["confidence"]
    assert body["meta"]["sources"][0]["id"] == d["estimate_id"]
    err(with_ads.get(f"{V1}/campaigns/CD999/iroas", headers=VIEWER), 404, "NOT_FOUND")


# ---------------------------------------------------------------- parameters
def test_parameters_list(api: TestClient) -> None:
    items = ok(api.get(f"{V1}/parameters", headers=VIEWER))["data"]
    keys = {i["key"] for i in items}
    assert {"leftover_value_per_unit", "restock_delay_days", "platform_fee_rate"} <= keys
    fee = next(i for i in items if i["key"] == "platform_fee_rate")
    assert fee["is_default"] and fee["editable_by"] == "lead" and fee["can_edit"] is False
    sku = ok(
        api.get(f"{V1}/parameters", params={"scope": "sku", "scope_id": "NOICHIEN2L"},
                headers=OPS)
    )["data"]  # fmt: skip
    v = next(i for i in sku if i["key"] == "leftover_value_per_unit")
    assert v["max"] == 690_000 and v["default_rule"] and v["can_edit"] is True
    # V net of cost (M3-whatif §3): default 0, may go down to −cost
    assert v["default"] == 0 and v["value"] == 0 and v["min"] == -455_000 and v["min_rule"]
    err(api.get(f"{V1}/parameters", params={"scope": "sku"}, headers=VIEWER), 400,
        "VALIDATION_ERROR")  # fmt: skip


def test_put_parameter_roles_and_ranges(api: TestClient) -> None:
    url = f"{V1}/parameters/leftover_value_per_unit"
    body = {"scope": "sku", "scope_id": "NOICHIEN2L", "value": 20_000, "reason": "Thanh lý"}
    err(api.put(url, json=body, headers=VIEWER), 403, "FORBIDDEN")
    d = ok(api.put(url, json=body, headers=OPS))["data"]
    assert d["value"] == 20_000 and d["version"] == 1 and d["scope"] == "sku"
    assert d["set_by"] == "operator" and d["reason"] == "Thanh lý"
    d2 = ok(api.put(url, json={**body, "value": 25_000}, headers=LEAD))["data"]
    assert d2["version"] == 2
    e = err(api.put(url, json={**body, "value": 900_000}, headers=OPS), 400, "PARAM_OUT_OF_RANGE")
    assert e["error"]["details"]["max"] == 690_000
    d3 = ok(api.put(url, json={**body, "value": -100_000}, headers=OPS))["data"]
    assert d3["value"] == -100_000  # recovered below cost
    e3 = err(api.put(url, json={**body, "value": -500_000}, headers=OPS), 400, "PARAM_OUT_OF_RANGE")
    assert e3["error"]["details"]["min"] == -455_000
    # lead-only key
    fee = {"scope": "shop", "scope_id": "*", "value": 0.1}
    err(api.put(f"{V1}/parameters/platform_fee_rate", json=fee, headers=OPS), 403, "FORBIDDEN")
    ok(api.put(f"{V1}/parameters/platform_fee_rate", json=fee, headers=LEAD))
    ok(api.put(f"{V1}/parameters/platform_fee_rate", json={**fee, "value": 0.12}, headers=LEAD))
    err(api.put(f"{V1}/parameters/khong_co", json=fee, headers=LEAD), 400, "PARAM_OUT_OF_RANGE")
    err(
        api.put(f"{V1}/parameters/platform_fee_rate", json={**fee, "scope": "sku",
                "scope_id": "NOICHIEN2L"}, headers=LEAD),
        400,
        "VALIDATION_ERROR",
    )  # fmt: skip


def test_ai_suggestion_needs_confirmation(api: TestClient) -> None:
    conn = api.app.state.db.write  # type: ignore[attr-defined]
    sug = set_parameter(
        conn,
        "restock_delay_days",
        2.0,
        scope="sku",
        scope_id="GIAYLOT50",
        set_by="ai_suggested",
        reason="AI: nhà cung cấp hay trễ 2 ngày",
    )
    q = {"scope": "sku", "scope_id": "GIAYLOT50", "key": "restock_delay_days"}
    item = ok(api.get(f"{V1}/parameters", params=q, headers=VIEWER))["data"][0]
    assert item["value"] == 0.0 and item["is_default"]  # not effective yet
    assert item["pending_suggestions"][0]["version"] == sug.version
    url = f"{V1}/parameters/restock_delay_days/confirm"
    payload = {"scope": "sku", "scope_id": "GIAYLOT50", "version": sug.version}
    err(api.post(url, json=payload, headers=VIEWER), 403, "FORBIDDEN")
    d = ok(api.post(url, json=payload, headers=OPS))["data"]
    assert d["value"] == 2.0 and d["set_by"] == "ai_suggested_confirmed"
    assert d["pending_suggestions"] == []
    err(api.post(url, json={**payload, "version": 99}, headers=OPS), 400, "VALIDATION_ERROR")


# ---------------------------------------------------------------- goals
def test_goal_planner_capabilities(api: TestClient) -> None:
    body = ok(api.get(f"{V1}/goal-planner/capabilities", headers=VIEWER))["data"]
    assert body == {
        "version": 1,
        "constraints": ["max_spend_vnd", "max_loss_vnd", "sell_target_prob", "min_marginal_return"],
    }
    assert api.get(f"{V1}/goal-planner/capabilities").status_code in (401, 403)


@pytest.mark.parametrize(
    "constraints",
    [
        {"max_spend_vnd": -1},
        {"max_spend_vnd": True},
        {"max_loss_vnd": "0"},
        {"sell_target_prob": 1.1},
        {"min_marginal_return": -1},
        {"target_units": 1.5},
        {"target_units": True},
        {"target_units": "4"},
    ],
)
def test_goal_rejects_invalid_constraints_before_replacing_active(
    api: TestClient, constraints: dict[str, object]
) -> None:
    before = ok(api.get(f"{V1}/goals", params={"scope_id": "CD004"}, headers=VIEWER))["data"]
    body = {
        "scope_id": "CD004",
        "kind": "sell_target",
        "target_units": 10,
        "deadline": "2026-11-12T00:00:00+07:00",
        "constraints": constraints,
    }
    err(api.post(f"{V1}/goals", json=body, headers=LEAD), 400, "VALIDATION_ERROR")
    after = ok(api.get(f"{V1}/goals", params={"scope_id": "CD004"}, headers=VIEWER))["data"]
    assert after == before


def test_goal_preserves_zero_budget_and_rejects_conflicting_constraints(api: TestClient) -> None:
    body = {
        "scope_id": "CD006",
        "kind": "profit",
        "deadline": "2026-11-12T00:00:00+07:00",
        "constraints": {"max_spend_vnd": 0, "max_loss_vnd": 0},
    }
    saved = ok(api.post(f"{V1}/goals", json=body, headers=LEAD), 201)["data"]
    assert saved["params"]["max_spend_vnd"] == 0 and saved["params"]["max_loss_vnd"] == 0
    err(
        api.post(f"{V1}/goals", json={**body, "params": {"max_spend_vnd": 1}}, headers=LEAD),
        400,
        "VALIDATION_ERROR",
    )


def test_goals_roles_versions(api: TestClient) -> None:
    body = {
        "scope": "campaign",
        "scope_id": "CD004",
        "kind": "profit",
        "deadline": "2026-11-12T00:00:00+07:00",
        "reason": "Giữ lãi",
    }
    err(api.post(f"{V1}/goals", json=body, headers=OPS), 403, "FORBIDDEN")
    g1 = ok(api.post(f"{V1}/goals", json=body, headers=LEAD), 201)["data"]
    assert g1["confirmed_by"] == "u-lead-1" and g1["active"] and g1["version"] == 1
    assert g1["label"].startswith("Lợi nhuận tới") and is_local_iso(g1["deadline"])
    g2 = ok(
        api.put(
            f"{V1}/goals/campaign/CD004",
            json={"kind": "sell_target", "deadline": body["deadline"],
                  "constraints": {"target_units": 40}},
            headers=LEAD,
        )
    )["data"]  # fmt: skip
    assert g2["version"] == 2 and g2["target_units"] == 40
    active = ok(api.get(f"{V1}/goals", params={"scope_id": "CD004"}, headers=VIEWER))["data"]
    assert [g["goal_id"] for g in active] == [g2["goal_id"]]
    old = ok(api.get(f"{V1}/goals/{g1['goal_id']}", headers=VIEWER))["data"]
    assert old["active"] is False
    err(
        api.post(f"{V1}/goals", json={**body, "kind": "sell_target"}, headers=LEAD),
        400,
        "VALIDATION_ERROR",
    )
    err(api.post(f"{V1}/goals", json={**body, "scope_id": "CD999"}, headers=LEAD), 404,
        "NOT_FOUND")  # fmt: skip
    err(api.get(f"{V1}/goals/goal-khong-co", headers=VIEWER), 404, "NOT_FOUND")
