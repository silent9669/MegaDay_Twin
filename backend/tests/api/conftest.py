"""API contract tests (API-01/03/04/05, part of EVAL-04): ``TestClient`` on a copy of the
shared ``nho`` database (``tests/conftest.py::_loaded_nho``, never written), so endpoints
that write (scenarios, goals, parameters) do not touch the shared fixture."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from app.ingest.pipeline import IngestReport
from tests.api.helpers import make_client


@pytest.fixture(scope="session")
def nho_db_file(
    _loaded_nho: tuple[duckdb.DuckDBPyConnection, IngestReport, Path],
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Closed snapshot of the shared ``nho`` database (``COPY FROM DATABASE`` only reads
    the source; a plain file copy would race with the open connection on Windows)."""
    conn, _report, _path = _loaded_nho
    dest = tmp_path_factory.mktemp("api_template") / "nho.duckdb"
    name = conn.execute("SELECT current_database()").fetchone()[0]  # type: ignore[index]
    conn.execute(f"ATTACH '{dest.as_posix()}' AS api_template")
    try:
        conn.execute(f'COPY FROM DATABASE "{name}" TO api_template')
    finally:
        conn.execute("DETACH api_template")
    return dest


@pytest.fixture(scope="module")
def api(nho_db_file: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """Client on a private copy of ``nho`` (one per test module)."""
    client, db = make_client(nho_db_file, tmp_path_factory.mktemp("api"))
    with client as c:
        yield c
    db.close()
