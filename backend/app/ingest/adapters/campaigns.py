"""Campaign structure JSON → ``campaigns``, ``campaign_skus``. Plan 02 §2.5.

A campaign with more than one SKU shares one budget: ``sku_level_control =
False`` and the simulator must never propose switching a single SKU off
(05 §6). SKUs here are platform SKUs, so ``sku_id`` is filled directly.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd

from app.core.money import parse_vnd
from app.core.timeutil import parse_local
from app.ingest.adapters.base import (
    DataIssue,
    IngestContext,
    NormFrame,
    RawFrame,
    SourceType,
    file_sha256,
    make_issue,
    read_json,
)

CAMPAIGN_COLUMNS = [
    "campaign_id",
    "name",
    "type",
    "daily_budget",
    "started_at",
    "sku_level_control",
]


def _parse_start(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return parse_local(text, fmt)
        except ValueError:
            continue
    return None


def _money(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float | str):
        return parse_vnd(value)
    return None


class CampaignsAdapter:
    source_type: ClassVar[SourceType] = SourceType.CAMPAIGNS
    header_signature: ClassVar[frozenset[str]] = frozenset({"ma_chien_dich", "sku"})

    def read(self, path: Path) -> RawFrame:
        path = Path(path)
        return RawFrame(
            source_type=self.source_type,
            df=pd.DataFrame(),
            file_name=path.name,
            file_sha256=file_sha256(path),
            payload=read_json(path),
        )

    def normalize(self, raw: RawFrame, ctx: IngestContext) -> NormFrame:
        payload = raw.payload if isinstance(raw.payload, list) else []
        issues: list[DataIssue] = []
        campaigns: dict[str, dict[str, Any]] = {}
        links: dict[tuple[str, str], dict[str, str]] = {}
        for i, item in enumerate(payload):
            ref = f"item {i}"
            if not isinstance(item, dict):
                issues.append(
                    make_issue("ROW_INVALID", row_ref=ref, detail={"reason": "not_object"})
                )
                continue
            cid = str(item.get("ma_chien_dich") or "").strip()
            skus_raw = item.get("sku") or []
            skus = (
                [str(s).strip() for s in skus_raw if str(s).strip()]
                if isinstance(skus_raw, list)
                else []
            )
            budget = _money(item.get("ngan_sach_ngay"))
            if not cid or budget is None:
                issues.append(
                    make_issue(
                        "ROW_INVALID" if not cid else "MONEY_FORMAT",
                        entity_type="campaign",
                        entity_id=cid or None,
                        row_ref=ref,
                        detail={"columns": ["ma_chien_dich", "ngan_sach_ngay"]},
                    )
                )
                continue
            if cid in campaigns:
                issues.append(
                    make_issue(
                        "DUP_ROW",
                        entity_type="campaign",
                        entity_id=cid,
                        row_ref=ref,
                        detail={"key": ["ma_chien_dich"]},
                        status="auto_fixed",
                    )
                )
                for key in [k for k in links if k[0] == cid]:
                    del links[key]
            started = _parse_start(item.get("bat_dau"))
            if item.get("bat_dau") and started is None:
                issues.append(
                    make_issue(
                        "ROW_INVALID",
                        entity_type="campaign",
                        entity_id=cid,
                        row_ref=ref,
                        detail={"columns": ["bat_dau"], "value": str(item.get("bat_dau"))},
                    )
                )
            unique_skus = list(dict.fromkeys(skus))
            campaigns[cid] = {
                "campaign_id": cid,
                "name": str(item.get("ten_chien_dich") or cid).strip(),
                "type": (str(item["loai"]).strip() or None) if item.get("loai") else None,
                "daily_budget": budget,
                "started_at": started,
                "sku_level_control": len(unique_skus) <= 1,
            }
            for s in unique_skus:
                links[(cid, s)] = {"campaign_id": cid, "sku_id": s}

        camp_df = pd.DataFrame(list(campaigns.values()), columns=CAMPAIGN_COLUMNS)
        camp_df["started_at"] = pd.to_datetime(camp_df["started_at"], utc=True)
        camp_df["daily_budget"] = camp_df["daily_budget"].astype("Int64")
        camp_df["sku_level_control"] = camp_df["sku_level_control"].astype(bool)
        link_df = pd.DataFrame(list(links.values()), columns=["campaign_id", "sku_id"])
        return NormFrame(
            source_type=self.source_type,
            file_name=raw.file_name,
            file_sha256=raw.file_sha256,
            batch_id=ctx.batch_id,
            row_count=len(payload),
            tables={"campaigns": camp_df, "campaign_skus": link_df},
            issues=issues,
            as_of=ctx.received_at,
        )
