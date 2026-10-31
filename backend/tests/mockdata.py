"""Shared test helpers for ``mock-data/out/<size>`` (EVAL-01).

``load_mock(conn, size, live=...)`` loads a size through the **real** pipeline
(``app.ingest.pipeline``): onboarding seed (combo) → ``ingest_files`` on the
history folder → optionally ``ingest_live_delta`` on ``live_11_11/`` (the whole
11.11 day). The panel is not built here so callers can time it.

Only tests and ``backend/eval`` may read the answer key (``answer_dir``).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from app.ingest.pipeline import IngestReport, LiveDeltaReport, ingest_files, ingest_live_delta
from app.warehouse import repo
from app.warehouse.db import migrate

REPO_DIR = Path(__file__).resolve().parents[2]
MOCK_OUT = REPO_DIR / "mock-data" / "out"


def mock_dir(size: str) -> Path:
    return MOCK_OUT / size


def answer_dir(size: str) -> Path:
    return MOCK_OUT / size / "ground_truth"


def require(size: str) -> Path:
    path = mock_dir(size)
    if not (path / "don_hang_seller_center.csv").exists():
        pytest.skip(f"mock-data/out/{size} chưa được sinh")
    return path


def load_mock(
    conn: duckdb.DuckDBPyConnection,
    size: str = "nho",
    *,
    live: bool = False,
    build_panel: bool = False,
) -> tuple[IngestReport, LiveDeltaReport | None]:
    """History (and the 11.11 day when ``live``) through the real pipeline."""
    base = require(size)
    migrate(conn)
    repo.seed_bundles(conn)  # onboarding: combo components (WH-02)
    report = ingest_files(conn, base, build_panel=build_panel)
    delta = ingest_live_delta(conn, base / "live_11_11", build_panel=build_panel) if live else None
    return report, delta
