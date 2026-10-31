"""Cell-by-cell comparison of panels (PNL-04 window mode vs whole-history build)."""

from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from app.panel.build import PanelFrames, compute_hourly_panel, load_panel

KEY = ["sku_id", "hour_utc"]


def frame_mismatches(got: pd.DataFrame, exp: pd.DataFrame) -> dict[str, int]:
    """Number of differing cells per column (``panel_version`` ignored); ``{"_shape": 1}``
    when the row sets differ."""
    a = got.drop(columns=["panel_version"]).sort_values(KEY).reset_index(drop=True)
    b = exp.drop(columns=["panel_version"]).sort_values(KEY).reset_index(drop=True)
    if a.shape != b.shape or not (a[KEY] == b[KEY]).all().all():
        return {"_shape": 1}
    out: dict[str, int] = {}
    for col in a.columns:
        x, y = a[col], b[col]
        if col == "data_flags":
            ne = np.array([list(p) != list(q) for p, q in zip(x, y, strict=True)])
        elif x.dtype.kind == "f" or y.dtype.kind == "f":
            xf, yf = x.astype(float).to_numpy(), y.astype(float).to_numpy()
            both_nan = np.isnan(xf) & np.isnan(yf)
            ne = ~(both_nan | (np.abs(xf - yf) <= 1e-9))
        else:
            ne = ~((x == y).fillna(False).to_numpy(dtype=bool) | (x.isna() & y.isna()).to_numpy())
        if ne.any():
            out[col] = int(ne.sum())
    return out


def price_mismatches(got: pd.DataFrame, exp: pd.DataFrame) -> int:
    cols = ["sku_id", "day_local", "list_price", "eff_price", "source"]
    a = got[cols].astype(str).sort_values(cols[:2]).reset_index(drop=True)
    b = exp[cols].astype(str).sort_values(cols[:2]).reset_index(drop=True)
    if a.shape != b.shape:
        return max(len(a), len(b))
    da = got.sort_values(cols[:2])["discount_pct"].to_numpy(dtype=float)
    db = exp.sort_values(cols[:2])["discount_pct"].to_numpy(dtype=float)
    return int((a != b).any(axis=1).sum()) + int((np.abs(da - db) > 1e-9).sum())


def full_frames(conn: duckdb.DuckDBPyConnection) -> PanelFrames:
    frames = compute_hourly_panel(conn, window=False)
    assert frames is not None
    return frames


def stored_vs_full(conn: duckdb.DuckDBPyConnection) -> tuple[dict[str, int], int]:
    """``hourly_panel`` + ``price_events`` in the DB vs a whole-history rebuild."""
    full = full_frames(conn)
    got = load_panel(conn)
    exp = full.panel.copy()
    exp["data_flags"] = [list(f) for f in exp["data_flags"]]
    cells = frame_mismatches(got, exp)
    pe = conn.execute(
        "SELECT sku_id, day_local, list_price, eff_price, discount_pct, source FROM price_events"
    ).df()
    pe["day_local"] = pd.to_datetime(pe["day_local"]).dt.date
    return cells, price_mismatches(pe, full.price_events)
