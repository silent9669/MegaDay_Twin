"""Versioned Warehouse label handoff through resolved inventory aliases."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.demand_view import demand_view
from app.api.deps import Conn, Operator, Viewer, audit, publish
from app.api.scenarios import live_write_lock
from app.api.schemas import Envelope, Meta
from app.core.errors import StateConflict, ValidationFailed
from app.core.events import RecomputeRequested
from app.core.timeutil import utcnow
from app.forecast.demand import active_labels, inventory_hash, inventory_mapping
from app.simulator.locks import RUN_LOCK

router = APIRouter(tags=["demand-labels"])


class DemandEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_code: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=300)
    label: Literal["Normal", "Seasonal", "Trendy"]
    reason: str = Field(min_length=1, max_length=1000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    manual: bool = False


class DemandSync(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=150)
    source_revision: int = Field(ge=0, strict=True)
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    inventory_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider: str = Field(min_length=1, max_length=150)
    market: str = Field(min_length=1, max_length=100)
    assessed_on: date
    completed_at: datetime
    evidence_through: datetime | None = None
    labels: list[DemandEntry] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def unique_codes(self) -> DemandSync:
        if len({entry.raw_code for entry in self.labels}) != len(self.labels):
            raise ValueError("Mã sản phẩm bị lặp trong bản nhãn.")
        return self


@router.get("/demand-labels", response_model=Envelope[dict[str, Any]])
def get_labels(conn: Conn, user: Viewer, request: Request) -> Envelope[dict[str, Any]]:
    with live_write_lock(request), RUN_LOCK:
        labels = active_labels(conn)
    return Envelope(
        data={"version": 1, "labels": {sku: demand_view(label) for sku, label in labels.items()}},
        meta=Meta(computed_at=utcnow()),
    )


@router.post("/demand-labels/sync", response_model=Envelope[dict[str, Any]])
def sync_labels(
    body: DemandSync, conn: Conn, user: Operator, request: Request
) -> Envelope[dict[str, Any]]:
    if body.completed_at.tzinfo is None or body.completed_at > utcnow():
        raise ValidationFailed("Thời điểm tạo nhãn phải có múi giờ và không ở tương lai.")
    if body.market.strip().lower() not in {"vietnam", "viet nam", "việt nam", "vn"}:
        raise ValidationFailed("Backend dùng lịch Việt Nam; cần nhãn cùng thị trường.")
    with live_write_lock(request), RUN_LOCK:
        if body.inventory_sha256 != inventory_hash(conn):
            raise StateConflict(
                "Cần gửi đúng bản dữ liệu tồn kho mới nhất sang backend trước khi đồng bộ nhãn."
            )
        vn = ZoneInfo("Asia/Ho_Chi_Minh")
        last = conn.execute(
            "SELECT max(snapshot_at) FROM inventory_snapshots JOIN ingest_batches USING (batch_id) "
            "WHERE file_sha256=?",
            [body.inventory_sha256],
        ).fetchone()
        latest_day = (
            max(utcnow().astimezone(vn).date(), last[0].astimezone(vn).date())
            if last and last[0]
            else utcnow().astimezone(vn).date()
        )
        if body.assessed_on > latest_day:
            raise ValidationFailed("Ngày đánh giá vượt quá ngày dữ liệu tồn kho hiện có.")
        if body.evidence_through is not None and (
            body.evidence_through.tzinfo is None
            or body.evidence_through
            > datetime.combine(latest_day + timedelta(days=1), time.min, vn)
        ):
            raise ValidationFailed("Mốc bằng chứng phải có múi giờ và nằm trong dữ liệu hiện có.")
        mapping = inventory_mapping(conn)
        targets = Counter(mapping.get(entry.raw_code) for entry in body.labels)
        rejected = []
        accepted = []
        changed: set[str] = set()
        now = utcnow()
        assessed = max(
            body.completed_at,
            datetime.combine(body.assessed_on, time.min, ZoneInfo("Asia/Ho_Chi_Minh")),
            body.evidence_through or body.completed_at,
        )
        # Never overwrite an earlier source run. Each entry is an immutable snapshot.
        conn.execute("BEGIN TRANSACTION")
        try:
            for entry in body.labels:
                sku = mapping.get(entry.raw_code)
                if not sku or targets[sku] > 1:
                    rejected.append(
                        {
                            "raw_code": entry.raw_code,
                            "reason": "Cần ánh xạ một-một giữa mã tồn và SKU.",
                        }
                    )
                    continue
                payload = {
                    **body.model_dump(mode="json", exclude={"labels"}),
                    **entry.model_dump(mode="json"),
                    "sku_id": sku,
                }
                encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
                entry_id = hashlib.sha256(encoded.encode()).hexdigest()
                inserted = conn.execute(
                    "INSERT INTO sku_demand_labels VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT DO NOTHING RETURNING entry_id",
                    [entry_id, sku, entry.raw_code, body.inventory_sha256, assessed, now, encoded],
                ).fetchone()
                if inserted:
                    changed.add(sku)
                accepted.append({"raw_code": entry.raw_code, "sku_id": sku, "entry_id": entry_id})
            audit(
                conn,
                request,
                user,
                "demand_labels.sync",
                "label_run",
                body.run_id,
                after={"accepted": len(accepted), "pending": len(rejected)},
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    if changed:
        publish(request, RecomputeRequested(sku_ids=sorted(changed), reason="demand_labels.synced"))
    return Envelope(
        data={"version": 1, "run_id": body.run_id, "accepted": accepted, "pending": rejected},
        meta=Meta(computed_at=now),
    )
