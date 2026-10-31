"""Sale program JSON → ``sale_commitments`` (typed constraint rows). Plan 02 §2.6.

| kind              | from                              | value / window                     |
|-------------------|-----------------------------------|------------------------------------|
| committed_price   | ``cam_ket[].gia_cam_ket``         | ``value_int`` = price (VND)        |
| flash_reserve     | ``cam_ket[].ton_giu_cho_flash_sale`` | ``value_int`` = units held |
| flash_window      | ``khung_flash_sale[]``            | ``window_start/end`` (VN → UTC)    |
| campaign_must_run | ``quy_dinh_chien_dich[]`` (*)     | one row per flash window           |
| rule_text         | ``quy_dinh_chung[]``, other rules | ``raw_text`` (+ ``UNPARSED_RULE``) |

(*) rule text matching "Không tắt chiến dịch trong khung flash sale".

``commitment_id`` is deterministic (hash of program, day, kind, target,
position) so re-ingesting the same file upserts the same rows.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
from unidecode import unidecode

from app.core.money import parse_vnd
from app.core.timeutil import VN_TZ
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

COMMITMENT_COLUMNS = [
    "commitment_id",
    "program",
    "day_local",
    "kind",
    "sku_id",
    "campaign_id",
    "value_int",
    "window_start",
    "window_end",
    "raw_text",
]
_WINDOW = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})\s*$")
_MUST_RUN = re.compile(
    r"khong (duoc )?(tat|dung|tam dung|pause) (cac )?chien dich.*(khung|trong) (gio )?flash ?sale"
)


def parse_window(text: str, day: date) -> tuple[datetime, datetime] | None:
    """``"12:00-14:00"`` on ``day`` (VN) → UTC [start, end). ``24:00`` and
    windows crossing midnight are supported."""
    m = _WINDOW.match(text)
    if not m:
        return None
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    if h1 > 24 or h2 > 24 or m1 > 59 or m2 > 59:
        return None
    base = datetime(day.year, day.month, day.day, tzinfo=VN_TZ)
    start = base + timedelta(hours=h1, minutes=m1)
    end = base + timedelta(hours=h2, minutes=m2)
    if end <= start:
        end += timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def is_must_run_rule(text: str) -> bool:
    return bool(_MUST_RUN.search(re.sub(r"\s+", " ", unidecode(text).lower())))


def _cid(*parts: Any) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return "sc-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float | str):
        return parse_vnd(value)
    return None


class SaleProgramAdapter:
    source_type: ClassVar[SourceType] = SourceType.SALE_PROGRAM
    header_signature: ClassVar[frozenset[str]] = frozenset({"cam_ket", "khung_flash_sale"})

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
        programs = raw.payload if isinstance(raw.payload, list) else [raw.payload]
        rows: list[dict[str, Any]] = []
        issues: list[DataIssue] = []
        for p_idx, prog in enumerate(programs):
            if isinstance(prog, dict):
                r, i = self._program(prog, p_idx)
                rows.extend(r)
                issues.extend(i)
            else:
                issues.append(
                    make_issue(
                        "ROW_INVALID", row_ref=f"program {p_idx}", detail={"reason": "not_object"}
                    )
                )
        df = pd.DataFrame(rows, columns=COMMITMENT_COLUMNS)
        for c in ("window_start", "window_end"):
            df[c] = pd.to_datetime(df[c], utc=True)
        df["value_int"] = df["value_int"].astype("Int64")
        return NormFrame(
            source_type=self.source_type,
            file_name=raw.file_name,
            file_sha256=raw.file_sha256,
            batch_id=ctx.batch_id,
            row_count=len(programs),
            tables={"sale_commitments": df},
            issues=issues,
            untrusted_columns={"sale_commitments": ["raw_text"]},
            as_of=ctx.received_at,
        )

    def _program(
        self, prog: dict[str, Any], p_idx: int
    ) -> tuple[list[dict[str, Any]], list[DataIssue]]:
        rows: list[dict[str, Any]] = []
        issues: list[DataIssue] = []
        name = str(prog.get("chuong_trinh") or "").strip() or f"program {p_idx}"
        try:
            day = date.fromisoformat(str(prog.get("ngay", "")).strip())
        except ValueError:
            return rows, [
                make_issue(
                    "ROW_INVALID",
                    entity_type="file",
                    row_ref=f"program {p_idx}",
                    detail={"columns": ["ngay"], "value": str(prog.get("ngay"))},
                )
            ]

        def add(kind: str, pos: int, **kw: Any) -> None:
            row = dict.fromkeys(COMMITMENT_COLUMNS)
            row.update(program=name, day_local=day, kind=kind, **kw)
            row["commitment_id"] = _cid(
                name, day, kind, kw.get("sku_id"), kw.get("campaign_id"), pos
            )
            rows.append(row)

        windows: list[tuple[datetime, datetime, str]] = []
        for i, w in enumerate(prog.get("khung_flash_sale") or []):
            parsed = parse_window(str(w), day)
            if parsed is None:
                issues.append(
                    make_issue(
                        "UNPARSED_RULE",
                        entity_type="file",
                        row_ref=f"khung_flash_sale[{i}]",
                        detail={"value": str(w)},
                    )
                )
                continue
            windows.append((parsed[0], parsed[1], str(w)))
            add("flash_window", i, window_start=parsed[0], window_end=parsed[1], raw_text=str(w))

        for i, c in enumerate(prog.get("cam_ket") or []):
            if not isinstance(c, dict) or not str(c.get("sku") or "").strip():
                issues.append(
                    make_issue("ROW_INVALID", row_ref=f"cam_ket[{i}]", detail={"columns": ["sku"]})
                )
                continue
            sku = str(c["sku"]).strip()
            if "gia_cam_ket" in c:
                price = _int(c.get("gia_cam_ket"))
                if price is None:
                    issues.append(
                        make_issue(
                            "MONEY_FORMAT",
                            entity_type="sku",
                            entity_id=sku,
                            row_ref=f"cam_ket[{i}]",
                            detail={"column": "gia_cam_ket", "value": str(c.get("gia_cam_ket"))},
                        )
                    )
                else:
                    add("committed_price", i, sku_id=sku, value_int=price)
            if "ton_giu_cho_flash_sale" in c:
                qty = _int(c.get("ton_giu_cho_flash_sale"))
                if qty is None or qty < 0:
                    issues.append(
                        make_issue(
                            "ROW_INVALID",
                            entity_type="sku",
                            entity_id=sku,
                            row_ref=f"cam_ket[{i}]",
                            detail={"columns": ["ton_giu_cho_flash_sale"]},
                        )
                    )
                else:
                    add("flash_reserve", i, sku_id=sku, value_int=qty)

        for i, r in enumerate(prog.get("quy_dinh_chien_dich") or []):
            if not isinstance(r, dict):
                continue
            cid = str(r.get("ma_chien_dich") or "").strip() or None
            text = str(r.get("quy_dinh") or "").strip()
            if cid and is_must_run_rule(text) and windows:
                for w_idx, (ws, we, _) in enumerate(windows):
                    add(
                        "campaign_must_run",
                        i * 100 + w_idx,
                        campaign_id=cid,
                        window_start=ws,
                        window_end=we,
                        raw_text=text,
                    )
                continue
            add("rule_text", i, campaign_id=cid, raw_text=text)
            issues.append(
                make_issue(
                    "UNPARSED_RULE",
                    entity_type="campaign",
                    entity_id=cid,
                    row_ref=f"quy_dinh_chien_dich[{i}]",
                    detail={"text": text, "trust": "untrusted"},
                )
            )

        for i, text in enumerate(prog.get("quy_dinh_chung") or []):
            if str(text).strip():
                add("rule_text", 10_000 + i, raw_text=str(text).strip())
        return rows, issues
