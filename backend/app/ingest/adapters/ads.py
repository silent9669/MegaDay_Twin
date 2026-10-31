"""Hourly ads report → ``ad_hourly``. Plan 02 §2.2, §3.2.

* ``Thời gian`` has no timezone. The offset comes from ``ctx.tz_offsets["ads"]``
  (already confirmed) or is detected against ``ctx.orders_hour_profile``
  (``app.ingest.timezone_check``) → ``ADS_TZ_SHIFT`` with the ``+7h`` proposal.
* A run of ≥ 20 hours with no row for any started campaign → ``ADS_GAP_DAY``;
  those hours get ``status='unknown'``, ``spend=NULL`` rows so nothing reads
  them as zero spend.
* Ads ending before the orders (``ctx.orders_as_of``) → ``ADS_LAG``; the
  missing tail hours also get ``unknown`` rows (a later batch overwrites them).
* ``Đơn hàng`` / ``Doanh thu`` are platform-attributed → ``attributed_*``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd

from app.core.timeutil import VN_TZ, local_date
from app.ingest.adapters._common import (
    parse_int_series,
    parse_money_series,
    parse_naive_series,
    shift_naive_to_utc,
)
from app.ingest.adapters.base import (
    DataIssue,
    IngestContext,
    NormFrame,
    RawFrame,
    SourceType,
    file_sha256,
    line_ref,
    make_issue,
    missing_columns_issue,
    read_csv_header,
    read_csv_str,
)
from app.ingest.pii import split_pii_columns
from app.ingest.timezone_check import check_ads_timezone

C_TIME = "Thời gian"
C_ID = "Mã chiến dịch"
C_NAME = "Tên chiến dịch"
C_STATUS = "Trạng thái"
C_BUDGET = "Ngân sách ngày"
C_SPEND = "Chi phí"
C_IMPR = "Lượt hiển thị"
C_CLICK = "Lượt click"
C_ORDERS = "Đơn hàng"
C_REVENUE = "Doanh thu"
REQUIRED = frozenset({C_TIME, C_ID, C_STATUS, C_SPEND})
OPTIONAL = (C_NAME, C_BUDGET, C_IMPR, C_CLICK, C_ORDERS, C_REVENUE)

STATUS_MAP: dict[str, str] = {
    "Đang chạy": "running",
    "Tạm dừng": "paused",
    "Hết ngân sách": "budget_exhausted",
}
GAP_MIN_HOURS = 20

AD_COLUMNS = [
    "campaign_id",
    "hour_utc",
    "status",
    "daily_budget",
    "spend",
    "impressions",
    "clicks",
    "attributed_orders",
    "attributed_revenue",
    "batch_id",
]


class AdsAdapter:
    source_type: ClassVar[SourceType] = SourceType.ADS
    header_signature: ClassVar[frozenset[str]] = frozenset({C_ID, C_SPEND, C_CLICK})

    def read(self, path: Path) -> RawFrame:
        path = Path(path)
        kept, dropped = split_pii_columns(read_csv_header(path))
        return RawFrame(
            source_type=self.source_type,
            df=read_csv_str(path, kept),
            file_name=path.name,
            file_sha256=file_sha256(path),
            dropped_columns=dropped,
        )

    def normalize(self, raw: RawFrame, ctx: IngestContext) -> NormFrame:
        df = raw.df.copy()
        issues: list[DataIssue] = []
        out = NormFrame(
            source_type=self.source_type,
            file_name=raw.file_name,
            file_sha256=raw.file_sha256,
            batch_id=ctx.batch_id,
            dropped_columns=list(raw.dropped_columns),
            row_count=len(df),
        )
        missing = set(REQUIRED) - set(df.columns)
        if missing:
            out.issues = [missing_columns_issue(missing, raw.file_name)]
            return out
        for c in OPTIONAL:
            if c not in df.columns:
                df[c] = ""
        df[C_ID] = df[C_ID].str.strip()

        naive = parse_naive_series(df[C_TIME])
        bad = naive.isna() | (df[C_ID] == "")
        for idx in df.index[bad]:
            issues.append(
                make_issue(
                    "ROW_INVALID",
                    entity_type="campaign",
                    entity_id=str(df.at[idx, C_ID]) or None,
                    row_ref=line_ref(int(idx)),
                    detail={"columns": [C_TIME, C_ID]},
                )
            )
        df, naive = df[~bad], naive[~bad]

        # ---- timezone (02 §3.2)
        offset, tz_issue, tz_detail = self._offset(df, naive, ctx)
        if tz_issue is not None:
            issues.append(tz_issue)
        out.tz_offset_hours = offset
        out.detail["timezone"] = tz_detail
        hour_utc = shift_naive_to_utc(naive, offset).dt.floor("h")

        # ---- values
        status_text = df[C_STATUS].str.strip()
        status = status_text.map(STATUS_MAP)
        for text, grp in df[status.isna()].groupby(C_STATUS):
            issues.append(
                make_issue(
                    "UNKNOWN_STATUS",
                    entity_type="file",
                    entity_id=raw.file_name,
                    detail={"value": str(text), "rows": len(grp), "mapped_to": "unknown"},
                )
            )
        status = status.fillna("unknown")
        budget, budget_bad = parse_money_series(df[C_BUDGET])
        spend, spend_bad = parse_money_series(df[C_SPEND])
        revenue, revenue_bad = parse_money_series(df[C_REVENUE])
        for idx in df.index[budget_bad | spend_bad | revenue_bad]:
            cols = [
                c
                for c, m in ((C_BUDGET, budget_bad), (C_SPEND, spend_bad), (C_REVENUE, revenue_bad))
                if m.at[idx]
            ]
            issues.append(
                make_issue(
                    "MONEY_FORMAT",
                    entity_type="campaign",
                    entity_id=str(df.at[idx, C_ID]),
                    row_ref=line_ref(int(idx)),
                    detail={"columns": cols, "value": [str(df.at[idx, c]) for c in cols]},
                )
            )

        ads = pd.DataFrame(
            {
                "campaign_id": df[C_ID],
                "hour_utc": hour_utc,
                "status": status,
                "daily_budget": budget,
                "spend": spend,
                "impressions": parse_int_series(df[C_IMPR]),
                "clicks": parse_int_series(df[C_CLICK]),
                "attributed_orders": parse_int_series(df[C_ORDERS]),
                "attributed_revenue": revenue,
                "batch_id": ctx.batch_id,
                "src_fill": pd.Series(None, index=df.index, dtype="object"),
            }
        )
        ads["_line"] = df.index
        dup = ads.duplicated(subset=["campaign_id", "hour_utc"], keep="last")
        for idx in ads.index[dup]:
            issues.append(
                make_issue(
                    "DUP_ROW",
                    entity_type="campaign",
                    entity_id=str(ads.at[idx, "campaign_id"]),
                    row_ref=line_ref(int(idx)),
                    detail={"key": ["campaign_id", "hour_utc"]},
                    status="auto_fixed",
                )
            )
        ads = ads[~dup]

        campaigns_seen = self._campaigns_seen(ads, df[C_NAME])
        fills: list[pd.DataFrame] = []
        if len(ads):
            gap_issues, gap_rows = self._gaps(ads, campaigns_seen, ctx.batch_id)
            issues.extend(gap_issues)
            fills.extend(gap_rows)
            ads_as_of = ads["hour_utc"].max().to_pydatetime() + timedelta(hours=1)
            out.as_of = ads_as_of
            if ctx.orders_as_of is not None and ads_as_of < ctx.orders_as_of:
                lag_issue, lag_rows = self._lag(
                    ads_as_of, ctx.orders_as_of, campaigns_seen, ctx.batch_id
                )
                issues.append(lag_issue)
                fills.append(lag_rows)

        ads = ads.drop(columns=["_line"])
        frames = [f for f in [ads, *fills] if len(f)]
        table = pd.concat(frames, ignore_index=True) if frames else ads
        table = table.sort_values(["campaign_id", "hour_utc"], kind="stable")
        out.tables = {"ad_hourly": table[[*AD_COLUMNS, "src_fill"]].reset_index(drop=True)}
        out.extras = {"campaigns_seen": campaigns_seen}
        out.issues = issues
        return out

    # ------------------------------------------------------------ helpers
    def _offset(
        self, df: pd.DataFrame, naive: pd.Series, ctx: IngestContext
    ) -> tuple[int, DataIssue | None, dict[str, Any]]:
        if SourceType.ADS.value in ctx.tz_offsets:
            off = int(ctx.tz_offsets[SourceType.ADS.value])
            return off, None, {"offset_hours": off, "source": "context"}
        if not ctx.orders_hour_profile:
            return 0, None, {"offset_hours": 0, "source": "not_checked_no_orders"}
        res = check_ads_timezone(
            ctx.orders_hour_profile,
            naive,
            df[C_SPEND],
            df[C_STATUS].str.strip().map(STATUS_MAP),
            campaign=df[C_ID],
            budget=df[C_BUDGET].str.strip(),
        )
        detail: dict[str, Any] = {
            "offset_hours": res.offset_hours if res.accepted else 0,
            "source": "detected" if res.accepted else "detected_unconfirmed",
            "proposed_offset_hours": res.offset_hours,
            "suggestion": res.suggestion,
            "accepted": res.accepted,
            "method": res.method,
            "signals": res.signals,
            "corr": res.corr,
            "best_corr_offset": res.best_corr_offset,
            "best_corr": res.best_corr,
            "n_days": res.n_days,
            "night_pause_share": res.night_pause_share,
            "needs_user_confirmation": True,
        }
        if res.offset_hours == 0:
            return 0, None, detail
        issue = make_issue(
            "ADS_TZ_SHIFT",
            entity_type="file",
            entity_id=ctx.batch_id,
            detail=detail,
            status="auto_fixed" if res.accepted else "open",
        )
        return (res.offset_hours if res.accepted else 0), issue, detail

    @staticmethod
    def _campaigns_seen(ads: pd.DataFrame, names: pd.Series) -> pd.DataFrame:
        if not len(ads):
            return pd.DataFrame(
                columns=["campaign_id", "name", "first_hour_utc", "last_hour_utc", "daily_budget"]
            )
        tmp = ads[["campaign_id", "hour_utc", "daily_budget"]].copy()
        tmp["name"] = names.reindex(tmp.index).str.strip()
        tmp = tmp.sort_values("hour_utc", kind="stable")
        g = tmp.groupby("campaign_id", sort=True)
        return pd.DataFrame(
            {
                "name": g["name"].last(),
                "first_hour_utc": g["hour_utc"].min(),
                "last_hour_utc": g["hour_utc"].max(),
                "daily_budget": g["daily_budget"].last(),
            }
        ).reset_index()

    @staticmethod
    def _unknown_rows(
        campaign_ids: list[str], hours: pd.DatetimeIndex, batch_id: str, fill: str
    ) -> pd.DataFrame:
        if not campaign_ids or not len(hours):
            return pd.DataFrame(columns=[*AD_COLUMNS, "src_fill"])
        idx = pd.MultiIndex.from_product([campaign_ids, hours], names=["campaign_id", "hour_utc"])
        rows = idx.to_frame(index=False)
        rows["status"] = "unknown"
        for c in ("daily_budget", "spend", "impressions", "clicks"):
            rows[c] = pd.Series(pd.NA, index=rows.index, dtype="Int64")
        for c in ("attributed_orders", "attributed_revenue"):
            rows[c] = pd.Series(pd.NA, index=rows.index, dtype="Int64")
        rows["batch_id"] = batch_id
        rows["src_fill"] = fill
        return rows[[*AD_COLUMNS, "src_fill"]]

    def _gaps(
        self, ads: pd.DataFrame, seen: pd.DataFrame, batch_id: str
    ) -> tuple[list[DataIssue], list[pd.DataFrame]]:
        start, end = ads["hour_utc"].min(), ads["hour_utc"].max()
        grid = pd.date_range(start, end, freq="h")
        present = pd.Index(ads["hour_utc"].unique())
        empty = grid[~grid.isin(present)]
        issues: list[DataIssue] = []
        fills: list[pd.DataFrame] = []
        if not len(empty):
            return issues, fills
        # consecutive runs of empty hours
        run_id = (pd.Series(empty).diff() != pd.Timedelta(hours=1)).cumsum()
        for _, run in pd.Series(empty).groupby(run_id.to_numpy()):
            if len(run) < GAP_MIN_HOURS:
                continue
            r0, r1 = run.iloc[0], run.iloc[-1] + pd.Timedelta(hours=1)
            started = seen[seen["first_hour_utc"] < r0]["campaign_id"].tolist()
            full_days = _full_local_days(r0.to_pydatetime(), r1.to_pydatetime())
            label = full_days[0] if full_days else str(local_date(r0.to_pydatetime()))
            issues.append(
                make_issue(
                    "ADS_GAP_DAY",
                    entity_type="file",
                    entity_id=label,
                    detail={
                        "start_utc": r0.isoformat(),
                        "end_utc": r1.isoformat(),
                        "hours": len(run),
                        "local_dates": full_days,
                        "campaigns": len(started),
                        "filled_as": "unknown",
                    },
                )
            )
            fills.append(self._unknown_rows(started, pd.DatetimeIndex(run), batch_id, "gap"))
        return issues, fills

    def _lag(
        self, ads_as_of: datetime, orders_as_of: datetime, seen: pd.DataFrame, batch_id: str
    ) -> tuple[DataIssue, pd.DataFrame]:
        hours = pd.date_range(ads_as_of, orders_as_of, freq="h", inclusive="left")
        last_hour = pd.Timestamp(ads_as_of) - pd.Timedelta(hours=1)
        active = seen[seen["last_hour_utc"] >= last_hour]["campaign_id"].tolist()
        issue = make_issue(
            "ADS_LAG",
            entity_type="file",
            entity_id=batch_id,
            detail={
                "ads_as_of": ads_as_of.isoformat(),
                "orders_as_of": orders_as_of.isoformat(),
                "lag_hours": len(hours),
                "campaigns": len(active),
                "filled_as": "unknown",
            },
        )
        return issue, self._unknown_rows(active, hours, batch_id, "lag")


def _full_local_days(start_utc: datetime, end_utc: datetime) -> list[str]:
    """VN calendar dates entirely inside [start_utc, end_utc)."""
    s = start_utc.astimezone(VN_TZ)
    e = end_utc.astimezone(VN_TZ)
    day = s.date() if (s.hour, s.minute) == (0, 0) else s.date() + timedelta(days=1)
    out: list[str] = []
    while True:
        day_end = datetime(day.year, day.month, day.day, tzinfo=VN_TZ) + timedelta(days=1)
        if day_end > e:
            break
        out.append(day.isoformat())
        day += timedelta(days=1)
    return out
