"""API-01 (auth, freshness) + API-04 (SKUs, panel, forecast, demand group, scores)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.helpers import LEAD, OPS, VIEWER, err, is_local_iso, ok

V1 = "/api/v1"


# ---------------------------------------------------------------- auth (API-01, GOV-02)
def test_health_needs_no_auth(api: TestClient) -> None:
    assert api.get(f"{V1}/health").status_code == 200


def test_missing_user_401(api: TestClient) -> None:
    err(api.get(f"{V1}/skus"), 401, "UNAUTHENTICATED")
    err(api.get(f"{V1}/me", headers={"X-Role": "lead"}), 401, "UNAUTHENTICATED")


def test_bad_role_401(api: TestClient) -> None:
    err(api.get(f"{V1}/me", headers={"X-User": "x", "X-Role": "admin"}), 401, "UNAUTHENTICATED")


def test_me_defaults_to_viewer(api: TestClient) -> None:
    body = ok(api.get(f"{V1}/me", headers={"X-User": "u-x"}))
    assert body["data"]["role"] == "viewer" and not body["data"]["can_run_scenarios"]
    lead = ok(api.get(f"{V1}/me", headers=LEAD))["data"]
    assert lead["can_edit_goals"] and lead["role_label"] == "Trưởng ca"


# ---------------------------------------------------------------- freshness
def test_freshness_contract(api: TestClient) -> None:
    body = ok(api.get(f"{V1}/freshness", headers=VIEWER))
    d = body["data"]
    srcs = {s["source"]: s for s in d["sources"]}
    assert set(srcs) == {"orders", "inventory", "ads", "cost", "campaigns", "sale_program"}
    assert d["clock"] == "data" and is_local_iso(d["now"])
    assert srcs["orders"]["threshold_minutes"] == 30 and srcs["ads"]["threshold_minutes"] == 180
    assert srcs["orders"]["status"] in ("fresh", "stale")
    assert srcs["orders"]["lag_minutes"] == 0  # demo clock = latest data mark
    assert srcs["cost"]["status"] in ("fresh", "partial")
    assert body["meta"]["data_as_of"] == d["data_as_of"] and is_local_iso(d["data_as_of"])
    assert body["meta"]["compute_state"] == "fresh"


def test_freshness_with_now_marks_stale(api: TestClient) -> None:
    d = ok(api.get(f"{V1}/freshness", params={"now": "2027-01-01T00:00:00+07:00"}, headers=VIEWER))
    srcs = {s["source"]: s for s in d["data"]["sources"]}
    assert d["data"]["clock"] == "query"
    assert srcs["orders"]["status"] == "stale" and srcs["orders"]["lag_minutes"] > 30


# ---------------------------------------------------------------- SKUs
def test_skus_page_and_cursor(api: TestClient) -> None:
    first = ok(api.get(f"{V1}/skus", params={"limit": 7}, headers=VIEWER))
    assert len(first["data"]) == 7 and first["next_cursor"]
    assert first["meta"]["data_as_of"] is not None
    row = first["data"][0]
    for k in (
        "sku",
        "name",
        "stock_available",
        "hours_of_cover",
        "sold_today_units",
        "deviation",
        "campaign",
        "recommendation",
        "open_alerts",
        "priority_score",
        "confidence",
        "compute_state",
        "computed_at",
    ):
        assert k in row
    scores = [r["priority_score"] for r in first["data"]]
    assert scores == sorted(scores, reverse=True)
    seen = {r["sku"] for r in first["data"]}
    cur = first["next_cursor"]
    while cur:
        page = ok(api.get(f"{V1}/skus", params={"limit": 7, "cursor": cur}, headers=VIEWER))
        seen |= {r["sku"] for r in page["data"]}
        cur = page["next_cursor"]
    total = len(ok(api.get(f"{V1}/skus", params={"limit": 500}, headers=VIEWER))["data"])
    assert len(seen) == total


def test_skus_filters_and_bad_cursor(api: TestClient) -> None:
    d = ok(api.get(f"{V1}/skus", params={"campaign_id": "CD002"}, headers=VIEWER))["data"]
    assert {r["sku"] for r in d} == {"NOICHIEN2L", "GIAYLOT50", "COMBO-NOICHIEN"}
    err(api.get(f"{V1}/skus", params={"cursor": "%%%"}, headers=VIEWER), 400, "VALIDATION_ERROR")
    err(api.get(f"{V1}/skus", params={"limit": 501}, headers=VIEWER), 400, "VALIDATION_ERROR")


def test_sku_detail(api: TestClient) -> None:
    body = ok(api.get(f"{V1}/skus/NOICHIEN2L", headers=VIEWER))
    d = body["data"]
    assert d["sku"] == "NOICHIEN2L" and d["campaign"]["id"] == "CD002"
    assert d["used_in_combos"] == ["COMBO-NOICHIEN"]
    assert set(d["campaign_detail"]["shared_with"]) == {"GIAYLOT50", "COMBO-NOICHIEN"}
    assert d["price_list_vnd"] == 690_000
    assert d["source"]["kind"] == "snapshot"
    assert any(c["kind"] == "campaign_must_run" for c in d["commitments"]) or d["commitments"] == []
    assert body["meta"]["sources"][0]["tool"] == "get_sku_snapshot"
    combo = ok(api.get(f"{V1}/skus/COMBO-NOICHIEN", headers=VIEWER))["data"]
    assert combo["is_bundle"] and {c["sku"] for c in combo["components_of"]} == {
        "NOICHIEN2L",
        "GIAYLOT50",
    }
    err(api.get(f"{V1}/skus/KHONGCO", headers=VIEWER), 404, "NOT_FOUND")


def test_panel_hour_and_day(api: TestClient) -> None:
    hours = ok(api.get(f"{V1}/skus/NOICHIEN2L/panel", headers=VIEWER))["data"]
    assert hours["grain"] == "hour" and 160 <= len(hours["points"]) <= 168
    p = hours["points"][0]
    assert is_local_iso(p["t"]) and {"units", "stock_end", "ad_spend_vnd", "event"} <= set(p)
    days = ok(
        api.get(
            f"{V1}/skus/NOICHIEN2L/panel",
            params={"from": "2026-10-09T00:00:00+07:00", "to": "2026-10-12T00:00:00+07:00",
                    "grain": "day"},
            headers=VIEWER,
        )
    )["data"]  # fmt: skip
    assert [x["t"][:10] for x in days["points"]] == ["2026-10-09", "2026-10-10", "2026-10-11"]
    assert days["points"][1]["event"] in ("double_day", "flash")
    assert sum(x["units"] for x in days["points"]) > 0
    err(
        api.get(
            f"{V1}/skus/NOICHIEN2L/panel",
            params={"start": "2026-10-12T00:00:00+07:00", "end": "2026-10-09T00:00:00+07:00"},
            headers=VIEWER,
        ),
        400,
        "VALIDATION_ERROR",
    )


def test_forecast_compute_and_read(api: TestClient) -> None:
    err(
        api.get(f"{V1}/skus/GIAYLOT50/forecast", params={"compute": False}, headers=VIEWER),
        404,
        "NOT_FOUND",
    )
    body = ok(
        api.get(f"{V1}/skus/GIAYLOT50/forecast", params={"horizon_hours": 12}, headers=VIEWER)
    )
    d = body["data"]
    assert d["sku"] == "GIAYLOT50" and len(d["hourly"]) == 12
    h = d["hourly"][0]
    assert h["p10"] <= h["p50"] <= h["p90"] and is_local_iso(h["t"])
    assert d["model"]["id"] and d["confidence"] in ("verified", "reference", "scenario")
    meta = body["meta"]
    assert meta["confidence"] == d["confidence"] and meta["compute_state"] in ("fresh", "stale")
    assert meta["sources"][0]["kind"] == "forecast" and meta["sources"][0]["id"] == d["forecast_id"]
    # the overview now shows cover hours for that SKU
    row = next(
        r
        for r in ok(api.get(f"{V1}/skus", params={"q": "GIAYLOT50"}, headers=VIEWER))["data"]
        if r["sku"] == "GIAYLOT50"
    )
    assert row["forecast_id"] == d["forecast_id"] and row["compute_state"] is not None


def test_demand_group_roles(api: TestClient) -> None:
    url = f"{V1}/skus/COCMEME-A/demand-group"
    err(api.put(url, json={"value": "trend"}, headers=VIEWER), 403, "FORBIDDEN")
    err(api.put(url, json={"value": "khong_co"}, headers=OPS), 400, "VALIDATION_ERROR")
    d = ok(api.put(url, json={"value": "trend", "reason": "Hàng theo trào lưu"}, headers=OPS))
    assert d["data"]["value"] == "trend" and d["data"]["source"] == "user"
    snap = ok(api.get(f"{V1}/skus/COCMEME-A", headers=VIEWER))["data"]
    assert snap["demand_group_info"] == {"value": "trend", "source": "user", "confirmed": True}
    back = ok(api.patch(url, json={"value": None}, headers=OPS))["data"]
    assert back["source"] == "auto"
    err(api.put(f"{V1}/skus/KHONGCO/demand-group", json={"value": None}, headers=OPS), 404,
        "NOT_FOUND")  # fmt: skip


def test_scores_shape(api: TestClient) -> None:
    for path in ("/forecast/scores", "/models/scores"):
        body = ok(api.get(f"{V1}{path}", headers=VIEWER))
        assert isinstance(body["data"], list) and "next_cursor" in body
