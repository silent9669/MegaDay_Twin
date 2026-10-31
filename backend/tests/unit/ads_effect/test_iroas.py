"""Labels, break-even, storage, CLI, and the real pipeline on ``nho`` (ADS-04, ADS-06)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pytest
from typer.testing import CliRunner

from app.ads_effect.dataset import CampaignHours
from app.ads_effect.iroas import (
    breakeven_of,
    estimate_all,
    iroas_label,
    load_estimate,
    load_estimates,
    prior_estimate,
    write_estimates,
)
from app.ads_effect.schemas import EXCLUSION_REASONS
from app.cli import cli
from app.warehouse.db import migrate, set_db


def test_label_rules() -> None:
    assert iroas_label(11, 0.1, True) == "scenario"
    assert iroas_label(12, 0.1, True) == "reference"
    assert iroas_label(48, 0.6, True) == "verified"
    assert iroas_label(48, 0.61, True) == "reference"
    assert iroas_label(500, 0.2, False) == "reference"


def _one_sku(sku: str, units: float, price: float) -> CampaignHours:
    import pandas as pd

    z = np.zeros(1)
    return CampaignHours(
        campaign_id="C",
        hours=pd.DatetimeIndex([pd.Timestamp("2026-11-01", tz="UTC")]),
        day=z.astype(np.int64),
        hour_local=z.astype(np.int64),
        ctx=z.astype(np.int64),
        y=z,
        revenue=z,
        spend=z,
        status=np.array(["running"]),
        keep=np.array([True]),
        reason=np.array([""]),
        sku_ids=[sku],
        sku_units=np.array([units]),
        sku_revenue=np.array([units * price]),
        sku_shares={sku: 1.0},
    )


def test_breakeven_coc_meme_example() -> None:
    """04 §1: 89.000đ, cost 32.000đ, fee 12 %, packing 3.000đ → break-even iROAS ≈ 2,05."""
    ch = _one_sku("COCMEME-A", 100, 89_000)
    be = breakeven_of(ch, {"COCMEME-A": (32_000.0, 3_000.0)}, 0.12)
    assert be == pytest.approx(89_000 / 43_320, rel=1e-6)
    assert round(be, 2) == 2.05
    assert breakeven_of(ch, {}, 0.12) is None  # no cost → no break-even
    assert breakeven_of(ch, {"COCMEME-A": (90_000.0, 0.0)}, 0.12) is None  # loses per unit


def test_write_and_load_roundtrip() -> None:
    conn = duckdb.connect(":memory:")
    migrate(conn)
    e = prior_estimate("C9", spend_ref=5000.0, sku_shares={"A": 0.7, "B": 0.3})
    e = e.model_copy(update={"breakeven_iroas": 2.5, "flags": ["X"], "adstock_last": 12.5})
    assert write_estimates(conn, [e]) == 1
    back = load_estimate(conn, "C9")
    assert back is not None
    assert back.model_dump(exclude={"created_at"}) == e.model_dump(exclude={"created_at"})
    # same campaign / model / period again → replaced, not duplicated
    write_estimates(conn, [e.model_copy(update={"estimate_id": "iroas-2"})])
    assert conn.execute("SELECT count(*) FROM iroas_estimates").fetchall() == [(1,)]
    assert list(load_estimates(conn)) == ["C9"]


def test_cli_estimate_ads_on_empty_db(tmp_path: Path) -> None:
    try:
        r = CliRunner().invoke(cli, ["estimate-ads", "--db", str(tmp_path / "e.duckdb")])
    finally:
        set_db(None)
    assert r.exit_code == 0, r.output
    assert "Không có chiến dịch" in r.output


# ---------------------------------------------------------------------- nho (real pipeline)
@pytest.mark.integration
def test_estimate_all_on_nho(loaded_db: duckdb.DuckDBPyConnection) -> None:
    run = estimate_all(loaded_db)
    ests = {e.campaign_id: e for e in run.estimates}
    n_campaigns = loaded_db.execute("SELECT count(*) FROM campaigns").fetchall()[0][0]
    assert len(ests) == n_campaigns
    assert run.delta in (0.5, 0.6, 0.7, 0.8)
    assert run.seconds < 10
    for e in ests.values():
        assert set(e.excluded_hours) == set(EXCLUSION_REASONS)
        assert e.excluded_hours["launch"] >= 72
        assert e.iroas_p10 <= e.iroas_mean <= e.iroas_p90
        assert abs(sum(c.w for c in e.posterior) - 1) < 1e-3
        assert abs(sum(e.sku_shares.values()) - 1) < 1e-6
        assert e.platform_roas is not None and e.iroas_mean < e.platform_roas * 1.5
        assert e.reasons and e.confidence in ("verified", "reference", "scenario")
        u = e.uplift(np.array([0.0, e.spend_ref, 10 * e.spend_ref]))
        assert u[0] == 0 and u[1] < u[2]
    # the new vacuum cleaner (CD006, launched 7 days before 11.11) has almost no clean off-hours
    assert ests["CD006"].confidence == "scenario"
    # the meme mug campaign loses money: iROAS under its break-even
    cd004 = ests["CD004"]
    assert cd004.breakeven_iroas is not None
    # the hot-pot campaign: incremental far below what the platform reports
    cd002 = ests["CD002"]
    assert cd002.platform_roas is not None and cd002.iroas_mean < cd002.platform_roas

    conn = duckdb.connect(":memory:")
    migrate(conn)
    assert write_estimates(conn, run.estimates) == len(ests)
    back = load_estimates(conn)
    assert back["CD002"].iroas_mean == pytest.approx(cd002.iroas_mean)
