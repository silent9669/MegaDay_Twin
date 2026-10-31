"""Helpers shared by the API tests (headers, envelope checks, client factory)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.warehouse.db import Database

VIEWER = {"X-User": "u-view-1", "X-Role": "viewer"}
OPS = {"X-User": "u-ops-1", "X-Role": "operator"}
LEAD = {"X-User": "u-lead-1", "X-Role": "lead"}


def make_client(src: Path, dest_dir: Path) -> tuple[TestClient, Database]:
    dest = dest_dir / "api.duckdb"
    shutil.copy(src, dest)
    return client_for(dest)


def client_for(path: Path) -> tuple[TestClient, Database]:
    db = Database(path)
    settings = Settings(_env_file=None, duckdb_path=path, app_env="test")  # type: ignore[call-arg]
    return TestClient(create_app(settings=settings, db=db)), db


def ok(resp: Any, status: int = 200) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    body: dict[str, Any] = resp.json()
    assert "data" in body and "meta" in body, body
    return body


def err(resp: Any, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, resp.text
    body: dict[str, Any] = resp.json()
    assert body["error"]["code"] == code, body
    assert body["error"]["message"]
    return body


def is_local_iso(v: str | None) -> bool:
    return v is None or v.endswith("+07:00")
