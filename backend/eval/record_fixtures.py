"""Ghi fixtures của web/ từ API thật.

M5, mục "Fixtures từ API thật" trong docs/quyet-dinh/M5-chuan-bi-demo.md.

Trang công khai chạy ``web/`` ở chế độ fixtures (``VITE_USE_FIXTURES=true``). Script này dựng
DB demo tạm tới 01:00 ngày 11.11 giống hệt ``scripts/demo.mjs`` mặc định
(``app.demo_import --replay-hour 0 --models``: qua thư mục phát lại, backtest + fit + iROAS), lead
đặt mục tiêu profit cho CD002, chạy mô phỏng đầu tiên giống nút trên màn So sánh, rồi gọi
đúng các GET mà web gọi ở chế độ API thật qua ``TestClient`` và ghi response ``{data, meta}``
thành file fixture (tên theo ``FIXTURE_ROUTES`` / quy ước ``<loại>-<id>.json`` trong
``web/src/api/client.ts``).

    cd backend
    uv run python -m eval.record_fixtures --out ../web/src/api/fixtures            # ~4–10 phút
    uv run python -m eval.record_fixtures --out ../web/src/api/fixtures --db PATH  # DB có sẵn

``--db``: DB demo 01:00 đầy đủ của ``scripts/demo.mjs`` (``data/pitch-<thời gian>/demo.duckdb``).
Script chép sang thư mục tạm, không ghi vào file gốc. DB tạm xóa khi xong.

Danh sách SKU giữ ``SKU_ROWS`` dòng ưu tiên cao nhất (bundle nhẹ) nhưng ``meta.total`` vẫn là
tổng thật. Ví dụ minh họa (``*-rec-540``, ``*-rec-510``, ``*-scn-2280``, ``*-scn-2210``) không
có trong DB nên không bị đụng tới. Sau khi ghi, quét PII (số điện thoại, email, tên người mua
lấy từ file đơn hàng) trong mọi file vừa ghi; thấy là dừng với mã lỗi. Giờ máy lúc tính
(``computed_at``…) đổi thành 01:00 11/11 (``to_demo_clock``); ``live_dir`` (đường dẫn máy) bỏ.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
SHOP = BACKEND.parent / "mock-data" / "out" / "demo"
CLOCK = "2026-11-11T01:00:00+07:00"
CAMPAIGN = "CD002"
LEAD_SKU = "NOICHIEN2L"
SEED = 20261111  # web/src/pages/compare/compareLogic.ts DEFAULT_SEED
SKU_ROWS = 50
OPERATOR = {"X-User": "u-ops-1", "X-Role": "operator"}  # web/src/lib/config.ts mặc định
LEAD = {"X-User": "lead-1", "X-Role": "lead"}
GOAL = {
    "scope": "campaign",
    "scope_id": CAMPAIGN,
    "kind": "profit",
    "deadline": "2026-11-12T00:00:00+07:00",
    "reason": "Lãi trong ngày 11.11",
}
# Cột có thể chứa thông tin người mua trong file đơn hàng (chỉ đọc để quét, không ghi đi đâu).
BUYER_COLUMNS = ("Người mua", "Số điện thoại", "Địa chỉ nhận hàng", "Ghi chú của người mua")
PHONE = re.compile(r"(?<!\d)(?:\+?84|0)(?:3|5|7|8|9)\d{8}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}")
STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+(?:Z|[+-]\d\d:\d\d)$")


def show(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.flush()


def build_db(tmp: Path, reuse: Path | None) -> Path:
    if reuse is not None:
        path = tmp / "demo.duckdb"
        shutil.copy2(reuse, path)
        wal = reuse.with_name(reuse.name + ".wal")
        if wal.exists():
            shutil.copy2(wal, path.with_name(path.name + ".wal"))
        return path
    out = tmp / "import"
    cmd = [sys.executable, "-m", "app.demo_import", "--shop", str(SHOP), "--output", str(out)]
    subprocess.run([*cmd, "--replay-hour", "0", "--models"], cwd=BACKEND, check=True)
    return out / "demo.duckdb"


def record(path: Path) -> dict[str, Any]:
    """Gọi API như web; trả {tên fixture: body}."""
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app
    from app.warehouse.db import Database

    settings = Settings(duckdb_path=path, ai_enabled=False, live_enabled=False, app_env="demo")
    out: dict[str, Any] = {}
    with TestClient(create_app(settings, db=Database(path))) as api:

        def call(method: str, url: str, headers: dict[str, str], **kw: Any) -> Any:
            res = api.request(method, "/api/v1" + url, headers=headers, **kw)
            if res.status_code >= 400:
                raise RuntimeError(f"{method} {url}: {res.status_code} {res.text[:500]}")
            return res.json()

        def get(url: str) -> Any:
            return call("GET", url, OPERATOR)

        goal = call("POST", "/goals", LEAD, json=GOAL)["data"]
        # Nút "Chạy mô phỏng" trên màn So sánh (CompareSetup.useFirstRun).
        body = {"campaign_id": CAMPAIGN, "goal_id": goal["goal_id"], "samples": 1000, "seed": SEED}
        run = call("POST", "/scenarios/run", OPERATOR, json=body)["data"]
        rec_id = run["recommendation"]["recommendation_id"]
        scn_id = run["scenario_id"]

        out["health"] = get("/health")
        out["me"] = get("/me")
        out["freshness"] = get("/freshness")
        live = get("/live/status")
        live["data"]["live_dir"] = None  # đường dẫn trên máy ghi, không đưa lên web
        out["live-status"] = live
        out["alerts"] = get("/alerts?status=open")
        out["recommendations"] = get("/recommendations?active=true")
        out[f"recommendation-{rec_id}"] = get(f"/recommendations/{rec_id}")
        out[f"scenario-{scn_id}"] = get(f"/scenarios/{scn_id}")
        out[f"campaign-{CAMPAIGN}"] = get(f"/campaigns/{CAMPAIGN}")
        out[f"sku-{LEAD_SKU}"] = get(f"/skus/{LEAD_SKU}")
        # Tổng quan ca: "Cần xem ngay" đọc sort=priority&limit=200; bảng đọc trang 50.
        skus = get("/skus?sort=priority&limit=500")
        total = skus["meta"].get("total") or len(skus["data"])
        skus["data"] = skus["data"][:SKU_ROWS]
        skus["meta"]["total"] = total
        skus["next_cursor"] = None  # fixture không phân trang: cursor sẽ trả lại cùng file
        out["skus"] = skus
    return out


def to_demo_clock(body: Any, end: datetime) -> Any:
    """Giờ máy lúc tính (computed_at, received_at, created_at, …) → đồng hồ demo 01:00 11/11.

    Giờ máy có phần lẻ giây và không muộn hơn lúc ghi; mốc dữ liệu mô phỏng (tròn giây, theo
    lịch 11.11) giữ nguyên. Trang công khai là ảnh chụp lúc 01:00, nên "tính lúc" ghi 01:00.
    """
    if isinstance(body, dict):
        return {k: to_demo_clock(v, end) for k, v in body.items()}
    if isinstance(body, list):
        return [to_demo_clock(v, end) for v in body]
    if isinstance(body, str) and STAMP.match(body) and datetime.fromisoformat(body) <= end:
        return CLOCK
    return body


def buyer_strings() -> set[str]:
    """Tên/sđt/địa chỉ người mua trong file đơn (chỉ để quét). Không đọc ground_truth."""
    found: set[str] = set()
    name = "don_hang_seller_center.csv"
    for f in (SHOP / name, SHOP / "live_11_11" / name):
        with f.open(encoding="utf-8-sig", newline="") as src:
            reader = csv.DictReader(src)
            for row in reader:
                found.update(v.strip() for c in BUYER_COLUMNS if len(v := row.get(c) or "") >= 6)
    return found


def scan_pii(files: list[Path]) -> list[str]:
    buyers = buyer_strings()
    hits: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        hits += [f"{f.name}: sđt {m}" for m in PHONE.findall(text)]
        hits += [f"{f.name}: email {m}" for m in EMAIL.findall(text)]
        hits += [f"{f.name}: người mua {b!r}" for b in buyers if b in text]
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ghi fixtures web từ API thật (11.11 01:00).")
    ap.add_argument("--out", type=Path, required=True, help="thư mục fixtures của web")
    ap.add_argument("--db", type=Path, default=None, help="DB demo 01:00 có sẵn (sẽ chép)")
    args = ap.parse_args(argv)
    if args.db is not None and not args.db.is_file():
        ap.error(f"Không thấy {args.db}")
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="record-fixtures-") as tmp:
        bodies = record(build_db(Path(tmp), args.db))
    end = datetime.now(UTC)
    bodies = {k: to_demo_clock(v, end) for k, v in bodies.items()}
    written: list[Path] = []
    for name, body in bodies.items():
        f = args.out / f"{name}.json"
        text = json.dumps(body, ensure_ascii=False, indent=2) + "\n"
        f.write_text(text, encoding="utf-8", newline="\n")  # LF như trong repo
        written.append(f)
        show(f"ghi {f.name} ({f.stat().st_size:,} byte)")
    hits = scan_pii(written)
    if hits:
        show("CÓ PII trong fixtures:\n" + "\n".join(hits[:20]))
        return 1
    show(f"Không thấy PII trong {len(written)} file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
