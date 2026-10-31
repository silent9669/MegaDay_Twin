"""INF-06 · Code in backend/app/ must never read the answer key of mock-data.

Only backend/eval/ may read ``mock-data/out/*/ground_truth/`` (01 §8, 11 §2).
This test scans every text file under app/ for the directory name (any
spelling) and for imports of the eval package.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
TEXT_SUFFIXES = {".py", ".sql", ".json", ".yaml", ".yml", ".toml", ".txt", ".md", ".csv"}

LEAK = re.compile(r"ground[\s_\-]?truth", re.IGNORECASE)
EVAL_IMPORT = re.compile(r"^\s*(from\s+eval(\.|\s)|import\s+eval\b)", re.MULTILINE)


def _files() -> list[Path]:
    return [p for p in APP_DIR.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES]


def test_app_dir_exists_and_has_code() -> None:
    files = _files()
    assert any(p.name == "main.py" for p in files)


def test_no_ground_truth_reference_in_app() -> None:
    offenders = []
    for path in _files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if LEAK.search(line):
                offenders.append(f"{path.relative_to(APP_DIR.parent)}:{lineno}: {line.strip()}")
    assert not offenders, "app/ references the answer key:\n" + "\n".join(offenders)


def test_app_does_not_import_eval_package() -> None:
    offenders = [
        str(p.relative_to(APP_DIR.parent))
        for p in _files()
        if p.suffix == ".py" and EVAL_IMPORT.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"app/ imports backend/eval: {offenders}"


def test_detector_catches_variants() -> None:
    for s in ["ground_truth", "GROUND_TRUTH", "ground-truth", "groundtruth", "Ground Truth"]:
        assert LEAK.search(s)
