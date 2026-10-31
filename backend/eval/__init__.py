"""Scoring harness against the mock-data answer key (docs/dev-plan/11 §2).

This is the ONLY place allowed to read ``mock-data/out/<size>/ground_truth/``.
Code here may import ``app``; ``app`` must never import ``eval``
(enforced by tests/unit/test_no_ground_truth_leak.py).
"""

from __future__ import annotations

from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent
REPO_DIR = BACKEND_DIR.parent
REPORTS_DIR = EVAL_DIR / "reports"


def mock_dir(size: str = "demo") -> Path:
    return REPO_DIR / "mock-data" / "out" / size


def ground_truth_dir(size: str = "demo") -> Path:
    return mock_dir(size) / "ground_truth"
