"""Planning and dashboard clocks must use the same successfully ingested data."""

from datetime import UTC, datetime

import duckdb
import pytest

from app.api.common import data_as_of as dashboard_clock
from app.core.errors import ValidationFailed
from app.simulator.inputs import data_as_of as simulation_clock
from app.simulator.inputs import default_start


@pytest.mark.parametrize("has_valid_batch", [True, False])
def test_failed_future_batch_cannot_advance_planning_clock(has_valid_batch: bool) -> None:
    conn = duckdb.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE ingest_batches (source_type VARCHAR, as_of TIMESTAMPTZ, status VARCHAR)"
        )
        conn.execute(
            "INSERT INTO ingest_batches VALUES ('inventory', '2026-11-20 00:00:00+00', 'failed')"
        )
        if has_valid_batch:
            conn.execute(
                "INSERT INTO ingest_batches VALUES ('orders', '2026-11-10 17:00:00+00', 'ok')"
            )
        expected = datetime(2026, 11, 10, 17, tzinfo=UTC) if has_valid_batch else None
        assert dashboard_clock(conn) == expected
        assert simulation_clock(conn) == expected
        if has_valid_batch:
            assert default_start(conn) == expected
        else:
            with pytest.raises(ValidationFailed):
                default_start(conn)
    finally:
        conn.close()
