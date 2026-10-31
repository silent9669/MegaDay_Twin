from datetime import datetime
from pathlib import Path

import pytest

from app.demo_import import OBSERVATIONS, REFERENCES, stage_live


def test_stage_preserves_sources_and_respects_ads_utc(tmp_path: Path) -> None:
    shop = tmp_path / "shop"
    (shop / "live_11_11").mkdir(parents=True)
    for name in REFERENCES:
        (shop / name).write_text("reference")
    for name, clock in OBSERVATIONS.items():
        values = (
            ["2026-11-10 17:00", "2026-11-10 18:00"]
            if name == "bao_cao_ads.csv"
            else ["2026-11-11 00:59", "2026-11-11 01:01"]
        )
        (shop / "live_11_11" / name).write_text(clock + "\n" + "\n".join(values), encoding="utf-8")
    before = {p: p.read_bytes() for p in shop.rglob("*") if p.is_file()}
    out = tmp_path / "staged"
    hashes = stage_live(shop, out, datetime.fromisoformat("2026-11-11T01:00:00+07:00"))
    assert len(hashes) == 6
    ads = (out / "bao_cao_ads.csv").read_text(encoding="utf-8").splitlines()
    assert ads == ["Thời gian", "2026-11-10 17:00"]
    orders = (out / "don_hang_seller_center.csv").read_text(encoding="utf-8").splitlines()
    assert len(orders) == 2
    assert all(p.read_bytes() == b for p, b in before.items())
    with pytest.raises(ValueError, match="separate"):
        stage_live(shop, shop / "unsafe", datetime.now().astimezone())
