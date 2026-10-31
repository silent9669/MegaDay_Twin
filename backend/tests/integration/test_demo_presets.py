import hashlib

from app.demo_presets import build_presets
from tests.mockdata import require


def test_six_monitoring_presets_use_read_only_sources_and_advancing_clock():
    shop = require("nho")
    files = [p for p in (shop / "live_11_11").iterdir() if p.is_file()]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    result = build_presets(shop)
    assert len(result["presets"]) == 6
    assert len(result["source_sha256"]) == 6
    sold = []
    for preset in result["presets"]:
        assert preset["freshness"]["data"]["clock"] == "data"
        assert preset["inventory"]["data"]
        sold.append(sum(r["sold_today_units"] for r in preset["inventory"]["data"]))
    assert sold[0] < sold[-1]
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in before.items())
