"""ECO-02 · parameter catalogue, versions, ai_suggested, resolution (05 §3)."""

from __future__ import annotations

import duckdb
import pytest

from app.core.errors import Forbidden, ParamOutOfRange, ValidationFailed
from app.economics.parameters import (
    CATALOGUE,
    SCENARIO_KEYS,
    confirm_suggestion,
    history,
    latest_effective,
    load_param_set,
    parse_overrides,
    set_parameter,
    validate_value,
)
from app.warehouse.db import migrate


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    migrate(c)
    return c


def test_catalogue_matches_plan() -> None:
    assert len(CATALOGUE) == 17  # economics + the two bounded forecast-selection controls
    for key, default, lo, hi in (
        ("forecast_min_history_days", 14, 14, 90),
        ("forecast_sparse_adi", 1.32, 1, 7),
    ):
        policy = CATALOGUE[key]
        assert (policy.default, policy.min, policy.max) == (default, lo, hi)
        assert policy.editable_by == {"lead"} and policy.scopes == {"shop"}
        with pytest.raises(ParamOutOfRange):
            validate_value(key, lo - 1)
        with pytest.raises(ParamOutOfRange):
            validate_value(key, hi + 1)
    d = CATALOGUE["platform_fee_rate"]
    assert (d.default, d.min, d.max, d.scopes, d.editable_by) == (0.12, 0, 0.3, {"shop"}, {"lead"})
    assert CATALOGUE["reduce_budget_options"].default_list == [0.3, 0.5]
    assert CATALOGUE["decision_min_gain_vnd"].default == 200_000
    assert {
        "rank_loss_after_pause",
        "stockout_penalty_per_unit",
        "restock_delay_days",
    } == SCENARIO_KEYS
    for d in CATALOGUE.values():
        assert d.label_vi and d.unit and d.scopes and d.editable_by and d.source_hint


def test_out_of_range_vietnamese() -> None:
    with pytest.raises(ParamOutOfRange) as e:
        validate_value("platform_fee_rate", 0.5)
    assert "Phí sàn" in e.value.message and "0–0,3" in e.value.message
    with pytest.raises(ParamOutOfRange):
        validate_value("reduce_budget_options", [0.05])
    with pytest.raises(ParamOutOfRange):
        validate_value("leftover_value_per_unit", 600_000, upper=531_000)
    assert validate_value("leftover_value_per_unit", 20_000, upper=531_000) == 20_000
    with pytest.raises(ParamOutOfRange):
        validate_value("no_such_key", 1)


def test_leftover_value_is_net_of_cost_default_zero() -> None:
    """M3-whatif §3: V is net of cost like m, default 0 (not 0,5 × cost); may be negative
    down to −cost (recovered below cost), never below."""
    d = CATALOGUE["leftover_value_per_unit"]
    assert d.default == 0.0 and d.default_rule and "giá vốn" in d.label_vi
    assert validate_value("leftover_value_per_unit", -100_000, lower=-455_000) == -100_000
    with pytest.raises(ParamOutOfRange) as e:
        validate_value("leftover_value_per_unit", -500_000, lower=-455_000, upper=690_000)
    assert e.value.details["min"] == -455_000 and e.value.details["max"] == 690_000
    with pytest.raises(ParamOutOfRange):
        validate_value("leftover_value_per_unit", float("-inf"))


def test_versions_and_history(conn: duckdb.DuckDBPyConnection) -> None:
    a = set_parameter(conn, "platform_fee_rate", 0.1, set_by="lead", reason="hợp đồng mới")
    b = set_parameter(conn, "platform_fee_rate", 0.11, set_by="lead")
    assert (a.version, b.version) == (1, 2)
    assert [p.value for p in history(conn, "platform_fee_rate")] == [0.1, 0.11]
    assert latest_effective(conn)[("platform_fee_rate", "shop", "*")].version == 2


def test_role_and_scope_checks(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(Forbidden):
        set_parameter(conn, "platform_fee_rate", 0.1, set_by="operator")
    with pytest.raises(ValidationFailed):
        set_parameter(conn, "platform_fee_rate", 0.1, scope="sku", scope_id="X", set_by="lead")
    # lead may edit operator parameters
    set_parameter(conn, "safety_stock", 5, scope="sku", scope_id="X", set_by="lead")


def test_ai_suggestion_not_effective_until_confirmed(conn: duckdb.DuckDBPyConnection) -> None:
    s = set_parameter(
        conn,
        "leftover_value_per_unit",
        20_000,
        scope="sku",
        scope_id="NOICHIEN2L",
        set_by="ai_suggested",
        reason="thanh lý 20k",
    )
    ps = load_param_set(conn)
    assert ps.num("leftover_value_per_unit", "NOICHIEN2L") == 0.0  # catalogue default
    confirm_suggestion(
        conn, "leftover_value_per_unit", scope="sku", scope_id="NOICHIEN2L", version=s.version
    )
    ps = load_param_set(conn)
    r = ps.resolve("leftover_value_per_unit", "NOICHIEN2L")
    assert r.value == 20_000 and r.set_by == "ai_suggested_confirmed" and r.version == 2
    assert ps.versions == {"leftover_value_per_unit@NOICHIEN2L": 2}


def test_resolution_order(conn: duckdb.DuckDBPyConnection) -> None:
    set_parameter(
        conn, "leftover_value_per_unit", 10_000, scope="sku", scope_id="A", set_by="operator"
    )
    set_parameter(
        conn, "leftover_value_per_unit", 5_000, scope="session", scope_id="ca-1", set_by="operator"
    )
    ps = load_param_set(conn, session_id="ca-1")
    assert ps.num("leftover_value_per_unit", "A") == 5_000  # session wins
    ps = load_param_set(conn)
    assert ps.num("leftover_value_per_unit", "A") == 10_000
    assert ps.num("leftover_value_per_unit", "B") == 0.0  # catalogue default (net of cost)
    assert ps.resolve("platform_fee_rate").version == 0  # catalogue default
    ps = load_param_set(conn, overrides={"leftover_value_per_unit@A": 1_000})
    r = ps.resolve("leftover_value_per_unit", "A")
    assert r.value == 1_000 and r.version == -1


def test_overrides_and_scenario_flags(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ParamOutOfRange):
        parse_overrides({"unknown_param": 1})
    with pytest.raises(ParamOutOfRange):
        parse_overrides({"decision_min_prob": 0.99})
    assert parse_overrides({"restock_delay_days": -1}) == {"restock_delay_days": -1.0}
    ps = load_param_set(conn, overrides={"rank_loss_after_pause": 0.1})
    assert ps.scenario_active() == ["rank_loss_after_pause"]
    assert load_param_set(conn).scenario_active() == []
