"""Panel on the full ``demo`` size (300 SKUs): plan 02 §6.4 targets < 20 s."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from tests.unit.panel.conftest import Built, _build

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def demo() -> Iterator[Built]:
    yield from _build("demo", live=False)


def test_demo_panel_fast_and_correct(demo: Built) -> None:
    p = demo.panel
    assert p["sku_id"].nunique() == 300 and len(p) == 300 * 2880
    assert demo.seconds < 20, demo.seconds
    daily = p.groupby(["sku_id", "day_local"])["units_gross"].sum().rename("ours").reset_index()
    gd = demo.answer_daily().merge(
        daily, left_on=["sku", "day_local"], right_on=["sku_id", "day_local"]
    )
    final = gd[gd["day_local"] <= date(2026, 11, 8)]
    assert (final["ours"] == final["ban_rong"]).all()
    assert abs(gd["ours"].sum() - gd["ban_rong"].sum()) <= 0.01 * gd["ban_rong"].sum()
    eod = p[p["hour_local"] == 23][["sku_id", "day_local", "stock_end"]]
    m = demo.answer_daily().merge(
        eod, left_on=["sku", "day_local"], right_on=["sku_id", "day_local"]
    )
    m = m[m["ton_cuoi_ngay"].notna()]
    assert (m["stock_end"] != m["ton_cuoi_ngay"]).sum() <= 3
