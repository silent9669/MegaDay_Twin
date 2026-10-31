from pathlib import Path

from fastapi.testclient import TestClient

from app.demo_import import main
from tests.mockdata import require


def test_demo_launcher_serves_its_isolated_database(tmp_path: Path, monkeypatch) -> None:
    shop = require("nho")
    output = tmp_path / "isolated"
    monkeypatch.setattr(
        "sys.argv", ["demo_import", "--shop", str(shop), "--output", str(output), "--serve"]
    )
    import uvicorn

    def inspect_server(app, **_kwargs):
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/skus?limit=1", headers={"X-User": "test", "X-Role": "viewer"}
            )
            assert response.status_code == 200
            assert len(response.json()["data"]) == 1
            assert app.state.db.path == output / "demo.duckdb"
            assert response.json()["meta"]["data_as_of"].startswith("2026-11-11T01:")

    monkeypatch.setattr(uvicorn, "run", inspect_server)
    main()
    assert (output / "import-report.json").exists()


def test_replay_hour_loads_the_demo_live_folder(tmp_path: Path, monkeypatch) -> None:
    """``--replay-hour 0``: 11.11 comes in through ``<output>/live_stream`` exactly as the demo
    replay writes it, so a server watching that folder continues from the same cursor."""
    import json

    import duckdb

    from app.api.common import source_as_of
    from app.core.timeutil import to_local
    from app.ingest.pipeline import ingest_live_delta
    from app.live.replay import replay_source, write_live_stream

    shop = require("nho")
    output = tmp_path / "pitch"
    monkeypatch.setattr(
        "sys.argv",
        ["demo_import", "--shop", str(shop), "--output", str(output), "--replay-hour", "0"],
    )
    main()
    report = json.loads((output / "import-report.json").read_text())
    assert report["clock"] == "2026-11-11T01:00:00+07:00"
    assert "live_11_11/_replay.json" in report["source_sha256"]
    assert (output / "live_stream" / "trang_thai.json").exists()
    assert not (output / "observations").exists()
    conn = duckdb.connect(str(output / "demo.duckdb"))
    try:
        orders_as_of = source_as_of(conn)["orders"][0]
        assert orders_as_of is not None
        assert to_local(orders_as_of).isoformat() == "2026-11-11T01:00:00+07:00"
        # the next replay hour is an increment of the same folder, not a restart
        write_live_stream(replay_source(shop), output / "live_stream", 3)
        delta = ingest_live_delta(conn, output / "live_stream", build_panel=False)
        assert delta.status == "loaded" and not delta.restarted
    finally:
        conn.close()
