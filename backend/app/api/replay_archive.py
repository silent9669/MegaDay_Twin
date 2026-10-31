"""Demo ZIP staging. Upload does not change the data clock or analytical warehouse."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request

from app.api.common import data_as_of
from app.api.deps import Viewer, audit
from app.api.live import DemoLead, _runtime
from app.core.errors import StateConflict, ValidationFailed
from app.live.archive import (
    MAX_ARCHIVE_BYTES,
    archive_path,
    archive_summary,
    inspect_archive,
    save_archive,
)

router = APIRouter(tags=["live"])


@router.get("/live/replay-archive")
def get_archive(request: Request, user: Viewer) -> dict[str, Any]:
    rt = _runtime(request)
    return {
        **archive_summary(rt.control.source),
        "canControl": user.at_least("lead") and rt.settings.demo_mode,
    }


@router.post("/live/replay-archive")
async def upload_archive(request: Request, user: DemoLead) -> dict[str, Any]:
    rt = _runtime(request)
    if not rt.settings.demo_mode:
        raise StateConflict("Chỉ tải replay khi DEMO_MODE=true.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_ARCHIVE_BYTES:
            raise ValidationFailed("ZIP vượt quá 20 MiB.")
    data = await asyncio.to_thread(inspect_archive, bytes(body))
    async with rt.control.command_lock:
        if rt.control.running:
            raise StateConflict("Dừng replay trước khi thay ZIP.")
        current = archive_summary(rt.control.source)
        if current.get("sha256") == data["_upload"]["sha256"]:
            return {**current, "canControl": True}

        def stage() -> None:
            with rt.lock, rt.db.reader() as conn:
                live = conn.execute(
                    "SELECT count(*) FROM ingest_batches WHERE mode = 'live'"
                ).fetchone()
                if live and live[0]:
                    raise StateConflict("Đã có dữ liệu replay. Đặt lại replay trước khi thay ZIP.")
                mark = data_as_of(conn)
                from app.core.timeutil import to_local

                if mark is None or to_local(mark).isoformat()[:10] > data["ngay"]:
                    raise StateConflict(
                        "Cần nạp lịch sử trước ngày replay, campaign và giá vốn trước."
                    )
                if (
                    to_local(mark).isoformat()[:10] == data["ngay"]
                    and to_local(mark).strftime("%H:%M:%S") != "00:00:00"
                ):
                    raise StateConflict(
                        "Đã import dữ liệu tương lai của ngày này vào lịch sử. "
                        "Dùng database demo sạch."
                    )
                dest = archive_path(rt.db.path)
                save_archive(dest, data)
                rt.control.source = dest
                audit(
                    conn,
                    request,
                    user,
                    "live.upload",
                    "live",
                    data["ngay"],
                    after={"sha256": data["_upload"]["sha256"]},
                )

        await asyncio.to_thread(stage)
    return {**archive_summary(rt.control.source), "canControl": True}
