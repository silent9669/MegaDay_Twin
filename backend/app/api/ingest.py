"""Data import + data quality (API-03, 08 §3.2–3.3).

Hackathon flow (no AI column mapping yet, M4): ``POST /ingest/files`` stores the uploads
under ``<duckdb dir>/uploads/<batch_id>/`` and runs the real pipeline
(``ingest_files``: detect by header → adapter → SKU matching → quality → panel). Rows whose
SKU alias is still pending are held (``rows_held``) → the batch is ``needs_sku_review``.
The operator settles aliases (``POST /ingest/sku-queue/{alias_id}``), then
``POST /ingest/batches/{batch_id}/confirm`` re-loads the file with ``force`` so the held rows
are written (M1-pipeline: ``force`` keeps ``batch_id`` and ``received_at``).
"""

from __future__ import annotations

import hashlib
import shutil
import threading
from pathlib import Path
from typing import Annotated, Any, Literal

import duckdb
from fastapi import APIRouter, File, Query, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.common import MAX_LIMIT, paginate, parse_json
from app.api.deps import Conn, Operator, Viewer, audit, publish
from app.api.schemas import Envelope, LocalDatetime, Meta, Page
from app.core.config import Settings
from app.core.errors import NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.timeutil import utcnow
from app.ingest.pipeline import FileReport, ingest_files
from app.ingest.sku_matching import apply_confirmation
from app.warehouse.repo import fetch_all, fetch_one

router = APIRouter(tags=["ingest"])

_INGEST_LOCK = threading.Lock()
MAX_FILES = 10
MAX_BYTES = 50 * 1024 * 1024
ALLOWED_SUFFIXES = {".csv", ".json", ".xlsx", ".png", ".jpg", ".jpeg"}
LOADABLE_SUFFIXES = {".csv", ".json"}

BatchStatus = Literal["loaded", "needs_sku_review", "skipped", "failed", "unsupported"]


class UploadedBatch(BaseModel):
    batch_id: str | None
    file_name: str
    detected_kind: str | None
    variant: str | None
    rows: int
    rows_written: dict[str, int]
    rows_held: int
    issues: dict[str, int]
    status: BatchStatus
    batch_status: str | None  # ok|ok_with_issues|failed (ingest_batches.status)
    as_of: LocalDatetime | None
    duration_ms: int
    message: str


class UploadResult(BaseModel):
    batches: list[UploadedBatch]
    pending_aliases: int
    skus_changed: int
    panel_rows: int | None


def _uploads_dir(settings: Settings) -> Path:
    return settings.duckdb_path.parent / "uploads"


def _status(f: FileReport) -> BatchStatus:
    if f.status == "skipped":
        return "skipped"
    if f.status == "failed":
        return "failed"
    return "needs_sku_review" if f.rows_held > 0 else "loaded"


_STATUS_MSG = {
    "loaded": "Đã nạp xong",
    "needs_sku_review": "Đã nạp, còn {held} dòng chờ xác nhận SKU",
    "skipped": "File này đã nạp trước đó (cùng nội dung), bỏ qua",
    "failed": "Không nạp được file",
    "unsupported": "Chưa đọc được loại file này (chỉ CSV, JSON)",
}


def _uploaded(f: FileReport) -> UploadedBatch:
    st = _status(f)
    return UploadedBatch(
        batch_id=f.batch_id,
        file_name=f.file_name,
        detected_kind=f.source_type,
        variant=f.variant,
        rows=f.rows_in,
        rows_written=f.rows_written,
        rows_held=f.rows_held,
        issues=f.issues,
        status=st,
        batch_status=f.batch_status,
        as_of=f.as_of,
        duration_ms=f.duration_ms,
        message=_STATUS_MSG[st].format(held=f.rows_held),
    )


@router.post(
    "/ingest/files",
    response_model=Envelope[UploadResult],
    status_code=201,
    summary="Tải file của shop lên và nạp",
)
def upload_files(
    request: Request,
    conn: Conn,
    user: Operator,
    files: Annotated[list[UploadFile], File(alias="files[]")] = [],  # noqa: B006
    files_plain: Annotated[list[UploadFile] | None, File(alias="files")] = None,
    force: bool = False,
) -> Envelope[UploadResult]:
    items = list(files) + list(files_plain or [])
    if not items:
        raise ValidationFailed("Chưa chọn file nào (trường files[]).")
    if len(items) > MAX_FILES:
        raise ValidationFailed(f"Tối đa {MAX_FILES} file mỗi lần.", got=len(items))
    settings: Settings = request.app.state.settings
    root = _uploads_dir(settings)
    staged: list[tuple[Path, str]] = []
    unsupported: list[UploadedBatch] = []
    for up in items:
        name = Path(up.filename or "file").name
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValidationFailed(f"Không nhận file {name}.", allowed=sorted(ALLOWED_SUFFIXES))
        data = up.file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValidationFailed(f"File {name} lớn hơn 50 MB.", file=name)
        if suffix not in LOADABLE_SUFFIXES:
            unsupported.append(
                UploadedBatch(
                    batch_id=None,
                    file_name=name,
                    detected_kind=None,
                    variant=None,
                    rows=0,
                    rows_written={},
                    rows_held=0,
                    issues={},
                    status="unsupported",
                    batch_status=None,
                    as_of=None,
                    duration_ms=0,
                    message=_STATUS_MSG["unsupported"],
                )
            )
            continue
        d = root / new_id("up")
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        path.write_bytes(data)
        staged.append((path, hashlib.sha256(data).hexdigest()))
    batches: list[UploadedBatch] = []
    pending = 0
    changed = 0
    panel_rows = None
    if staged:
        with _INGEST_LOCK:
            report = ingest_files(conn, [p for p, _ in staged], force=force, build_panel=True)
        for f in report.files:
            src = Path(f.path)
            if f.batch_id and src.exists():
                dest = root / f.batch_id
                dest.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest / src.name))
                shutil.rmtree(src.parent, ignore_errors=True)
            batches.append(_uploaded(f))
        pending = report.pending_aliases
        changed = len(report.skus_changed)
        panel_rows = report.panel.rows_written if report.panel else None
        publish(request, *report.events)
        audit(
            conn,
            request,
            user,
            "ingest.upload",
            "batch",
            ",".join(b.batch_id or b.file_name for b in batches),
            after={"files": [b.file_name for b in batches]},
        )
    return Envelope[UploadResult](
        data=UploadResult(
            batches=batches + unsupported,
            pending_aliases=pending,
            skus_changed=changed,
            panel_rows=panel_rows,
        ),
        meta=Meta(computed_at=utcnow(), compute_state="fresh"),
    )


# ---------------------------------------------------------------------- batches
class BatchOut(BaseModel):
    batch_id: str
    source_type: str
    file_name: str
    received_at: LocalDatetime
    as_of: LocalDatetime | None
    row_count: int | None
    dropped_columns: list[str]
    tz_offset_hours: int | None
    mode: str
    status: str
    duration_ms: int | None
    issues: dict[str, int] = Field(default_factory=dict)
    open_blocking: int = 0


_BATCH_COLS = (
    "batch_id, source_type, file_name, received_at, as_of, row_count, dropped_columns,"
    " tz_offset_hours, mode, status, duration_ms"
)


def _issue_counts(conn: duckdb.DuckDBPyConnection) -> dict[str, tuple[dict[str, int], int]]:
    out: dict[str, tuple[dict[str, int], int]] = {}
    for b, code, n, blocking in conn.execute(
        "SELECT batch_id, code, count(*), count(*) FILTER (WHERE severity = 'error'"
        " AND status = 'open') FROM data_quality_issues GROUP BY 1, 2"
    ).fetchall():
        counts, blk = out.get(str(b), ({}, 0))
        counts[str(code)] = int(n)
        out[str(b)] = (counts, blk + int(blocking))
    return out


def _batch(r: dict[str, Any], counts: dict[str, tuple[dict[str, int], int]]) -> BatchOut:
    c, blk = counts.get(str(r["batch_id"]), ({}, 0))
    return BatchOut(**{**r, "dropped_columns": list(r["dropped_columns"] or [])}, issues=c,
                    open_blocking=blk)  # fmt: skip


@router.get("/ingest/batches", response_model=Page[BatchOut], summary="Các lần nạp")
def list_batches(
    conn: Conn,
    _user: Viewer,
    source_type: str | None = None,
    mode: Literal["history", "live"] | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    cursor: str | None = None,
) -> Page[BatchOut]:
    cond: list[str] = ["TRUE"]
    params: list[Any] = []
    if source_type:
        cond.append("source_type = ?")
        params.append(source_type)
    if mode:
        cond.append("mode = ?")
        params.append(mode)
    rows = fetch_all(
        conn,
        f"SELECT {_BATCH_COLS} FROM ingest_batches WHERE {' AND '.join(cond)}"
        " ORDER BY received_at DESC, batch_id",
        params,
    )
    page, nxt = paginate(rows, limit, cursor)
    counts = _issue_counts(conn)
    return Page[BatchOut](
        data=[_batch(r, counts) for r in page], meta=Meta(computed_at=utcnow()), next_cursor=nxt
    )


@router.get("/ingest/batches/{batch_id}", response_model=Envelope[BatchOut], summary="Một lần nạp")
def get_batch(batch_id: str, conn: Conn, _user: Viewer) -> Envelope[BatchOut]:
    r = fetch_one(conn, f"SELECT {_BATCH_COLS} FROM ingest_batches WHERE batch_id = ?", [batch_id])
    if r is None:
        raise NotFound(f"Không có lần nạp {batch_id}.", batch_id=batch_id)
    return Envelope[BatchOut](data=_batch(r, _issue_counts(conn)), meta=Meta(computed_at=utcnow()))


class SkuMatchIn(BaseModel):
    alias_id: str
    sku: str | None  # None = ignore (not a product of the shop)


class ConfirmIn(BaseModel):
    sku_matches: list[SkuMatchIn] = Field(default_factory=list)
    accept_remaining_auto: bool = True  # pending aliases keep waiting otherwise


def _find_file(settings: Settings, batch: dict[str, Any]) -> Path | None:
    """The original file of a batch: the upload folder, else the configured data folder.
    The SHA-256 must match ``ingest_batches.file_sha256``."""
    name = str(batch["file_name"])
    for cand in (_uploads_dir(settings) / str(batch["batch_id"]) / Path(name).name,
                 settings.data_dir / name):  # fmt: skip
        if cand.is_file() and hashlib.sha256(cand.read_bytes()).hexdigest() == batch["sha"]:
            return cand
    return None


@router.post(
    "/ingest/batches/{batch_id}/confirm",
    response_model=Envelope[UploadResult],
    summary="Chốt ghép SKU và nạp lại các dòng đang chờ",
)
def confirm_batch(
    batch_id: str, body: ConfirmIn, request: Request, conn: Conn, user: Operator
) -> Envelope[UploadResult]:
    b = fetch_one(
        conn,
        "SELECT batch_id, file_name, file_sha256 AS sha, mode FROM ingest_batches"
        " WHERE batch_id = ?",
        [batch_id],
    )
    if b is None:
        raise NotFound(f"Không có lần nạp {batch_id}.", batch_id=batch_id)
    for m in body.sku_matches:
        apply_confirmation(conn, m.alias_id, m.sku, user=user.id)
    settings: Settings = request.app.state.settings
    path = _find_file(settings, b)
    if path is None:
        raise ValidationFailed(
            "Không tìm thấy file gốc của lần nạp này để nạp lại. Tải lại file lên với force.",
            batch_id=batch_id,
        )
    with _INGEST_LOCK:
        report = ingest_files(conn, [path], force=True, build_panel=True)
    publish(request, *report.events)
    audit(
        conn,
        request,
        user,
        "ingest.confirm",
        "batch",
        batch_id,
        after={"sku_matches": [m.model_dump() for m in body.sku_matches]},
    )
    return Envelope[UploadResult](
        data=UploadResult(
            batches=[_uploaded(f) for f in report.files],
            pending_aliases=report.pending_aliases,
            skus_changed=len(report.skus_changed),
            panel_rows=report.panel.rows_written if report.panel else None,
        ),
        meta=Meta(computed_at=utcnow(), compute_state="fresh"),
    )


# ---------------------------------------------------------------------- SKU queue
class Candidate(BaseModel):
    sku: str
    score: float | None
    name: str | None = None


class AliasItem(BaseModel):
    alias_id: str
    alias: str  # raw name as written by the seller
    raw_code: str
    alias_kind: str  # "<source>_name", e.g. cost_name
    source_type: str
    status: str  # auto_accepted|pending|confirmed|rejected
    candidates: list[Candidate]
    selected: str | None
    method: str | None
    flag: Literal["check", "ok"]
    decided_by: str | None


class SkuQueue(BaseModel):
    summary: dict[str, int]
    items: list[AliasItem]


class SkuQueuePage(BaseModel):
    data: SkuQueue
    meta: Meta
    next_cursor: str | None = None


_QUEUE_STATUS = {
    "review": ("pending",),
    "auto": ("auto_accepted",),
    "decided": ("confirmed", "rejected"),
    "all": ("pending", "auto_accepted", "confirmed", "rejected"),
}


@router.get("/ingest/sku-queue", response_model=SkuQueuePage, summary="Hàng chờ ghép SKU")
def sku_queue(
    conn: Conn,
    _user: Operator,
    status: Literal["review", "auto", "decided", "all"] = "review",
    source_type: str | None = None,
    batch_id: Annotated[
        str | None, Query(description="Bỏ qua: alias không gắn với lần nạp (M3-api)")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    cursor: str | None = None,
) -> SkuQueuePage:
    _ = batch_id
    cond = ["list_contains(?, a.status)"]
    params: list[Any] = [list(_QUEUE_STATUS[status])]
    if source_type:
        cond.append("a.source_type = ?")
        params.append(source_type)
    rows = fetch_all(
        conn,
        "SELECT a.alias_id, a.source_type, a.raw_code, a.raw_name, a.sku_id, a.score, a.method,"
        " a.status, a.confirmed_by, coalesce(p.name, s.product_id) || CASE WHEN"
        " s.variant_name IS NOT NULL AND s.variant_name <> '' THEN ' - ' || s.variant_name"
        " ELSE '' END AS sku_name FROM sku_aliases a LEFT JOIN skus s USING (sku_id)"
        " LEFT JOIN products p ON p.product_id = s.product_id"
        f" WHERE {' AND '.join(cond)} ORDER BY a.status DESC, a.score NULLS FIRST, a.raw_name",
        params,
    )
    page, nxt = paginate(rows, limit, cursor)
    items = [
        AliasItem(
            alias_id=r["alias_id"],
            alias=r["raw_name"],
            raw_code=r["raw_code"],
            alias_kind=f"{r['source_type']}_name",
            source_type=r["source_type"],
            status=r["status"],
            candidates=[Candidate(sku=r["sku_id"], score=r["score"], name=r["sku_name"])]
            if r["sku_id"]
            else [],
            selected=r["sku_id"],
            method=r["method"],
            flag="check" if r["status"] == "pending" else "ok",
            decided_by=r["confirmed_by"],
        )
        for r in page
    ]
    summary = {
        "auto_matched": 0,
        "needs_review": 0,
        "confirmed": 0,
        "rejected": 0,
    }
    for st, n in conn.execute("SELECT status, count(*) FROM sku_aliases GROUP BY 1").fetchall():
        key = {"auto_accepted": "auto_matched", "pending": "needs_review"}.get(str(st), str(st))
        summary[key] = int(n)
    return SkuQueuePage(
        data=SkuQueue(summary=summary, items=items),
        meta=Meta(computed_at=utcnow()),
        next_cursor=nxt,
    )


class AliasDecision(BaseModel):
    sku: str | None = None
    action: Literal["confirm", "ignore"] = "confirm"


class AliasDecided(BaseModel):
    alias_id: str
    sku: str | None
    status: str
    decided_by: str


@router.post(
    "/ingest/sku-queue/{alias_id}",
    response_model=Envelope[AliasDecided],
    summary="Chốt một alias SKU",
)
def decide_alias(
    alias_id: str, body: AliasDecision, request: Request, conn: Conn, user: Operator
) -> Envelope[AliasDecided]:
    if body.action == "confirm" and not body.sku:
        raise ValidationFailed("Cần chọn SKU, hoặc gửi action = ignore.", alias_id=alias_id)
    sku = None if body.action == "ignore" else body.sku
    m = apply_confirmation(conn, alias_id, sku, user=user.id)
    audit(conn, request, user, "sku_alias.decide", "alias", alias_id, after=body.model_dump())
    return Envelope[AliasDecided](
        data=AliasDecided(alias_id=alias_id, sku=m.sku_id, status=m.status, decided_by=user.id),
        meta=Meta(computed_at=utcnow()),
    )


# ---------------------------------------------------------------------- data quality
SEVERITY_API = {"error": "blocking", "warn": "warning", "info": "info"}
SEVERITY_DB = {v: k for k, v in SEVERITY_API.items()}

_MSG: dict[str, str] = {
    "MISSING_COST": "Thiếu giá vốn, chưa tính được lợi nhuận",
    "NEG_STOCK": "Tồn âm ({value}) ngày {date}, đã bỏ qua dòng này",
    "DUP_ORDER": "Đơn trùng, đã bỏ bản lặp",
    "ADS_TZ_SHIFT": "File ads ghi giờ lệch {offset_hours} giờ, đã đổi về giờ Việt Nam",
    "ADS_GAP_DAY": "Thiếu số liệu ads trọn ngày {entity}",
    "ADS_LAG": "Số liệu ads trễ {lag_hours} giờ so với đơn hàng",
    "UNMATCHED_SKU": "Chưa ghép được '{raw_name}' với SKU nào (gợi ý {suggested_sku})",
    "PROVISIONAL_STATUS": "{orders} đơn chưa chốt trạng thái (đang giao / chờ lấy)",
    "BUNDLE_NO_STOCK_ROW": "Combo chưa khai thành phần nên không có dòng tồn",
    "PII_DROPPED": "Đã bỏ cột thông tin cá nhân: {columns}",
    "SUSPICIOUS_NOTE": "Ghi chú của người mua có câu giống lệnh, đã đánh dấu không tin cậy",
    "MONEY_FORMAT": "Không đọc được số tiền",
    "STOCK_JUMP": "Tồn đổi bất thường ({diff:+}), đã tin số đếm kho",
    "STOCK_ADJUSTMENT": "Điều chỉnh tồn có ghi chú: {note}",
    "SPEND_OVER_BUDGET": "Chi ads vượt ngân sách ngày",
    "ORDER_TOTAL_MISMATCH": "Tổng tiền đơn không khớp các dòng",
    "COST_NOTE_REVIEW": "Ghi chú giá vốn cần xem: {note}",
    "UNKNOWN_SOURCE": "Không nhận ra loại file",
    "UNPARSED_RULE": "Chưa hiểu một điều khoản chương trình sale",
}


def issue_message(code: str, detail: dict[str, Any], entity: str | None) -> str:
    tpl = _MSG.get(code, code)
    vals: dict[str, Any] = {"entity": entity or ""}
    for k, v in detail.items():
        vals[k] = ", ".join(map(str, v)) if isinstance(v, list) else v
    try:
        return tpl.format(**vals)
    except (KeyError, ValueError, IndexError):
        return _MSG.get(code, code).split("{")[0].rstrip(" ('") or code


class IssueOut(BaseModel):
    id: str
    kind: str
    severity: str  # blocking|warning|info
    status: str
    sku: str | None
    entity_type: str | None
    entity_id: str | None
    row_ref: str | None
    message: str
    detail: dict[str, Any]
    batch_id: str
    detected_at: LocalDatetime
    resolved_by: str | None


def _issues(
    conn: duckdb.DuckDBPyConnection,
    status: str | None,
    sku: str | None,
    severity: str | None,
    code: str | None,
    batch_id: str | None,
    limit: int,
    cursor: str | None,
) -> Page[IssueOut]:
    cond: list[str] = ["TRUE"]
    params: list[Any] = []
    if status and status != "all":
        cond.append("status = ?")
        params.append(status)
    if sku:
        cond.append("entity_id = ?")
        params.append(sku)
    if severity:
        cond.append("severity = ?")
        params.append(SEVERITY_DB.get(severity, severity))
    if code:
        cond.append("code = ?")
        params.append(code)
    if batch_id:
        cond.append("batch_id = ?")
        params.append(batch_id)
    rows = fetch_all(
        conn,
        "SELECT issue_id, batch_id, code, severity, entity_type, entity_id, row_ref, detail,"
        f" status, resolved_by, created_at FROM data_quality_issues WHERE ({') AND ('.join(cond)})"
        " ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,"
        " created_at DESC, issue_id",
        params,
    )
    page, nxt = paginate(rows, limit, cursor)
    out = []
    for r in page:
        det = parse_json(r["detail"]) or {}
        det = det if isinstance(det, dict) else {"value": det}
        out.append(
            IssueOut(
                id=r["issue_id"],
                kind=r["code"],
                severity=SEVERITY_API.get(r["severity"], r["severity"]),
                status=r["status"],
                sku=r["entity_id"] if r["entity_type"] in ("sku", "snapshot") else None,
                entity_type=r["entity_type"],
                entity_id=r["entity_id"],
                row_ref=r["row_ref"],
                message=issue_message(r["code"], det, r["entity_id"]),
                detail=det,
                batch_id=r["batch_id"],
                detected_at=r["created_at"],
                resolved_by=r["resolved_by"],
            )
        )
    return Page[IssueOut](data=out, meta=Meta(computed_at=utcnow()), next_cursor=nxt)


@router.get("/data-quality/issues", response_model=Page[IssueOut], summary="Vấn đề dữ liệu")
def list_issues(
    conn: Conn,
    _user: Viewer,
    status: str | None = "open",
    sku: str | None = None,
    severity: Literal["blocking", "warning", "info"] | None = None,
    code: str | None = None,
    batch_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 100,
    cursor: str | None = None,
) -> Page[IssueOut]:
    return _issues(conn, status, sku, severity, code, batch_id, limit, cursor)


class QualitySummary(BaseModel):
    open_by_severity: dict[str, int]
    open_by_code: dict[str, int]
    blocking_skus: list[str]
    total: int


@router.get("/data-quality", response_model=Envelope[QualitySummary], summary="Tóm tắt chất lượng")
def quality_summary(conn: Conn, _user: Viewer) -> Envelope[QualitySummary]:
    by_sev: dict[str, int] = {"blocking": 0, "warning": 0, "info": 0}
    by_code: dict[str, int] = {}
    for code, sev, n in conn.execute(
        "SELECT code, severity, count(*) FROM data_quality_issues WHERE status = 'open'"
        " GROUP BY 1, 2 ORDER BY 1"
    ).fetchall():
        key = SEVERITY_API.get(str(sev), str(sev))
        by_sev[key] = by_sev.get(key, 0) + int(n)
        by_code[str(code)] = by_code.get(str(code), 0) + int(n)
    blocking = [
        str(r[0])
        for r in conn.execute(
            "SELECT DISTINCT entity_id FROM data_quality_issues WHERE status = 'open'"
            " AND severity = 'error' AND entity_type = 'sku' AND entity_id IS NOT NULL ORDER BY 1"
        ).fetchall()
    ]
    total = int(conn.execute("SELECT count(*) FROM data_quality_issues").fetchone()[0])  # type: ignore[index]
    return Envelope[QualitySummary](
        data=QualitySummary(
            open_by_severity=by_sev, open_by_code=by_code, blocking_skus=blocking, total=total
        ),
        meta=Meta(computed_at=utcnow()),
    )
