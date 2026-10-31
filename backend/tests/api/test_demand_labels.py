from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.core.timeutil import utcnow
from app.forecast.demand import active_labels, forecast_signature, inventory_hash
from tests.api.helpers import OPS, VIEWER, err, ok


@pytest.fixture(autouse=True)
def aliases(api: TestClient) -> Iterator[None]:
    conn = api.app.state.db.write  # type: ignore[union-attr]
    conn.execute("DELETE FROM sku_demand_labels")
    conn.execute("DELETE FROM sku_aliases WHERE alias_id LIKE 'unify-%'")
    conn.execute(
        "INSERT INTO sku_aliases (alias_id,source_type,raw_code,raw_name,norm_name,sku_id,status) "
        "VALUES ('unify-1','inventory','UNIFY-1','Nồi chiên','noi chien','NOICHIEN2L','confirmed')"
    )
    yield
    conn.execute("DELETE FROM sku_demand_labels")
    conn.execute("DELETE FROM sku_aliases WHERE alias_id LIKE 'unify-%'")


def body(api: TestClient) -> dict[str, Any]:
    return {
        "run_id": "unify-run",
        "source_revision": 2,
        "source_fingerprint": "a" * 64,
        "inventory_sha256": inventory_hash(api.app.state.db.write),  # type: ignore[union-attr]
        "provider": "test:demand-v3",
        "market": "Vietnam",
        "assessed_on": utcnow().date().isoformat(),
        "completed_at": (utcnow() - timedelta(seconds=1)).isoformat(),
        "labels": [
            {
                "raw_code": "UNIFY-1",
                "name": "Nồi chiên",
                "label": "Seasonal",
                "reason": "Test supplied product evidence",
                "confidence": 0.4,
            }
        ],
    }


def test_label_sync_is_canonical_idempotent_and_time_bounded(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    monkeypatch.setattr(
        "app.api.demand_labels.publish", lambda request, event: events.append(event)
    )
    conn = api.app.state.db.write  # type: ignore[union-attr]
    payload = body(api)
    before = forecast_signature(conn, utcnow() + timedelta(days=1))
    result = ok(api.post("/api/v1/demand-labels/sync", json=payload, headers=OPS))["data"]
    assert result["accepted"][0]["sku_id"] == "NOICHIEN2L"
    assert result["pending"] == []
    again = ok(api.post("/api/v1/demand-labels/sync", json=payload, headers=OPS))["data"]
    assert again == result
    assert len(events) == 1 and events[0].sku_ids == ["NOICHIEN2L"]
    assert conn.execute("SELECT count(*) FROM sku_demand_labels").fetchone()[0] == 1
    assert active_labels(conn, utcnow() - timedelta(days=1)) == {}
    assert active_labels(conn)["NOICHIEN2L"]["label"] == "Seasonal"
    assert forecast_signature(conn, utcnow() + timedelta(days=1)) != before
    detail = ok(api.get("/api/v1/skus/NOICHIEN2L", headers=VIEWER))["data"]
    assert detail["demand"]["primary_label"] == "Seasonal"
    assert detail["demand"]["source"] == "warehouse_ai"


def test_pending_alias_is_not_guessed_from_a_name(api: TestClient) -> None:
    payload = body(api)
    payload["labels"][0]["raw_code"] = "MISSING"
    result = ok(api.post("/api/v1/demand-labels/sync", json=payload, headers=OPS))["data"]
    assert not result["accepted"]
    assert result["pending"][0]["raw_code"] == "MISSING"


def test_manual_replay_label_waits_for_its_evidence_hour(api: TestClient) -> None:
    conn = api.app.state.db.write  # type: ignore[union-attr]
    payload = body(api)
    evidence = conn.execute("SELECT max(snapshot_at) FROM inventory_snapshots").fetchone()[0]
    payload["assessed_on"] = evidence.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat()
    payload["evidence_through"] = evidence.isoformat()
    payload["labels"][0]["manual"] = True
    ok(api.post("/api/v1/demand-labels/sync", json=payload, headers=OPS))
    boundary = max(evidence, datetime.fromisoformat(payload["completed_at"]))
    assert active_labels(conn, boundary - timedelta(seconds=1)) == {}
    assert active_labels(conn, boundary)["NOICHIEN2L"]["manual"] is True
    detail = ok(api.get("/api/v1/skus/NOICHIEN2L", headers=VIEWER))["data"]
    assert detail["demand"]["source"] == "warehouse_manual"


def test_two_codes_for_one_sku_require_resolution(api: TestClient) -> None:
    conn = api.app.state.db.write  # type: ignore[union-attr]
    conn.execute(
        "INSERT INTO sku_aliases (alias_id,source_type,raw_code,raw_name,norm_name,sku_id,status) "
        "VALUES ('unify-2','inventory','UNIFY-2','Variant','variant','NOICHIEN2L','confirmed')"
    )
    payload = body(api)
    payload["labels"].append({**payload["labels"][0], "raw_code": "UNIFY-2", "label": "Trendy"})
    result = ok(api.post("/api/v1/demand-labels/sync", json=payload, headers=OPS))["data"]
    assert not result["accepted"] and len(result["pending"]) == 2
    assert active_labels(conn) == {}


def test_same_origin_forecast_refreshes_only_when_inputs_change(api: TestClient) -> None:
    from app.simulator.inputs import load_forecasts

    conn = api.app.state.db.write  # type: ignore[union-attr]
    # A future simulated origin makes today's label eligible. No real API call.
    origin = (utcnow() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    first = load_forecasts(conn, ["NOICHIEN2L"], origin, 24)["NOICHIEN2L"]
    cached = load_forecasts(conn, ["NOICHIEN2L"], origin, 24)["NOICHIEN2L"]
    assert cached.forecast_id == first.forecast_id
    conn.execute("UPDATE skus SET demand_group_at=? WHERE sku_id='NOICHIEN2L'", [utcnow()])
    assert (
        load_forecasts(conn, ["NOICHIEN2L"], origin, 24)["NOICHIEN2L"].forecast_id
        == first.forecast_id
    )
    ok(api.post("/api/v1/demand-labels/sync", json=body(api), headers=OPS))
    refreshed = load_forecasts(conn, ["NOICHIEN2L"], origin, 24)["NOICHIEN2L"]
    assert refreshed.forecast_id != first.forecast_id
    assert refreshed.primary_label == "Seasonal"
    assert refreshed.model_selection["label_source"] == "warehouse_ai"


def test_ambiguous_alias_and_remapping_invalidate_old_labels(api: TestClient) -> None:
    conn = api.app.state.db.write  # type: ignore[union-attr]
    payload = body(api)
    ok(api.post("/api/v1/demand-labels/sync", json=payload, headers=OPS))
    conn.execute(
        "INSERT INTO sku_aliases (alias_id,source_type,raw_code,raw_name,norm_name,sku_id,status) "
        "VALUES ('unify-2','inventory','UNIFY-1','Other','other',NULL,'pending')"
    )
    assert active_labels(conn) == {}
    result = ok(api.post("/api/v1/demand-labels/sync", json=payload, headers=OPS))["data"]
    assert not result["accepted"]


def test_wrong_source_and_invalid_scope_are_rejected(api: TestClient) -> None:
    payload = body(api)
    err(api.post("/api/v1/demand-labels/sync", json=payload, headers=VIEWER), 403, "FORBIDDEN")
    payload["inventory_sha256"] = "0" * 64
    err(api.post("/api/v1/demand-labels/sync", json=payload, headers=OPS), 409, "STATE_CONFLICT")
    payload = body(api)
    payload["completed_at"] = (utcnow() + timedelta(days=1)).isoformat()
    assert api.post("/api/v1/demand-labels/sync", json=payload, headers=OPS).status_code == 400
    payload = body(api)
    payload["market"] = "US"
    assert api.post("/api/v1/demand-labels/sync", json=payload, headers=OPS).status_code == 400
    payload = body(api)
    payload["labels"] *= 2
    assert api.post("/api/v1/demand-labels/sync", json=payload, headers=OPS).status_code == 400


def test_label_changes_require_new_preview(api: TestClient) -> None:
    from tests.api.helpers import LEAD
    from tests.api.test_sale_plans import apply_body, preview, set_goal

    set_goal(api)
    plan = preview(api)
    saved_trace = plan["scenario"]["calculation_trace"]
    assert saved_trace["sources"]["forecasts"]
    assert all(
        f["model_selection"]["model_id"] == f["model_id"]
        for f in saved_trace["sources"]["forecasts"]
    )
    ok(api.post("/api/v1/demand-labels/sync", json=body(api), headers=OPS))
    reread = ok(api.get(f"/api/v1/sale-plans/{plan['preview_id']}", headers=VIEWER))["data"]
    assert reread["scenario"]["calculation_trace"] == saved_trace
    refreshed = preview(api)["scenario"]["calculation_trace"]
    chosen = next(f for f in refreshed["sources"]["forecasts"] if f["sku_id"] == "NOICHIEN2L")
    assert chosen["model_selection"]["primary_label"] == "Seasonal"
    err(
        api.post(
            f"/api/v1/sale-plans/{plan['preview_id']}/apply", json=apply_body(plan), headers=LEAD
        ),
        409,
        "STALE_INPUT",
    )
