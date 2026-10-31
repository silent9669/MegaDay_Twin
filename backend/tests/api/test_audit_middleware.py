"""GOV-07: the audit middleware writes one generic row for an unaudited successful write and
nothing when the endpoint already audited (same request id) or the write failed."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.governance.middleware import install_audit_middleware
from app.main import create_app
from app.warehouse.db import Database
from tests.api.helpers import OPS
from tests.unit.governance.seed import add_batch, seed_scenario


@pytest.fixture
def audited(tmp_path: Path) -> Iterator[tuple[TestClient, Database]]:
    path = tmp_path / "audit.duckdb"
    db = Database(path)
    settings = Settings(_env_file=None, duckdb_path=path, app_env="test")  # type: ignore[call-arg]
    app = create_app(settings=settings, db=db)

    @app.post("/api/v1/_probe/{item}")
    def probe(item: str) -> dict[str, str]:
        return {"item": item}

    install_audit_middleware(app)
    with TestClient(app) as c:
        yield c, db
    db.close()


def _rows(db: Database) -> list[tuple[str, str, str, str]]:
    return [
        (str(r[0]), str(r[1]), str(r[2]), str(r[3]))
        for r in db.write.execute(
            'SELECT action, target_id, actor, request_id FROM audit_log ORDER BY "at", id'
        ).fetchall()
    ]


def test_generic_row_for_unaudited_write(audited: tuple[TestClient, Database]) -> None:
    c, db = audited
    r = c.post("/api/v1/_probe/x", headers=OPS)
    assert r.status_code == 200
    rows = _rows(db)
    assert len(rows) == 1
    action, target, actor, rid = rows[0]
    assert (action, target, actor) == ("http.post", "/api/v1/_probe/{item}", "u-ops-1")
    assert rid == r.headers["X-Request-ID"]


def test_no_duplicate_when_endpoint_audits(audited: tuple[TestClient, Database]) -> None:
    c, db = audited
    add_batch(db.write, "orders")
    _sid, rid = seed_scenario(db.write)
    r = c.post("/api/v1/decisions", json={"recommendation_id": rid}, headers=OPS)
    assert r.status_code == 201, r.text
    rows = _rows(db)
    assert [x[0] for x in rows] == ["decision.create"]
    assert rows[0][3] == r.headers["X-Request-ID"]  # bound request id reaches the row


def test_failed_write_not_logged(audited: tuple[TestClient, Database]) -> None:
    c, db = audited
    assert c.post("/api/v1/decisions", json={}, headers={"X-User": "u"}).status_code == 403
    assert c.get("/api/v1/health").status_code == 200
    assert _rows(db) == []
