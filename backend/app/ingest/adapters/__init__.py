"""Source adapters (one per seller file): base.SourceAdapter / RawFrame, orders, ads,
inventory, cost, campaigns, sale program.

Plan: docs/dev-plan/02-lop-du-lieu.md §1, §2
Tasks: ING-01..ING-06 (docs/dev-plan/12-lo-trinh-va-phan-viec.md)

Entry points for the pipeline::

    det = detect_source(path)                 # app.ingest.detect
    adapter = get_adapter(det.source_type)
    raw = adapter.read(path)                  # PII dropped here
    norm = adapter.normalize(raw, ctx)        # NormFrame: tables + source_items + issues

or simply ``read_and_normalize(path, ctx)``.
"""

from __future__ import annotations

from pathlib import Path

from app.ingest.adapters.ads import AdsAdapter
from app.ingest.adapters.base import (
    DataIssue,
    IngestContext,
    NormFrame,
    RawFrame,
    SourceAdapter,
    SourceType,
    make_issue,
)
from app.ingest.adapters.campaigns import CampaignsAdapter
from app.ingest.adapters.cost import CostAdapter
from app.ingest.adapters.inventory import InventoryAdapter
from app.ingest.adapters.orders import OrdersAdapter
from app.ingest.adapters.sale_program import SaleProgramAdapter

ADAPTERS: dict[SourceType, type[SourceAdapter]] = {
    SourceType.ORDERS: OrdersAdapter,
    SourceType.ADS: AdsAdapter,
    SourceType.INVENTORY: InventoryAdapter,
    SourceType.COST: CostAdapter,
    SourceType.CAMPAIGNS: CampaignsAdapter,
    SourceType.SALE_PROGRAM: SaleProgramAdapter,
}


def get_adapter(source_type: SourceType | str) -> SourceAdapter:
    return ADAPTERS[SourceType(source_type)]()


def read_and_normalize(path: Path, ctx: IngestContext) -> NormFrame | DataIssue:
    """Detect → read → normalize one file. Returns the ``UNKNOWN_SOURCE`` issue
    when the header matches no adapter."""
    from app.ingest.detect import detect_source

    det = detect_source(Path(path))
    if det.source_type is None:
        return det.issue or make_issue("UNKNOWN_SOURCE", entity_type="file", entity_id=str(path))
    adapter = get_adapter(det.source_type)
    return adapter.normalize(adapter.read(Path(path)), ctx)


__all__ = [
    "ADAPTERS",
    "AdsAdapter",
    "CampaignsAdapter",
    "CostAdapter",
    "DataIssue",
    "IngestContext",
    "InventoryAdapter",
    "NormFrame",
    "OrdersAdapter",
    "RawFrame",
    "SaleProgramAdapter",
    "SourceAdapter",
    "SourceType",
    "get_adapter",
    "read_and_normalize",
]
