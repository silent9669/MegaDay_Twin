"""Typed read/write helpers over DuckDB. Plan: 02 §1 (``warehouse.upsert``), WH-01/WH-02.

Generic helpers
---------------
* :func:`upsert_rows` / :func:`upsert_df` — idempotent upsert on the table's
  natural key: ``INSERT … ON CONFLICT (key) DO UPDATE SET <non-key> = excluded.*``.
  Rows with the same key inside one call are de-duplicated (last wins), because
  DuckDB rejects a single INSERT that hits the same key twice.
* :func:`fetch_all` / :func:`fetch_one` — rows as ``dict``.
* ``dict`` values are serialised to JSON text; ``list`` values are passed as
  DuckDB lists (``VARCHAR[]``). For a JSON column holding a list, wrap it with
  :func:`as_json`.

Domain helpers
--------------
* :func:`ensure_sku` — minimal product+SKU row (used by bundle declaration
  before orders are ingested; ingest later overwrites names/prices).
* :func:`declare_bundle`, :func:`get_bundle_components`, :func:`seed_bundles` —
  combo declaration (``bundle_components``, 02 §2.3). Seed file:
  ``app/warehouse/seeds/bundles.json``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from pydantic import BaseModel, Field

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SEEDS_DIR = Path(__file__).with_name("seeds")
BUNDLE_SEED_PATH = SEEDS_DIR / "bundles.json"


class JsonText(str):
    """Marker: value is already JSON text for a JSON column."""

    __slots__ = ()


def as_json(value: Any) -> JsonText:
    return JsonText(json.dumps(value, ensure_ascii=False, default=str))


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return f'"{name}"'


def _param(value: Any) -> Any:
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def _conflict_clause(columns: Sequence[str], key: Sequence[str]) -> str:
    keys = ", ".join(_ident(k) for k in key)
    updates = [c for c in columns if c not in key]
    if not updates:
        return f"ON CONFLICT ({keys}) DO NOTHING"
    sets = ", ".join(f"{_ident(c)} = excluded.{_ident(c)}" for c in updates)
    return f"ON CONFLICT ({keys}) DO UPDATE SET {sets}"


def upsert_rows(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    rows: Iterable[Mapping[str, Any]],
    key: Sequence[str],
) -> int:
    """Upsert dict rows. All rows must have the same keys. Returns rows written."""
    unique: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    columns: list[str] | None = None
    for row in rows:
        if columns is None:
            columns = list(row.keys())
            missing = [k for k in key if k not in row]
            if missing:
                raise ValueError(f"rows lack key columns {missing}")
        elif list(row.keys()) != columns:
            raise ValueError("all rows must have the same columns in the same order")
        unique[tuple(row[k] for k in key)] = row
    if columns is None:
        return 0
    cols_sql = ", ".join(_ident(c) for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = (
        f"INSERT INTO {_ident(table)} ({cols_sql}) VALUES ({placeholders}) "
        f"{_conflict_clause(columns, key)}"
    )
    params = [[_param(r[c]) for c in columns] for r in unique.values()]
    conn.executemany(sql, params)
    return len(params)


def upsert_df(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    df: pd.DataFrame,
    key: Sequence[str],
) -> int:
    """Bulk upsert a DataFrame whose column names match the table. Fast path for
    ingest/panel (``INSERT … SELECT FROM df``)."""
    if df.empty:
        return 0
    columns = [str(c) for c in df.columns]
    missing = [k for k in key if k not in columns]
    if missing:
        raise ValueError(f"DataFrame lacks key columns {missing}")
    frame = df.drop_duplicates(subset=list(key), keep="last")
    view = "_upsert_src"
    cols_sql = ", ".join(_ident(c) for c in columns)
    conn.register(view, frame)
    try:
        conn.execute(
            f"INSERT INTO {_ident(table)} ({cols_sql}) SELECT {cols_sql} FROM {view} "
            f"{_conflict_clause(columns, key)}"
        )
    finally:
        conn.unregister(view)
    return len(frame)


def fetch_all(
    conn: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] | None = None
) -> list[dict[str, Any]]:
    cur = conn.execute(sql, list(params) if params is not None else [])
    names = [d[0] for d in cur.description or []]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def fetch_one(
    conn: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] | None = None
) -> dict[str, Any] | None:
    rows = fetch_all(conn, sql, params)
    return rows[0] if rows else None


def table_columns(conn: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    return [str(r[0]) for r in rows]


def count_rows(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {_ident(table)}").fetchone()
    return int(row[0]) if row else 0


# ============================================================== SKUs & bundles
def derive_product_id(sku_id: str, *, is_bundle: bool = False) -> str:
    """02 §4: product_id = platform SKU prefix before the first '-'.
    Bundles keep their full SKU (``COMBO-NOICHIEN`` would otherwise collapse to
    ``COMBO`` and merge every combo into one product)."""
    if is_bundle:
        return sku_id
    return sku_id.split("-", 1)[0]


def ensure_sku(
    conn: duckdb.DuckDBPyConnection,
    sku_id: str,
    *,
    is_bundle: bool = False,
    product_id: str | None = None,
) -> None:
    """Insert a placeholder product + SKU if absent; never overwrites ingest data
    (except raising ``is_bundle`` to TRUE)."""
    pid = product_id or derive_product_id(sku_id, is_bundle=is_bundle)
    conn.execute(
        "INSERT INTO products (product_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
        [pid, pid],
    )
    conn.execute(
        "INSERT INTO skus (sku_id, product_id, is_bundle) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
        [sku_id, pid, is_bundle],
    )
    if is_bundle:
        conn.execute(
            "UPDATE skus SET is_bundle = TRUE WHERE sku_id = ? AND NOT is_bundle", [sku_id]
        )


class BundleComponent(BaseModel):
    sku_id: str
    qty: int = Field(default=1, ge=1)


class BundleDecl(BaseModel):
    bundle_sku_id: str
    components: list[BundleComponent] = Field(min_length=1)


def declare_bundle(conn: duckdb.DuckDBPyConnection, decl: BundleDecl) -> int:
    """Replace the component list of one bundle (idempotent). Returns #components."""
    comp_ids = [c.sku_id for c in decl.components]
    if decl.bundle_sku_id in comp_ids:
        raise ValueError("a bundle cannot contain itself")
    if len(set(comp_ids)) != len(comp_ids):
        raise ValueError("duplicate component sku")
    conn.execute("BEGIN TRANSACTION")
    try:
        ensure_sku(conn, decl.bundle_sku_id, is_bundle=True)
        for c in decl.components:
            ensure_sku(conn, c.sku_id)
        conn.execute("DELETE FROM bundle_components WHERE bundle_sku_id = ?", [decl.bundle_sku_id])
        conn.executemany(
            "INSERT INTO bundle_components (bundle_sku_id, component_sku_id, qty) VALUES (?, ?, ?)",
            [[decl.bundle_sku_id, c.sku_id, c.qty] for c in decl.components],
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(decl.components)


def get_bundle_components(
    conn: duckdb.DuckDBPyConnection, bundle_sku_id: str | None = None
) -> dict[str, list[BundleComponent]]:
    sql = "SELECT bundle_sku_id, component_sku_id, qty FROM bundle_components"
    params: list[Any] = []
    if bundle_sku_id is not None:
        sql += " WHERE bundle_sku_id = ?"
        params.append(bundle_sku_id)
    sql += " ORDER BY bundle_sku_id, component_sku_id"
    out: dict[str, list[BundleComponent]] = {}
    for b, c, q in conn.execute(sql, params).fetchall():
        out.setdefault(str(b), []).append(BundleComponent(sku_id=str(c), qty=int(q)))
    return out


def load_bundle_seed(path: Path = BUNDLE_SEED_PATH) -> list[BundleDecl]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [BundleDecl.model_validate(item) for item in raw["bundles"]]


def seed_bundles(conn: duckdb.DuckDBPyConnection, path: Path = BUNDLE_SEED_PATH) -> int:
    """Apply the onboarding bundle declarations (WH-02). Idempotent."""
    return sum(declare_bundle(conn, decl) for decl in load_bundle_seed(path))
