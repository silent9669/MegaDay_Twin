"""Publish read-only monitoring presets using the real six-source pipeline.

uv run python -m app.demo_presets --shop ../mock-data/out/demo --output data/live11-presets.json
History is normalized once; six chronological live deltas reuse its SKU aliases/panel.
The temporary database and staged CSVs never modify the shop or a running database.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.demo_import import OBSERVATIONS, REFERENCES, stage_live
from app.ingest.pipeline import ingest_files, ingest_live_delta
from app.main import create_app
from app.warehouse import repo
from app.warehouse.db import Database

PRESETS = (
    ("opening", "Sale opening", 1),
    ("morning", "Morning demand", 9),
    ("midday", "Midday flash sale", 12),
    ("afternoon", "Afternoon operations", 15),
    ("evening", "Evening demand", 20),
    ("closing", "Closing watch", 23),
)


def build_presets(shop: Path) -> dict:
    shop = shop.resolve()
    with tempfile.TemporaryDirectory(prefix="megaday-presets-") as scratch:
        root = Path(scratch)
        database = Database(root / "demo.duckdb")
        database.migrate()
        repo.seed_bundles(database.write)
        history = ingest_files(
            database.write,
            [shop / name for name in [*OBSERVATIONS, *REFERENCES]],
            build_panel=True,
        )
        if any(f.status == "failed" for f in history.files):
            database.close()
            raise RuntimeError("Historical baseline failed; no presets published.")
        settings = Settings(
            duckdb_path=database.path, ai_enabled=False, live_enabled=False, app_env="demo"
        )
        presets = []
        headers = {"X-User": "demo-publisher", "X-Role": "viewer"}
        with TestClient(create_app(settings, db=database)) as api:
            for key, label, hour in PRESETS:
                clock = datetime.fromisoformat(f"2026-11-11T{hour:02d}:00:00+07:00")
                hashes = stage_live(shop, root / "observations", clock)
                delta = ingest_live_delta(database.write, root / "observations", build_panel=True)
                if delta.ingest and any(f.status == "failed" for f in delta.ingest.files):
                    raise RuntimeError(f"Live delta failed at {key}; no presets published.")

                def get(path: str) -> Any:
                    response = api.get("/api/v1/" + path, headers=headers)
                    response.raise_for_status()
                    return response.json()

                inventory = get("skus?limit=500&include_bundles=false")
                campaigns = get("campaigns?limit=500")
                freshness = get("freshness")
                issues = get("data-quality/issues?limit=500")
                keep = (
                    "sku",
                    "name",
                    "variant",
                    "stock_available",
                    "is_bundle",
                    "sold_today_units",
                    "sold_3h_units",
                    "campaign",
                    "data_labels",
                )
                inventory["data"] = [
                    {k: row[k] for k in keep if k in row} for row in inventory["data"]
                ]
                presets.append(
                    {
                        "id": key,
                        "name": label,
                        "clock": clock.isoformat(),
                        "inventory": inventory,
                        "campaigns": campaigns,
                        "freshness": freshness,
                        "issues": issues,
                        "newRows": delta.new_rows,
                    }
                )
        return {
            "schema": "megaday.monitor-presets.v1",
            "mode": "recorded_demo",
            "shop": shop.name,
            "source_sha256": hashes,
            "presets": presets,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shop", type=Path, default=Path("../mock-data/out/demo"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new export path.")
    output = args.output.resolve()
    if output == args.shop.resolve() or args.shop.resolve() in output.parents:
        parser.error("Output must stay outside the source shop.")
    result = build_presets(args.shop)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"Published {len(result['presets'])} pipeline-generated presets to {output}")


if __name__ == "__main__":
    main()
