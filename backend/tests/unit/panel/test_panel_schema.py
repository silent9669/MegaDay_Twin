"""validate_panel: the DataFrame contract between panel and forecast / ads_effect."""

from __future__ import annotations

import pandas as pd
import pytest

from app.panel.schema import COLUMN_NAMES, PanelSchemaError, validate_panel
from tests.unit.panel.conftest import Built


def _sample(b: Built) -> pd.DataFrame:
    return b.panel.head(200).copy()


def test_valid_panel_passes(nho: Built) -> None:
    df = _sample(nho)
    assert validate_panel(df) is df


def test_missing_column(nho: Built) -> None:
    with pytest.raises(PanelSchemaError, match="thiếu cột"):
        validate_panel(_sample(nho).drop(columns=["stockout"]))


def test_naive_timestamps_rejected(nho: Built) -> None:
    df = _sample(nho)
    df["hour_utc"] = df["hour_utc"].dt.tz_localize(None)
    with pytest.raises(PanelSchemaError, match="UTC"):
        validate_panel(df)


@pytest.mark.parametrize(
    ("col", "value", "msg"),
    [
        ("hour_local", 5, "hour_local"),
        ("units_gross", -1, "units_gross"),
        ("stock_source", "guess", "stock_source"),
        ("ad_status", "on", "ad_status"),
        ("stockout_conf", 1.5, "stockout_conf"),
    ],
)
def test_bad_values(nho: Built, col: str, value: object, msg: str) -> None:
    df = _sample(nho)
    df.loc[df.index[3], col] = value
    with pytest.raises(PanelSchemaError, match=msg):
        validate_panel(df)


def test_duplicates_and_nulls(nho: Built) -> None:
    df = _sample(nho)
    with pytest.raises(PanelSchemaError, match="trùng"):
        validate_panel(pd.concat([df, df.head(1)]))
    df2 = _sample(nho)
    df2["panel_version"] = pd.array([None] * len(df2), dtype="string")
    with pytest.raises(PanelSchemaError, match="panel_version"):
        validate_panel(df2)


def test_unknown_ads_must_have_null_spend(nho: Built) -> None:
    df = _sample(nho)
    df.loc[df.index[0], "ad_status"] = "unknown"
    df.loc[df.index[0], "ad_spend_alloc"] = 10.0
    with pytest.raises(PanelSchemaError, match="unknown"):
        validate_panel(df)


def test_column_list_is_complete() -> None:
    assert len(COLUMN_NAMES) == 34 and COLUMN_NAMES[:2] == ("sku_id", "hour_utc")
