"""Fixtures for the panel tests: mock-data loaded into the canonical tables by the
real ingest pipeline (``tests.mockdata.load_mock``), panel built once per session."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import duckdb
import pandas as pd
import pytest

from app.panel.build import PanelBuildResult, build_hourly_panel, load_panel
from tests.mockdata import answer_dir, load_mock, require

VN = "Asia/Ho_Chi_Minh"


@dataclass
class Built:
    conn: duckdb.DuckDBPyConnection
    panel: pd.DataFrame
    result: PanelBuildResult
    seconds: float
    size: str

    def answer(self, name: str) -> pd.DataFrame:
        return pd.read_csv(answer_dir(self.size) / name, encoding="utf-8-sig")

    def answer_hourly(self) -> pd.DataFrame:
        gh = self.answer("nhu_cau_theo_gio.csv")
        gh["hour_utc"] = pd.to_datetime(gh["thoi_gian"]).dt.tz_localize(VN).dt.tz_convert("UTC")
        return gh

    def answer_daily(self) -> pd.DataFrame:
        gd = self.answer("nhu_cau_theo_ngay.csv")
        gd["day_local"] = pd.to_datetime(gd["ngay"]).dt.date
        return gd


def _build(size: str, *, live: bool) -> Iterator[Built]:
    require(size)
    conn = duckdb.connect()
    load_mock(conn, size, live=live)
    t0 = time.perf_counter()
    result = build_hourly_panel(conn)
    seconds = time.perf_counter() - t0
    yield Built(conn=conn, panel=load_panel(conn), result=result, seconds=seconds, size=size)
    conn.close()


@pytest.fixture(scope="session")
def nho() -> Iterator[Built]:
    """History of ``nho`` (30 SKUs, 14/07 → 10/11)."""
    yield from _build("nho", live=False)


@pytest.fixture(scope="session")
def nho_live() -> Iterator[Built]:
    """History + the 11.11 day (hourly stock snapshots)."""
    yield from _build("nho", live=True)
