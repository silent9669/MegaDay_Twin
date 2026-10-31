"""GET /api/v1/health + error envelope (08 §1)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.errors import ConstraintViolation, DataQualityError, StateConflict


def test_health_ok(client: TestClient) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"data", "meta"}
    data = body["data"]
    assert data["status"] == "ok"
    assert data["db"] == "ok"
    assert data["ai"] == "disabled"  # tests force AI_ENABLED=false
    assert body["meta"]["computed_at"].endswith("+07:00")
    assert r.headers["X-Request-ID"].startswith("req-")


def test_health_echoes_request_id(client: TestClient) -> None:
    r = client.get("/api/v1/health", headers={"X-Request-ID": "abc"})
    assert r.headers["X-Request-ID"] == "abc"


def test_openapi_served_at_both_paths(client: TestClient) -> None:
    a = client.get("/api/v1/openapi.json")
    b = client.get("/openapi.json")
    assert a.status_code == b.status_code == 200
    assert "/api/v1/health" in a.json()["paths"]
    assert a.json() == b.json()


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert r.json()["error"]["message"]


def test_app_errors_map_to_http(client: TestClient) -> None:
    router = APIRouter()

    @router.get("/_t/state")
    def state() -> None:
        raise StateConflict(decision_id="dec-1")

    @router.get("/_t/constraint")
    def constraint() -> None:
        raise ConstraintViolation(
            "Không được tắt CD002 trong khung flash sale.", rule="campaign_must_run"
        )

    @router.get("/_t/blocking")
    def blocking() -> None:
        raise DataQualityError(code_hint="MISSING_COST")

    @router.get("/_t/crash")
    def crash() -> None:
        raise RuntimeError("secret internals")

    @router.get("/_t/validate")
    def validate(n: int) -> int:
        return n

    client.app.include_router(router)  # type: ignore[attr-defined]

    r = client.get("/_t/state")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "STATE_CONFLICT")
    assert r.json()["error"]["details"] == {"decision_id": "dec-1"}

    r = client.get("/_t/constraint")
    assert (r.status_code, r.json()["error"]["code"]) == (409, "CONSTRAINT_VIOLATION")
    assert "CD002" in r.json()["error"]["message"]

    r = client.get("/_t/blocking")
    assert (r.status_code, r.json()["error"]["code"]) == (422, "BLOCKING_DATA_ISSUE")

    r = client.get("/_t/validate", params={"n": "abc"})
    assert (r.status_code, r.json()["error"]["code"]) == (400, "VALIDATION_ERROR")


def test_unhandled_error_is_500_without_leaking(settings, db) -> None:  # type: ignore[no-untyped-def]
    from app.main import create_app

    app = create_app(settings=settings, db=db)

    @app.get("/_t/crash")
    def crash() -> None:
        raise RuntimeError("secret internals")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/_t/crash")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in r.text
