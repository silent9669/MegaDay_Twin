"""DuckDB access: one write connection per process, cheap read cursors.

Plan: docs/dev-plan/01-kien-truc-va-cong-nghe.md §3 ("DuckDB chỉ cho phép một
tiến trình ghi") and 02 §4.

* ``get_db()`` returns the process-wide :class:`Database` (path from settings).
* ``db.write`` is the single write connection. Every writer (ingest, panel,
  forecast, governance…) goes through it; keep writes short.
* ``with db.reader() as cur:`` gives a per-request cursor on the same database
  (DuckDB cursors are independent connections sharing the instance).
* External scripts (eval, notebooks) must open their own
  ``duckdb.connect(path, read_only=True)`` while the backend is stopped.
* ``migrate()`` is idempotent: schema.sql only uses ``IF NOT EXISTS``; later
  changes are appended to :data:`MIGRATIONS` and recorded in ``schema_version``.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.timeutil import utcnow

log = get_logger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_BASELINE_VERSION = 1

# (version, note, sql). Append only; never edit an applied entry.
# Example: (2, "add foo to skus", "ALTER TABLE skus ADD COLUMN IF NOT EXISTS foo VARCHAR")
MIGRATIONS: list[tuple[int, str, str]] = [
    (
        2,
        "model_scores.wape_floor (M2, 03 §7.3)",
        "ALTER TABLE model_scores ADD COLUMN IF NOT EXISTS wape_floor DOUBLE",
    ),
    (
        3,
        "scenarios.request, recommendations.what_if (sửa lỗi nhỏ M3)",
        "ALTER TABLE scenarios ADD COLUMN IF NOT EXISTS request JSON;"
        " ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS what_if BOOLEAN DEFAULT FALSE;"
        # older what-if runs (with overrides) superseded the standard one: mark them, then
        # give each campaign back its latest standard recommendation
        " UPDATE recommendations SET what_if = TRUE WHERE scenario_id IN (SELECT scenario_id"
        "  FROM scenarios WHERE coalesce(CAST(overrides AS VARCHAR), '{}') NOT IN ('{}', 'null'));"
        " UPDATE recommendations SET what_if = FALSE WHERE what_if IS NULL;"
        " UPDATE recommendations SET superseded_by = NULL WHERE recommendation_id IN ("
        "  SELECT arg_max(recommendation_id, created_at) FROM recommendations"
        "  WHERE NOT what_if GROUP BY campaign_id)",
    ),
    (
        4,
        "alerts dedupe/ack/resolve columns, actions.note (M4 quản trị, GOV-01)",
        "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR;"
        " ALTER TABLE alerts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;"
        " ALTER TABLE alerts ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;"
        " ALTER TABLE alerts ADD COLUMN IF NOT EXISTS occurrences INTEGER DEFAULT 1;"
        " ALTER TABLE alerts ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ;"
        " ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolved_by VARCHAR;"
        " ALTER TABLE actions ADD COLUMN IF NOT EXISTS note VARCHAR;"
        " ALTER TABLE actions ADD COLUMN IF NOT EXISTS verified_by VARCHAR",
    ),
    (
        5,
        "immutable scenario calculation traces and result snapshots",
        "CREATE TABLE IF NOT EXISTS scenario_calculation_traces ("
        " scenario_id VARCHAR PRIMARY KEY, trace JSON NOT NULL, results JSON NOT NULL)",
    ),
]

_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.IGNORECASE)


def schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


def expected_tables() -> list[str]:
    """Table names declared in schema.sql, in file order."""
    return _TABLE_RE.findall(schema_sql())


def _prepare(conn: duckdb.DuckDBPyConnection) -> None:
    # TIMESTAMPTZ values come back as UTC-aware datetimes (01 §7).
    conn.execute("SET TimeZone = 'UTC'")


def migrate(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Create every table and apply pending migrations. Safe to call repeatedly.
    Returns the list of tables present afterwards."""
    _prepare(conn)
    conn.execute(schema_sql())
    applied = {int(row[0]) for row in conn.execute("SELECT version FROM schema_version").fetchall()}
    if SCHEMA_BASELINE_VERSION not in applied:
        conn.execute(
            "INSERT INTO schema_version VALUES (?, ?, ?)",
            [SCHEMA_BASELINE_VERSION, utcnow(), "baseline schema.sql (M0)"],
        )
    for version, note, sql in sorted(MIGRATIONS):
        if version in applied:
            continue
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(sql)
            conn.execute("INSERT INTO schema_version VALUES (?, ?, ?)", [version, utcnow(), note])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        log.info("migration_applied", version=version, note=note)
    return list_tables(conn)


def list_tables(conn: duckdb.DuckDBPyConnection) -> list[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' ORDER BY table_name"
    ).fetchall()
    return [str(r[0]) for r in rows]


class Database:
    """Owns the single write connection for one DuckDB file."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._lock = threading.Lock()

    @property
    def in_memory(self) -> bool:
        return str(self.path) == ":memory:"

    @property
    def write(self) -> duckdb.DuckDBPyConnection:
        with self._lock:
            if self._conn is None:
                if not self.in_memory:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                self._conn = duckdb.connect(str(self.path))
                _prepare(self._conn)
            return self._conn

    @contextmanager
    def reader(self) -> Iterator[duckdb.DuckDBPyConnection]:
        cur = self.write.cursor()
        _prepare(cur)
        try:
            yield cur
        finally:
            cur.close()

    def migrate(self) -> list[str]:
        return migrate(self.write)

    def ping(self) -> bool:
        try:
            with self.reader() as cur:
                row = cur.execute("SELECT 1").fetchone()
            return row is not None and row[0] == 1
        except duckdb.Error:
            return False

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def reset(self) -> list[str]:
        """Delete the database file (and WAL) then migrate from scratch."""
        self.close()
        if not self.in_memory:
            for p in (self.path, self.path.with_name(self.path.name + ".wal")):
                p.unlink(missing_ok=True)
        return self.migrate()


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    with _db_lock:
        if _db is None:
            _db = Database(get_settings().duckdb_path)
        return _db


def set_db(db: Database | None) -> None:
    """Swap the process database (tests, CLI ``--db``). Closes the previous one."""
    global _db
    with _db_lock:
        if _db is not None and _db is not db:
            _db.close()
        _db = db
