"""Stage replay inputs separately from analytical tables; never ingest future CSVs."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from app.core.errors import ValidationFailed

MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
FILES = {
    "orders": "don_hang_seller_center.csv",
    "ads": "bao_cao_ads.csv",
    "inventory": "ton_kho_google_sheet.csv",
}
TIME_COLUMNS = {"orders": "Ngày đặt hàng", "ads": "Thời gian", "inventory": "Thời điểm cập nhật"}


def archive_path(db_path: Path) -> Path:
    return db_path.parent / f"{db_path.stem}-replay" / "_replay.json"


def inspect_archive(raw: bytes) -> dict[str, Any]:
    try:
        if len(raw) > MAX_ARCHIVE_BYTES:
            raise ValueError("ZIP vượt quá 20 MiB.")
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            entries = z.infolist()
            if len(entries) > 20 or sum(i.file_size for i in entries) > MAX_ARCHIVE_BYTES:
                raise ValueError("ZIP giải nén vượt giới hạn 20 MiB hoặc 20 mục.")
            seen: set[str] = set()
            sources = []
            for item in entries:
                path = PurePosixPath(item.filename)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in item.filename
                    or ":" in item.filename
                    or item.flag_bits & 1
                ):
                    raise ValueError("ZIP chứa đường dẫn hoặc mã hóa không được hỗ trợ.")
                if item.is_dir():
                    continue
                if path.name not in {*FILES.values(), "_replay.json"} or path.name in seen:
                    raise ValueError("ZIP chỉ nhận một bộ live_11_11, không kèm file khác.")
                seen.add(path.name)
                if path.name == "_replay.json":
                    sources.append(item)
            if len(sources) != 1:
                raise ValueError("ZIP cần có đúng một _replay.json để phát từng giờ.")
            data = json.loads(z.read(sources[0]).decode("utf-8-sig"))
        day = datetime.strptime(data["ngay"], "%Y-%m-%d")
        if type(data["adsLagHours"]) is not int or not 0 <= data["adsLagHours"] <= 23:
            raise ValueError("Độ trễ ads phải nằm trong 0–23 giờ.")
        if data["files"] != FILES:
            raise ValueError("Tên file replay không đúng hợp đồng live_11_11.")
        counts = {}
        for kind in FILES:
            header = data["headers"][kind]
            columns = next(csv.reader([header], strict=True))
            if TIME_COLUMNS[kind] not in columns or len(columns) > 100:
                raise ValueError(f"Thiếu cột thời gian của {kind}.")
            time_index = columns.index(TIME_COLUMNS[kind])
            rows = data["rows"][kind]
            if not isinstance(rows, list) or not rows or len(rows) > 150000:
                raise ValueError(f"Số dòng {kind} không hợp lệ.")
            for pair in rows:
                hour, line = pair
                if type(hour) is not int or not 0 <= hour <= 23 or not isinstance(line, str):
                    raise ValueError("Dòng replay cần giờ nguyên từ 0 đến 23.")
                if len(line) > 20000 or "\n" in line or "\r" in line or "\x00" in line:
                    raise ValueError("Dòng CSV replay không hợp lệ.")
                cells = next(csv.reader([line], strict=True))
                if len(cells) != len(columns):
                    raise ValueError("Số cột CSV không khớp header.")
                stamp = datetime.strptime(cells[time_index], "%Y-%m-%d %H:%M")
                # Orders are VN time, ads UTC, inventory is the next-hour boundary.
                offset = {"orders": 0, "ads": -7, "inventory": 1}[kind]
                if stamp.replace(minute=0) != day + timedelta(hours=hour + offset):
                    raise ValueError(
                        "Giờ phát không khớp thời gian bản ghi; tránh lộ dữ liệu tương lai."
                    )
            counts[kind] = len(rows)
        # Only these fields are persisted; archive paths and companion full-day CSVs are unused.
        return {key: data[key] for key in ("ngay", "adsLagHours", "files", "headers", "rows")} | {
            "_upload": {"sha256": hashlib.sha256(raw).hexdigest(), "counts": counts}
        }
    except (
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        StopIteration,
        csv.Error,
        zipfile.BadZipFile,
        NotImplementedError,
        RuntimeError,
        UnicodeError,
    ) as exc:
        raise ValidationFailed(f"Không đọc được ZIP replay: {exc}") from exc


def save_archive(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def archive_summary(source: Path) -> dict[str, Any]:
    if not source.is_file():
        return {"uploaded": False}
    from app.live.replay import load_replay

    data = load_replay(source)
    meta = data.get("_upload")
    return {
        "uploaded": bool(meta),
        "day": data["ngay"],
        "adsLagHours": data["adsLagHours"],
        "sha256": meta["sha256"] if meta else None,
        "counts": meta["counts"] if meta else None,
    }
