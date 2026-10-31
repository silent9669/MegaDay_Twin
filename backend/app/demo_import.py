"""Read-only 11.11 demo bootstrap around the existing M1/M2/M3 pipeline.

Run from backend/: uv run python -m app.demo_import --serve
History trains the models; only live_11_11 supplies sale-day observations.
All staging, cursors, reports and DB writes stay in a separate output directory.

Two ways to bring in 11.11:

* ``--until <clock>`` (default): the live CSVs are cut at the clock into
  ``<output>/observations`` (ads by their own hour, no reporting lag);
* ``--replay-hour H``: the live folder is written exactly as the demo replay writes it after
  hour ``H`` (``app.live.replay``: orders/stock to ``H:59``, ads ``adsLagHours`` behind) into
  ``<output>/live_stream`` and loaded from there. A server started with
  ``LIVE_DIR=<output>/live_stream`` then continues from the same cursor, and "back to
  01:00" (``reset`` + ``jump 0``) gives back the very same state (``scripts/demo.mjs``).

``--estimate-ads`` runs only the iROAS estimate; ``--models`` runs backtest + fit +
estimate-ads.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

OBSERVATIONS = {
    "don_hang_seller_center.csv": "Ngày đặt hàng",
    "bao_cao_ads.csv": "Thời gian",
    "ton_kho_google_sheet.csv": "Thời điểm cập nhật",
}
REFERENCES = ("gia_von_nhap_tay.csv", "cau_truc_chien_dich.json", "chuong_trinh_sale.json")
VN = ZoneInfo("Asia/Ho_Chi_Minh")


def stage_live(shop: Path, output: Path, until: datetime) -> dict[str, str]:
    """Copy observations up to a demo clock. Source files are never opened for writing."""
    shop, output = shop.resolve(), output.resolve()
    if output == shop or shop in output.parents or output in shop.parents:
        raise ValueError("Output must be separate from the source shop directory.")
    if until.tzinfo is None:
        raise ValueError("Demo clock needs an explicit timezone.")
    paths = [shop / name for name in REFERENCES] + [
        shop / "live_11_11" / name for name in OBSERVATIONS
    ]
    missing = [p.name for p in paths if not p.is_file()]
    if missing:
        raise ValueError(f"Missing source files: {', '.join(missing)}")
    hashes = {str(p.relative_to(shop)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    output.mkdir(parents=True, exist_ok=True)
    for name, clock in OBSERVATIONS.items():
        with (shop / "live_11_11" / name).open(encoding="utf-8-sig", newline="") as src:
            reader = csv.DictReader(src)
            if not reader.fieldnames or clock not in reader.fieldnames:
                raise ValueError(f"{name}: missing {clock}")
            with (output / name).open("w", encoding="utf-8", newline="") as dest:
                writer = csv.DictWriter(dest, fieldnames=reader.fieldnames)
                writer.writeheader()
                for row in reader:
                    tz = UTC if name == "bao_cao_ads.csv" else VN
                    stamp = datetime.fromisoformat(row[clock]).replace(tzinfo=tz)
                    # Ads records represent an hour; include only completed hours.
                    visible = (
                        stamp <= until if name == "ton_kho_google_sheet.csv" else stamp < until
                    )
                    if visible:
                        writer.writerow(row)
    return hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shop", type=Path, default=Path("../mock-data/out/demo"))
    parser.add_argument("--output", type=Path, default=Path("data/hackathon-live"))
    parser.add_argument("--until", default="2026-11-11T01:00:00+07:00")
    parser.add_argument(
        "--replay-hour",
        type=int,
        default=None,
        help="Load 11.11 through the replay folder up to H:59 (replaces --until)",
    )
    parser.add_argument("--models", action="store_true", help="Backtest, forecast and estimate ads")
    parser.add_argument("--estimate-ads", action="store_true", help="Only estimate ads (iROAS)")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    shop, output = args.shop.resolve(), args.output.resolve()
    until = datetime.fromisoformat(args.until)
    if args.replay_hour is not None and not 0 <= args.replay_hour <= 23:
        parser.error("--replay-hour must be 0-23.")
    # A new directory prevents clock rewind or mixing two shops in one cursor/DB.
    if output.exists():
        parser.error("Output already exists; choose a new --output directory. Nothing was reset.")
    from app.live.replay import load_replay, replay_source, write_live_stream

    if args.replay_hour is None:
        hashes = stage_live(shop, output / "observations", until)
        live_dir = output / "observations"
    else:
        source = replay_source(shop)
        load_replay(source)  # fail fast without a replay file
        paths = [shop / name for name in REFERENCES] + [source]
        hashes = {
            p.relative_to(shop).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
        }
        live_dir = output / "live_stream"
        status = write_live_stream(source, live_dir, args.replay_hour)
        day = date.fromisoformat(status["ngay"])
        until = datetime(day.year, day.month, day.day, tzinfo=VN) + timedelta(
            hours=args.replay_hour + 1
        )
    from app.ingest.pipeline import ingest_files, ingest_live_delta
    from app.warehouse import repo
    from app.warehouse.db import Database

    # ASCII only: stdout may be a cp1252 pipe (scripts/demo.mjs on Windows)
    print(f"[demo_import] loading history + 11.11 up to {until.isoformat()}...", flush=True)
    database = Database(output / "demo.duckdb")
    database.migrate()
    repo.seed_bundles(database.write)
    # Explicit six-file history list: no recursive scan, oracle or replay metadata.
    baseline_paths = [shop / name for name in [*OBSERVATIONS, *REFERENCES]]
    baseline = ingest_files(database.write, baseline_paths, build_panel=True)
    if any(f.status == "failed" for f in baseline.files):
        database.close()
        raise RuntimeError("Baseline ingestion failed; inspect pipeline diagnostics.")
    live = ingest_live_delta(database.write, live_dir, build_panel=True)
    report = {
        "shop": shop.name,
        "clock": until.isoformat(),
        "source_sha256": hashes,
        "baseline": baseline.model_dump(mode="json"),
        "live": live.model_dump(mode="json"),
    }
    (output / "import-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    database.close()
    if live.ingest and any(f.status == "failed" for f in live.ingest.files):
        raise RuntimeError("Live ingestion failed; inspect import-report.json.")
    commands = (
        ["backtest", "fit", "estimate-ads"]
        if args.models
        else ["estimate-ads"]
        if args.estimate_ads
        else []
    )
    if commands:
        import subprocess
        import sys
        import time

        for i, command in enumerate(commands, 1):
            # progress line for scripts/demo.mjs (backtest is the slow one: minutes); ASCII only
            print(f"[demo_import] step {i}/{len(commands)}: {command}...", flush=True)
            t0 = time.perf_counter()
            subprocess.run(
                [sys.executable, "-m", "app.cli", command, "--db", str(output / "demo.duckdb")],
                check=True,
            )
            print(f"[demo_import] done {command} in {time.perf_counter() - t0:.0f} s", flush=True)
    print(f"Imported at {until.isoformat()}; report: {output / 'import-report.json'}")
    if args.serve:
        import uvicorn

        from app.core.config import Settings
        from app.main import create_app

        settings = Settings(
            duckdb_path=output / "demo.duckdb", ai_enabled=False, live_enabled=False, app_env="demo"
        )
        uvicorn.run(
            create_app(settings, db=Database(output / "demo.duckdb")),
            host="127.0.0.1",
            port=args.port,
        )


if __name__ == "__main__":
    main()
