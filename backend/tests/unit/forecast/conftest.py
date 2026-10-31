"""Fixtures for the forecast tests.

* ``loaded_db`` (shared, ``tests/conftest.py``, read-only): ``nho`` history via the real
  pipeline + ``hourly_panel``.
* ``nho_live`` (here, writable): ``nho`` history **and** the 11.11 day
  (``live_11_11``) through the real pipeline, panel built, its own DuckDB — the forecast
  layer writes ``skus.demand_group``, ``model_registry``, ``model_scores``, ``forecasts``.
* ``demo_live``: same for ``demo`` (``e2e`` marker).
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime

import duckdb
import pandas as pd
import pytest

from app.forecast import service
from app.forecast.backtest import BacktestSpec, default_specs
from app.forecast.data import PanelMatrix, vn_midnight_utc
from app.panel.build import build_hourly_panel
from tests.mockdata import answer_dir, load_mock

ORIGIN_1111 = vn_midnight_utc(date(2026, 11, 11))
VN = "Asia/Ho_Chi_Minh"


@dataclass
class Live:
    conn: duckdb.DuckDBPyConnection
    matrix: PanelMatrix
    size: str

    def answer(self, name: str) -> pd.DataFrame:
        return pd.read_csv(
            answer_dir(self.size) / name, encoding="utf-8-sig", keep_default_na=False
        )


def _live(size: str) -> Iterator[Live]:
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    conn = duckdb.connect()
    load_mock(conn, size, live=True)
    build_hourly_panel(conn)
    yield Live(conn=conn, matrix=service.load_matrix(conn), size=size)
    conn.close()


@pytest.fixture(scope="session")
def nho_live() -> Iterator[Live]:
    yield from _live("nho")


@pytest.fixture(scope="session")
def demo_live() -> Iterator[Live]:
    yield from _live("demo")


@pytest.fixture(scope="session")
def nho_matrix(loaded_db: duckdb.DuckDBPyConnection) -> PanelMatrix:
    """History only (read-only shared DB)."""
    return service.load_matrix(loaded_db)


def quick_specs() -> list[BacktestSpec]:
    """dd_0909 + dd_1010 + 3 days of normal_roll: enough to score and pick champions."""
    out = []
    for s in default_specs():
        if s.name in ("dd_0909", "dd_1010"):
            out.append(s)
        elif s.name == "normal_roll":
            out.append(BacktestSpec(s.name, s.origins[:3], s.horizon_hours, s.context))
    return out


@pytest.fixture(scope="session")
def nho_scored(nho_live: Live) -> Live:
    """``nho_live`` after a (quick) backtest written to ``model_scores``."""
    service.run_backtests(nho_live.conn, specs=quick_specs(), matrix=nho_live.matrix)
    return nho_live


def at_local(text: str) -> datetime:
    return pd.Timestamp(text, tz=VN).tz_convert("UTC").to_pydatetime()
