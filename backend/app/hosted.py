"""Cloudflare entrypoint: restore first, persist acknowledged writes and replay hours.

The virtual HTTP storage endpoint is intercepted by ContainerProxy and backed by
private R2. No R2 credentials or public storage endpoint enter the container.
This deployment is a single demo workspace, not a multi-tenant database service.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import stat
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath

from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.logging import get_logger
from app.main import create_app
from app.orchestrator.runtime import Runtime
from app.warehouse.db import Database

log = get_logger(__name__)
MAX_SNAPSHOT = 96 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
STATE_DIRS = {"megaday-replay", "live"}


class SnapshotStore:
    def __init__(self, root: Path, url: str) -> None:
        self.root = root.resolve()
        self.url = url
        self.etag: str | None = None
        # The virtual host must go directly through ContainerProxy, never a system proxy.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def restore(self) -> bool:
        """Only 404 means a first boot. Storage errors/corruption abort startup."""
        try:
            with self.http.open(self.url, timeout=120) as response:
                raw = response.read(MAX_SNAPSHOT + 1)
                etag = response.headers.get("ETag")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise
        if len(raw) > MAX_SNAPSHOT or not etag:
            raise ValueError("Invalid stored snapshot")
        restore_archive(raw, self.root)
        self.etag = etag
        return True

    def save(self, runtime: Runtime) -> None:
        # All HTTP operations and replay hours hold the hosted gate. settle() has
        # drained the event bus / recompute before this additional writer lock.
        with runtime.lock:
            runtime.db.write.execute("CHECKPOINT")
            # Release the file handle as well (required by DuckDB on Windows).
            # Hosted HTTP and replay gates ensure no business writer is active.
            runtime.db.close()
            try:
                raw = snapshot_archive(self.root)
            finally:
                runtime.db.write.execute("SELECT 1")
        headers = {"Content-Type": "application/zip"}
        if self.etag:
            headers["If-Match"] = self.etag
        else:
            headers["If-None-Match"] = "*"
        req = urllib.request.Request(self.url, data=raw, headers=headers, method="PUT")
        with self.http.open(req, timeout=120) as response:
            etag = response.headers.get("ETag")
            if not etag:
                raise ValueError("Storage did not acknowledge the snapshot revision")
            self.etag = etag


def valid_member(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(path.parts)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in name
        and ":" not in name
        and (name in {"megaday.duckdb", "snapshot.json"} or path.parts[0] in STATE_DIRS)
    )


def snapshot_archive(root: Path) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("snapshot.json", json.dumps({"version": 1}))
        for path in sorted(root.rglob("*")):
            name = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ValueError("Symlinks are not allowed in backend state")
            if path.is_file() and valid_member(name) and name != "snapshot.json":
                archive.write(path, name)
    raw = data.getvalue()
    if len(raw) > MAX_SNAPSHOT:
        raise ValueError("Backend state exceeds the demo snapshot limit")
    return raw


def restore_archive(raw: bytes, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if (
            len(entries) > 2048
            or len(set(names)) != len(names)
            or sum(item.file_size for item in entries) > MAX_EXPANDED
            or "megaday.duckdb" not in names
        ):
            raise ValueError("Invalid snapshot contents")
        for item in entries:
            if (
                not valid_member(item.filename)
                or item.is_dir()
                or stat.S_ISLNK(item.external_attr >> 16)
            ):
                raise ValueError("Unsafe snapshot path")
        if json.loads(archive.read("snapshot.json")) != {"version": 1}:
            raise ValueError("Unsupported snapshot version")
        # Validate/decompress all members before touching live files.
        with tempfile.TemporaryDirectory(dir=root.parent) as temp:
            staged = Path(temp)
            for item in entries:
                dest = staged / item.filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, dest.open("wb") as target:
                    shutil.copyfileobj(source, target)
            for name in STATE_DIRS:
                if (root / name).exists():
                    shutil.rmtree(root / name)
            (root / "megaday.duckdb.wal").unlink(missing_ok=True)
            for path in staged.iterdir():
                dest = root / path.name
                if path.is_dir():
                    shutil.copytree(path, dest)
                else:
                    os.replace(path, dest)


class DurableResponses:
    """Do not acknowledge an HTTP write until its snapshot is durable.

    Streams are read-only and bypass buffering. Failed persistence blocks later
    operations until persistence succeeds, rather than silently serving volatile state.
    """

    def __init__(
        self, app: ASGIApp, fastapi: FastAPI, store: SnapshotStore, gate: asyncio.Lock
    ) -> None:
        self.app, self.fastapi, self.store, self.gate = app, fastapi, store, gate
        self.failed = False

    async def persist(self) -> None:
        runtime: Runtime = self.fastapi.state.runtime

        async def commit() -> None:
            try:
                await runtime.settle()
                await asyncio.to_thread(self.store.save, runtime)
                self.failed = False
            except Exception:
                self.failed = True
                log.exception("snapshot_failed")
                raise

        task = asyncio.create_task(commit())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # A disconnected client must not release the write gate while the
            # snapshot thread is still copying/uploading database state.
            await task
            raise

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # SSE holds connections open; it does not mutate state.
        if scope["path"] == "/api/v1/stream" and scope["method"] == "GET":
            await self.app(scope, receive, send)
            return
        async with self.gate:
            messages: list[Message] = []

            async def collect(message: Message) -> None:
                messages.append(message)

            try:
                if self.failed:
                    await self.persist()
                try:
                    await self.app(scope, receive, collect)
                finally:
                    # Even a failed operation may have committed ingestion/audit rows.
                    if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                        await self.persist()
            except Exception:
                response = JSONResponse(
                    {
                        "error": {
                            "code": "PERSISTENCE_UNAVAILABLE" if self.failed else "BACKEND_ERROR",
                            "message": (
                                "Could not confirm saved state. "
                                "Refresh before retrying this action."
                            ),
                        }
                    },
                    status_code=503,
                )
                await response(scope, receive, send)
                return
            for message in messages:
                await send(message)


def hosted_app(store: SnapshotStore, settings: Settings) -> ASGIApp:
    if settings.live_enabled:
        raise ValueError("Hosted mode uses controlled replay, not a filesystem polling watcher")
    if (
        settings.duckdb_path != store.root / "megaday.duckdb"
        or settings.live_dir != store.root / "live"
    ):
        raise ValueError("Hosted DB and live directory must reside under the snapshot root")
    restored = store.restore()
    if not restored:
        seed = Path(os.environ["MEGADAY_SEED_DB"])
        store.root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed, settings.duckdb_path)
    app = create_app(settings, Database(settings.duckdb_path))
    gate = asyncio.Lock()
    wrapped = DurableResponses(app, app, store, gate)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with original(application):
            runtime: Runtime = application.state.runtime
            runtime.control.step_guard = gate
            runtime.control.after_step = wrapped.persist
            # Persist the first seed/migrations before accepting traffic.
            await wrapped.persist()
            try:
                yield
            finally:
                await runtime.control.stop()
                async with gate:
                    await wrapped.persist()

    app.router.lifespan_context = lifespan
    return wrapped


def main() -> None:
    import uvicorn

    root = Path(os.environ["MEGADAY_STATE_ROOT"])
    store = SnapshotStore(root, os.environ["MEGADAY_STATE_URL"])
    application = hosted_app(store, Settings())
    uvicorn.run(application, host="0.0.0.0", port=8000, workers=1)


if __name__ == "__main__":
    main()
