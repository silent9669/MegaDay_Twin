"""Panel → dense ``[sku, hour]`` arrays, and the VN calendar of any hour range.

The panel (``app.panel``) is the only DataFrame crossing into this layer (01 §7). The
forecast code works on dense numpy arrays because a backtest fits ~6 models × 300 SKUs ×
20 origins; everything per origin is vectorised over SKUs (``context.py``).

The hour grid is aligned on VN midnight (the panel always starts at the VN midnight of the
first order, M1-panel P1), so ``y.reshape(S, days, 24)`` gives local days.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import numpy.typing as npt
import pandas as pd

from app.panel.build import GIFT_PEAKS, TET

FloatArr = npt.NDArray[np.float64]
IntArr = npt.NDArray[np.int64]
BoolArr = npt.NDArray[np.bool_]

VN_OFFSET = pd.Timedelta(hours=7)  # Vietnam has no DST
HOUR = pd.Timedelta(hours=1)


@dataclass(frozen=True)
class Calendar:
    """Per-hour calendar features (VN local) for a contiguous UTC hour range."""

    hours: pd.DatetimeIndex  # UTC
    hour_local: IntArr
    dow: IntArr  # Monday = 0
    day: npt.NDArray[np.datetime64]  # local date (datetime64[D])
    is_dd: BoolArr
    is_pre: BoolArr
    is_post: BoolArr
    is_payday: BoolArr
    days_to_tet: FloatArr
    days_to_gift: FloatArr  # signed: > 0 before the nearest gift peak (20/10, 20/11)

    def __len__(self) -> int:
        return len(self.hours)


def _days_to_gift(days: pd.DatetimeIndex) -> FloatArr:
    out = np.empty(len(days), dtype=np.float64)
    years = days.year.to_numpy()
    base = days.to_numpy().astype("datetime64[D]")
    for i, (y, d) in enumerate(zip(years, base, strict=True)):
        best: float | None = None
        for yy in (y - 1, y, y + 1):
            for m, dd in GIFT_PEAKS:
                diff = float((np.datetime64(date(int(yy), m, dd)) - d).astype(int))
                if (
                    best is None
                    or abs(diff) < abs(best)
                    or (abs(diff) == abs(best) and diff > best)
                ):
                    best = diff
        out[i] = best if best is not None else np.nan
    return out


def day_flags(days: pd.DatetimeIndex) -> dict[str, npt.NDArray[np.generic]]:
    """Flags of local calendar days (naive midnight timestamps)."""
    nxt = days + pd.Timedelta(days=1)
    prv = days - pd.Timedelta(days=1)
    dd = np.asarray(days.month == days.day, dtype=bool)
    tet = np.datetime64(TET)
    return {
        "is_dd": dd,
        "is_pre": np.asarray(nxt.month == nxt.day, dtype=bool),
        "is_post": np.asarray(prv.month == prv.day, dtype=bool),
        "is_payday": np.asarray(days.day == 25, dtype=bool),
        "days_to_tet": (tet - days.to_numpy().astype("datetime64[D]"))
        .astype(np.int64)
        .astype(np.float64),
        "days_to_gift": _days_to_gift(days),
        "dow": np.asarray(days.day_of_week, dtype=np.int64),
    }


def calendar_for(start: datetime | pd.Timestamp, n_hours: int) -> Calendar:
    """Calendar of ``n_hours`` hours starting at ``start`` (UTC, hour aligned)."""
    hours = pd.date_range(pd.Timestamp(start).tz_convert("UTC"), periods=n_hours, freq="h")
    local = (hours + VN_OFFSET).tz_localize(None)
    day_ts = local.normalize()
    uniq, inv = np.unique(day_ts.to_numpy(), return_inverse=True)
    flags = day_flags(pd.DatetimeIndex(uniq))
    return Calendar(
        hours=hours,
        hour_local=local.hour.to_numpy().astype(np.int64),
        dow=np.asarray(flags["dow"])[inv].astype(np.int64),
        day=day_ts.to_numpy().astype("datetime64[D]"),
        is_dd=np.asarray(flags["is_dd"], dtype=bool)[inv],
        is_pre=np.asarray(flags["is_pre"], dtype=bool)[inv],
        is_post=np.asarray(flags["is_post"], dtype=bool)[inv],
        is_payday=np.asarray(flags["is_payday"], dtype=bool)[inv],
        days_to_tet=np.asarray(flags["days_to_tet"], dtype=np.float64)[inv],
        days_to_gift=np.asarray(flags["days_to_gift"], dtype=np.float64)[inv],
    )


@dataclass
class PanelMatrix:
    """Dense view of ``hourly_panel``: arrays ``[S, T]`` with ``T = n_days × 24``.

    Missing (sku, hour) rows are ``present = False`` (no information, weight 0)."""

    sku_ids: list[str]
    start: pd.Timestamp  # UTC, VN midnight
    n_days: int
    y: FloatArr  # units_gross (0 where absent)
    present: BoolArr
    stockout: BoolArr
    conf: FloatArr  # stockout_conf (1.0 when null)
    disc: FloatArr  # discount_pct (0.0 when null)
    stock_end: FloatArr  # NaN when unknown
    list_price: FloatArr  # [S] latest known list price (NaN if unknown)
    panel_version: str
    product_id: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    end: pd.Timestamp | None = None  # first hour without data (UTC)

    @property
    def index(self) -> dict[str, int]:
        return {s: i for i, s in enumerate(self.sku_ids)}

    @property
    def n_hours(self) -> int:
        return self.n_days * 24

    def hour_index(self, ts: datetime | pd.Timestamp) -> int:
        """Index of the hour containing ``ts`` (may be < 0 or > T)."""
        delta = pd.Timestamp(ts).tz_convert("UTC") - self.start
        return int(np.floor(delta / HOUR))

    def calendar(self) -> Calendar:
        return calendar_for(self.start, self.n_hours)


def _vn_midnight(ts: pd.Timestamp) -> pd.Timestamp:
    local = (ts + VN_OFFSET).normalize()
    return local - VN_OFFSET


def panel_matrix(
    panel: pd.DataFrame,
    *,
    product_id: Mapping[str, str] | None = None,
    names: Mapping[str, str] | None = None,
    pad_until: datetime | None = None,
) -> PanelMatrix:
    """Pivot a (validated) panel DataFrame to dense arrays. Future rows are allowed in
    ``panel`` (they are only read up to an origin by :mod:`app.forecast.context`).
    ``pad_until`` extends the grid (absent hours) so an origin past the data still fits."""
    if panel.empty:
        raise ValueError("panel rỗng: chưa có dữ liệu để dự báo")
    hours = pd.DatetimeIndex(panel["hour_utc"]).tz_convert("UTC")
    start = _vn_midnight(hours.min())
    last = hours.max() + HOUR
    if pad_until is not None:
        last = max(last, pd.Timestamp(pad_until).tz_convert("UTC"))
    n_days = int(np.ceil((last - start) / pd.Timedelta(days=1)))
    n_hours = n_days * 24
    sku_ids = sorted(str(s) for s in panel["sku_id"].unique())
    sidx = {s: i for i, s in enumerate(sku_ids)}
    si = panel["sku_id"].astype(str).map(sidx).to_numpy()
    ti = ((hours - start) / HOUR).to_numpy().astype(np.int64)
    shape = (len(sku_ids), n_hours)

    def grid(values: npt.ArrayLike, fill: float) -> FloatArr:
        out = np.full(shape, fill, dtype=np.float64)
        out[si, ti] = np.asarray(values, dtype=np.float64)
        return out

    y = grid(panel["units_gross"].to_numpy(dtype=np.float64), 0.0)
    y = np.nan_to_num(y, nan=0.0)
    present = np.zeros(shape, dtype=bool)
    present[si, ti] = True
    so = np.zeros(shape, dtype=bool)
    so[si, ti] = panel["stockout"].to_numpy(dtype=bool)
    conf = grid(pd.to_numeric(panel["stockout_conf"]).fillna(1.0).to_numpy(), 1.0)
    disc = grid(pd.to_numeric(panel["discount_pct"]).fillna(0.0).to_numpy(), 0.0)
    disc = np.clip(np.nan_to_num(disc, nan=0.0), 0.0, 0.95)
    stock_end = grid(
        pd.to_numeric(panel["stock_end"])
        .astype("Float64")
        .to_numpy(dtype=np.float64, na_value=np.nan),
        np.nan,
    )
    lp = (
        panel.dropna(subset=["list_price"])
        .sort_values("hour_utc")
        .groupby("sku_id")["list_price"]
        .last()
    )
    list_price = np.array([float(lp.get(s, np.nan)) for s in sku_ids], dtype=np.float64)
    versions = panel["panel_version"].dropna().astype(str)
    pv = str(versions.max()) if len(versions) else ""
    return PanelMatrix(
        sku_ids=sku_ids,
        start=start,
        n_days=n_days,
        y=y,
        present=present,
        stockout=so,
        conf=conf,
        disc=disc,
        stock_end=stock_end,
        list_price=list_price,
        panel_version=pv,
        product_id=dict(product_id or {}),
        names=dict(names or {}),
        end=hours.max() + HOUR,
    )


def day_start_utc(d: date) -> pd.Timestamp:
    return pd.Timestamp(datetime(d.year, d.month, d.day)).tz_localize("UTC") - VN_OFFSET


def vn_midnight_utc(d: date) -> datetime:
    return day_start_utc(d).to_pydatetime()


def add_hours(ts: datetime, n: int) -> datetime:
    return ts + timedelta(hours=n)
