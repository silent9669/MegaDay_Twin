"""API-03: upload → batches → SKU queue → confirm, and data-quality listing."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.helpers import OPS, VIEWER, err, ok
from tests.mockdata import mock_dir

V1 = "/api/v1"


def test_batches_and_quality(api: TestClient) -> None:
    body = ok(api.get(f"{V1}/ingest/batches", headers=VIEWER))
    kinds = {b["source_type"] for b in body["data"]}
    assert {"orders", "ads", "inventory", "cost", "campaigns", "sale_program"} <= kinds
    b = body["data"][0]
    one = ok(api.get(f"{V1}/ingest/batches/{b['batch_id']}", headers=VIEWER))["data"]
    assert one["batch_id"] == b["batch_id"] and isinstance(one["issues"], dict)
    err(api.get(f"{V1}/ingest/batches/ib-khong-co", headers=VIEWER), 404, "NOT_FOUND")
    issues = ok(api.get(f"{V1}/data-quality/issues", params={"limit": 500}, headers=VIEWER))
    assert issues["data"], "nho has open issues"
    i = issues["data"][0]
    assert i["severity"] in ("blocking", "warning", "info") and i["message"] and i["kind"]
    blk = ok(api.get(f"{V1}/data-quality/issues", params={"severity": "blocking"}, headers=VIEWER))[
        "data"
    ]
    assert all(x["severity"] == "blocking" for x in blk)
    s = ok(api.get(f"{V1}/data-quality", headers=VIEWER))["data"]
    assert s["total"] >= len(issues["data"]) and set(s["open_by_severity"]) >= {"blocking"}


def test_sku_queue_and_decide(api: TestClient) -> None:
    err(api.get(f"{V1}/ingest/sku-queue", headers=VIEWER), 403, "FORBIDDEN")
    q = ok(api.get(f"{V1}/ingest/sku-queue", params={"status": "all"}, headers=OPS))
    assert {"auto_matched", "needs_review"} <= set(q["data"]["summary"]) and "next_cursor" in q
    item = q["data"]["items"][0]
    assert {"alias_id", "alias", "candidates", "selected", "flag"} <= set(item)
    sku = item["selected"] or "NOICHIEN2L"
    url = f"{V1}/ingest/sku-queue/{item['alias_id']}"
    err(api.post(url, json={"action": "confirm"}, headers=OPS), 400, "VALIDATION_ERROR")
    d = ok(api.post(url, json={"sku": sku}, headers=OPS))["data"]
    assert d["status"] == "confirmed" and d["decided_by"] == "u-ops-1"
    err(api.post(f"{V1}/ingest/sku-queue/al-khong-co", json={"sku": sku}, headers=OPS), 404,
        "NOT_FOUND")  # fmt: skip


def test_upload_same_file_is_skipped(api: TestClient) -> None:
    path = mock_dir("nho") / "chuong_trinh_sale.json"
    files = [("files[]", (path.name, path.read_bytes(), "application/json"))]
    err(api.post(f"{V1}/ingest/files", files=files, headers=VIEWER), 403, "FORBIDDEN")
    body = ok(api.post(f"{V1}/ingest/files", files=files, headers=OPS), 201)["data"]
    assert body["batches"][0]["status"] == "skipped"  # same SHA-256 as the history load
    forced = ok(
        api.post(f"{V1}/ingest/files", params={"force": True}, files=files, headers=OPS), 201
    )["data"]
    b = forced["batches"][0]
    assert b["status"] == "loaded" and b["detected_kind"] == "sale_program" and b["batch_id"]
    conf = ok(api.post(f"{V1}/ingest/batches/{b['batch_id']}/confirm", json={}, headers=OPS))[
        "data"
    ]
    assert conf["batches"][0]["batch_id"] == b["batch_id"]
    img = [("files[]", ("anh.png", b"\x89PNG", "image/png"))]
    up = ok(api.post(f"{V1}/ingest/files", files=img, headers=OPS), 201)["data"]
    assert up["batches"][0]["status"] == "unsupported"
    bad = [("files[]", ("x.exe", b"MZ", "application/octet-stream"))]
    err(api.post(f"{V1}/ingest/files", files=bad, headers=OPS), 400, "VALIDATION_ERROR")
