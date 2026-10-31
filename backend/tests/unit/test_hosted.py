from __future__ import annotations

import io
import json
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import Mock

import duckdb
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.hosted import (
    DurableResponses,
    SnapshotStore,
    hosted_app,
    restore_archive,
    snapshot_archive,
    valid_member,
)
from app.warehouse.db import Database


def seed(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    db = Database(root / "megaday.duckdb")
    db.migrate()
    db.write.execute("CREATE TABLE hosted_probe (value VARCHAR)")
    db.write.execute("INSERT INTO hosted_probe VALUES ('goal and replay checkpoint')")
    db.close()
    replay = root / "megaday-replay" / "_replay.json"
    replay.parent.mkdir()
    replay.write_text('{"ngay":"2026-11-11"}', encoding="utf-8")
    live = root / "live" / "_status.json"
    live.parent.mkdir()
    live.write_text('{"hour":3}', encoding="utf-8")


def test_restart_restores_database_and_replay_files(tmp_path: Path) -> None:
    original, restarted = tmp_path / "original", tmp_path / "restarted"
    seed(original)
    raw = snapshot_archive(original)
    restore_archive(raw, restarted)
    with duckdb.connect(str(restarted / "megaday.duckdb")) as conn:
        assert conn.execute("SELECT value FROM hosted_probe").fetchone() == (
            "goal and replay checkpoint",
        )
    assert json.loads((restarted / "live/_status.json").read_text()) == {"hour": 3}
    assert (restarted / "megaday-replay/_replay.json").read_bytes() == (
        original / "megaday-replay/_replay.json"
    ).read_bytes()


@pytest.mark.parametrize("name", ["../escape", "/escape", "live/../../escape"])
def test_bad_snapshot_never_touches_existing_state(tmp_path: Path, name: str) -> None:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("snapshot.json", '{"version":1}')
        archive.writestr("megaday.duckdb", b"bad")
        item = zipfile.ZipInfo(name)
        item.filename = name  # ZipInfo normalizes backslashes on Windows.
        archive.writestr(item, b"bad")
    existing = tmp_path / "megaday.duckdb"
    existing.write_bytes(b"keep")
    with pytest.raises(ValueError, match="Unsafe"):
        restore_archive(data.getvalue(), tmp_path)
    assert existing.read_bytes() == b"keep"


def test_snapshot_rejects_backslashes_before_path_resolution() -> None:
    assert not valid_member("live\\escape")
    assert not valid_member("")


def test_storage_failure_does_not_start_empty(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path, "http://state.internal/state")
    store.http = Mock()
    store.http.open.side_effect = urllib.error.HTTPError(store.url, 503, "unavailable", {}, None)
    with pytest.raises(urllib.error.HTTPError):
        store.restore()
    store.http.open.side_effect = urllib.error.HTTPError(store.url, 404, "absent", {}, None)
    assert store.restore() is False


def test_write_ack_waits_for_persistence_and_failure_blocks_writes(tmp_path: Path) -> None:
    import asyncio

    events: list[str] = []
    app = FastAPI()

    @app.post("/write")
    def write() -> dict[str, bool]:
        events.append("write")
        return {"ok": True}

    wrapper = DurableResponses(app, app, SnapshotStore(tmp_path, "unused"), asyncio.Lock())

    async def persist() -> None:
        events.append("persist")
        wrapper.failed = True
        raise OSError("R2 unavailable")

    wrapper.persist = persist  # type: ignore[method-assign]
    with TestClient(wrapper) as client:
        assert client.post("/write").status_code == 503
        assert events == ["write", "persist"]
        assert client.post("/write").status_code == 503
        assert events == ["write", "persist", "persist"]


def test_hosted_startup_and_restart_persist_database(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "state"
    seed_dir = tmp_path / "seed"
    seed(seed_dir)
    monkeypatch.setenv("MEGADAY_SEED_DB", str(seed_dir / "megaday.duckdb"))
    settings = Settings(
        app_env="test",
        duckdb_path=root / "megaday.duckdb",
        live_dir=root / "live",
        data_dir=tmp_path / "inputs",
        live_enabled=False,
        ai_enabled=False,
    )
    saved: list[bytes] = []

    class MemoryStore(SnapshotStore):
        def restore(self) -> bool:
            if not saved:
                return False
            restore_archive(saved[-1], self.root)
            return True

        def save(self, runtime) -> None:
            runtime.db.write.execute("CHECKPOINT")
            runtime.db.close()
            saved.append(snapshot_archive(self.root))

    store = MemoryStore(root, "unused")
    with TestClient(hosted_app(store, settings)) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["data"]["db"] == "ok"
        assert saved  # startup snapshot, before ready
    with TestClient(hosted_app(store, settings)) as client:
        assert client.get("/api/v1/health").status_code == 200
    with duckdb.connect(str(root / "megaday.duckdb")) as conn:
        assert conn.execute("SELECT count(*) FROM hosted_probe").fetchone() == (1,)
