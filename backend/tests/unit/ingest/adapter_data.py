"""Shared loaders for the adapter tests (ING-01..06). Not a test module.

Runs every adapter on ``mock-data/out/<size>`` once per process. Only tests may
read the answer key (``manifest()``); code in app/ never does.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from app.ingest.adapters import (
    AdsAdapter,
    CampaignsAdapter,
    CostAdapter,
    IngestContext,
    InventoryAdapter,
    NormFrame,
    OrdersAdapter,
    SaleProgramAdapter,
)
from app.ingest.timezone_check import orders_hour_profile

REPO = Path(__file__).resolve().parents[4]
MOCK = REPO / "mock-data" / "out"
RECEIVED_AT = datetime(2026, 11, 11, 0, 0, tzinfo=UTC)

needs_nho = pytest.mark.skipif(not (MOCK / "nho").is_dir(), reason="mock-data/out/nho missing")


def data_dir(size: str = "nho") -> Path:
    return MOCK / size


def ctx(**kw: Any) -> IngestContext:
    return IngestContext(batch_id=kw.pop("batch_id", "b-test"), received_at=RECEIVED_AT, **kw)


@cache
def manifest(size: str = "nho") -> dict[str, Any]:
    path = data_dir(size) / "ground_truth" / "manifest.json"
    return dict(json.loads(path.read_text(encoding="utf-8")))


def planted(size: str, kind: str) -> dict[str, Any]:
    return next(x for x in manifest(size)["loi_cai_san"] if x["loai"] == kind)


@cache
def orders(size: str = "nho", live: bool = False) -> NormFrame:
    sub = "live_11_11/" if live else ""
    a = OrdersAdapter()
    return a.normalize(a.read(data_dir(size) / f"{sub}don_hang_seller_center.csv"), ctx())


@cache
def ads(size: str = "nho") -> NormFrame:
    o = orders(size)
    c = ctx(
        orders_hour_profile=orders_hour_profile(o.tables["orders"]["ordered_at"]),
        orders_as_of=o.as_of,
    )
    a = AdsAdapter()
    return a.normalize(a.read(data_dir(size) / "bao_cao_ads.csv"), c)


@cache
def inventory(size: str = "nho", live: bool = False) -> NormFrame:
    sub = "live_11_11/" if live else ""
    a = InventoryAdapter()
    return a.normalize(a.read(data_dir(size) / f"{sub}ton_kho_google_sheet.csv"), ctx())


@cache
def cost(size: str = "nho") -> NormFrame:
    a = CostAdapter()
    return a.normalize(a.read(data_dir(size) / "gia_von_nhap_tay.csv"), ctx())


@cache
def campaigns(size: str = "nho") -> NormFrame:
    a = CampaignsAdapter()
    return a.normalize(a.read(data_dir(size) / "cau_truc_chien_dich.json"), ctx())


@cache
def sale_program(size: str = "nho") -> NormFrame:
    a = SaleProgramAdapter()
    return a.normalize(a.read(data_dir(size) / "chuong_trinh_sale.json"), ctx())


def codes(norm: NormFrame, code: str) -> list[Any]:
    return [i for i in norm.issues if i.code == code]
