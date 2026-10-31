import copy
import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest

from app.core.errors import ValidationFailed
from app.live.archive import inspect_archive
from app.live.replay import write_live_stream
from app.live.control import LiveController
from tests.mockdata import require


def source():
    return json.loads((require("nho") / "live_11_11/_replay.json").read_text(encoding="utf-8"))


def packed(data, name="live_11_11/_replay.json"):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, json.dumps(data, ensure_ascii=False))
    return out.getvalue()


def test_archive_and_published_hour_do_not_expose_future(tmp_path: Path):
    data = inspect_archive(packed(source()))
    path = tmp_path / "source.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    target = tmp_path / "stream"
    write_live_stream(path, target, 0)
    for key in ("orders", "inventory"):
        lines = (target / data["files"][key]).read_text(encoding="utf-8-sig").splitlines()
        assert lines[1:] == [line for h, line in data["rows"][key] if h == 0]
    assert len((target / data["files"]["ads"]).read_text(encoding="utf-8-sig").splitlines()) == 1
    write_live_stream(path, target, 2)
    ads = (target / data["files"]["ads"]).read_text(encoding="utf-8-sig").splitlines()
    assert ads[1:] == [line for h, line in data["rows"]["ads"] if h == 0]


@pytest.mark.parametrize(
    "path", ["../_replay.json", "/_replay.json", "C:/_replay.json", "ground_truth.json"]
)
def test_unsafe_archive_entries_are_rejected(path):
    with pytest.raises(ValidationFailed):
        inspect_archive(packed(source(), path))


def test_wrong_file_target_and_future_bucket_are_rejected():
    data = source()
    bad = copy.deepcopy(data)
    bad["files"]["orders"] = "../outside.csv"
    with pytest.raises(ValidationFailed):
        inspect_archive(packed(bad))
    bad = copy.deepcopy(data)
    future = next(pair for pair in bad["rows"]["orders"] if pair[0] == 23)
    future[0] = 0
    with pytest.raises(ValidationFailed, match="Giờ phát"):
        inspect_archive(packed(bad))


def test_invalid_and_oversized_zip():
    with pytest.raises(ValidationFailed):
        inspect_archive(b"not zip")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("_replay.json", "x" * (21 * 1024 * 1024))
    with pytest.raises(ValidationFailed, match="giới hạn"):
        inspect_archive(out.getvalue())


async def test_stop_finishes_pending_hour_even_without_background_watcher(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    class Watcher:
        root = tmp_path / "stream"
        last_error = None

        async def poll(self):
            entered.set()
            await release.wait()

    path = tmp_path / "source.json"
    path.write_text("{}")
    monkeypatch.setattr(
        "app.live.control.write_live_stream", lambda source, dest, hour: calls.append(hour)
    )
    controller = LiveController(Watcher(), path)
    await controller.start(0, 60)
    await asyncio.wait_for(entered.wait(), 2)
    stop = asyncio.create_task(controller.stop())
    await asyncio.sleep(0)
    assert not stop.done()
    release.set()
    await asyncio.wait_for(stop, 2)
    assert calls == [0]
    assert controller.hour == 0
    assert not controller.running
