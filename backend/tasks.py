"""Cross-platform dev tasks (Windows has no `make` by default).

    uv run python tasks.py <task> [<task> ...]

Tasks: install, lint, fmt, typecheck, test, check, migrate, db-reset, dev, serve.
`backend/Makefile` is a thin wrapper over this file for people who have make.
Each task is a list of commands run from backend/; the first failure stops.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
PY = sys.executable

TASKS: dict[str, tuple[str, list[list[str]]]] = {
    "install": ("uv sync (cài phụ thuộc)", [["uv", "sync"]]),
    "lint": (
        "ruff check + kiểm định dạng",
        [[PY, "-m", "ruff", "check"], [PY, "-m", "ruff", "format", "--check"]],
    ),
    "fmt": (
        "tự sửa lint + định dạng",
        [[PY, "-m", "ruff", "check", "--fix"], [PY, "-m", "ruff", "format"]],
    ),
    "typecheck": ("mypy app", [[PY, "-m", "mypy", "app"]]),
    "test": ("pytest", [[PY, "-m", "pytest"]]),
    "migrate": ("tạo/cập nhật bảng DuckDB", [[PY, "-m", "app.cli", "migrate"]]),
    "db-reset": ("xóa và tạo lại DuckDB", [[PY, "-m", "app.cli", "reset", "--yes"]]),
    "dev": (
        "API có tự nạp lại, cổng 8000",
        [[PY, "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"]],
    ),
    "serve": ("API không reload", [[PY, "-m", "uvicorn", "app.main:app", "--port", "8000"]]),
}
# Composite: same order as CI (.github/workflows/backend.yml).
COMPOSITE: dict[str, list[str]] = {"check": ["lint", "typecheck", "test"]}


def _usage() -> int:
    print(__doc__)
    for name, (desc, _) in TASKS.items():
        print(f"  {name:10s} {desc}")
    for name, parts in COMPOSITE.items():
        print(f"  {name:10s} {' + '.join(parts)}")
    return 2


def run(name: str) -> int:
    if name in COMPOSITE:
        for part in COMPOSITE[name]:
            code = run(part)
            if code:
                return code
        return 0
    if name not in TASKS:
        print(f"Không có task '{name}'.", file=sys.stderr)
        return _usage()
    for cmd in TASKS[name][1]:
        print(f"$ {' '.join(Path(c).name if c == PY else c for c in cmd)}", flush=True)
        code = subprocess.call(cmd, cwd=BACKEND)
        if code:
            return code
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        return _usage()
    for name in argv:
        code = run(name)
        if code:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
