from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from pydantic import ValidationError

from app.forecast.base import (
    ForecastRequest,
    ForecastResult,
    ValidatedScope,
    make_rng,
    nb_quantiles,
    nb_samples,
)


def test_nb_quantiles_shapes_and_order() -> None:
    mean = np.array([0.1, 1.0, 5.0, 20.0] * 6)
    q = nb_quantiles(mean, 3.0, 0.2, make_rng(1), n=2000)
    for arr in (q.p10, q.p50, q.p90, q.cum_p10, q.cum_p50, q.cum_p90):
        assert arr.shape == mean.shape
    assert np.all(q.p10 <= q.p50) and np.all(q.p50 <= q.p90)
    assert np.all(np.diff(q.cum_p50) >= 0)
    assert np.all(q.cum_p10 <= q.cum_p90)


def test_nb_quantiles_reproducible_with_seed() -> None:
    mean = np.full(72, 2.0)
    a = nb_quantiles(mean, 3.0, 0.3, make_rng(7))
    b = nb_quantiles(mean, 3.0, 0.3, make_rng(7))
    assert np.array_equal(a.cum_p90, b.cum_p90)


def test_nb_samples_mean_and_dispersion() -> None:
    """No level noise: samples are NegBin(mean, k) → var = μ + μ²/k."""
    mu, k = 4.0, 3.0
    s = nb_samples(np.full(1, mu), k, 0.0, make_rng(3), n=200_000)[:, 0]
    assert abs(s.mean() - mu) < 0.05
    assert abs(s.var() - (mu + mu * mu / k)) < 0.2


def test_level_sigma_widens_cumulative_interval() -> None:
    mean = np.full(24, 3.0)
    narrow = nb_quantiles(mean, 5.0, 0.05, make_rng(5))
    wide = nb_quantiles(mean, 5.0, 0.5, make_rng(5))
    assert wide.cum_p90[-1] - wide.cum_p10[-1] > narrow.cum_p90[-1] - narrow.cum_p10[-1]


def test_result_schema_roundtrip() -> None:
    t = datetime(2026, 11, 10, 17, tzinfo=UTC)
    r = ForecastResult(
        forecast_id="fc-1",
        sku_id="X",
        model_id="profile_ewma@1.0.0",
        origin=t,
        hours=[t],
        mean=[1.0],
        p10=[0.0],
        p50=[1.0],
        p90=[2.0],
        dispersion_k=3.0,
        level_sigma=0.2,
        cum_p10=[0.0],
        cum_p50=[1.0],
        cum_p90=[2.0],
        validated_scope=ValidatedScope(model_id="m", demand_group="on_dinh", context="normal"),
        confidence="reference",
        reasons=["a"],
        data_as_of=t,
        panel_version="v",
    )
    back = ForecastResult.model_validate_json(r.model_dump_json())
    assert back == r


def test_request_validation() -> None:
    t = datetime(2026, 11, 10, 17, tzinfo=UTC)
    assert ForecastRequest(sku_id="X", origin=t).horizon_hours == 72
    with pytest.raises(ValidationError):
        ForecastRequest(sku_id="X", origin=t, horizon_hours=0)
    with pytest.raises(ValidationError):
        ForecastRequest(sku_id="X", origin=t, context="weird")  # type: ignore[arg-type]
