from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.forecast.registry import ChampionInfo
from app.forecast.selection import select_model


def context(age: int = 40, adi: float = 1.0, group: str = "on_dinh") -> Any:
    return SimpleNamespace(
        groups=[group],
        sku_ids=["SKU"],
        kinds=[None],
        names={"SKU": "Cốc"},
        features={"SKU": {"ADI": adi, "peak_ratio": 1}},
        first_day=[0],
        history_days=lambda _: age,
        group_source={"SKU": "auto"},
    )


def test_new_and_sparse_are_separate_from_primary_label() -> None:
    base = ChampionInfo("profile_ewma@1.0.0", None, False, False)
    label = {"label": "Seasonal"}
    model, evidence = select_model(context(age=7), 0, base, label)
    assert model.model_id.startswith("analog")
    assert evidence["primary_label"] == "Seasonal"
    assert evidence["data_status"]["new"]
    model, evidence = select_model(context(adi=3), 0, base, label)
    assert model.model_id.startswith("tsb")
    assert evidence["primary_label"] == "Seasonal"


def test_unknown_season_never_assumes_a_gift_calendar() -> None:
    base = ChampionInfo("seasonal_calendar@1.0.0", None, False, False)
    model, evidence = select_model(context(), 0, base, {"label": "Seasonal"})
    assert model.model_id.startswith("profile_ewma")
    assert any("lịch mùa" in reason for reason in evidence["reasons"])


def test_label_prior_and_validated_champion_are_distinguished() -> None:
    base = ChampionInfo("profile_ewma@1.0.0", None, False, False)
    chosen, _ = select_model(context(), 0, base, {"label": "Trendy"})
    assert chosen.model_id.startswith("trend_damped") and chosen.scope is None
    # Any actual scope is sufficient here: selection must retain, not reinterpret, it.
    scored: Any = SimpleNamespace(model_id="profile_ewma@1.0.0")
    base = ChampionInfo("profile_ewma@1.0.0", scored, True, True)
    chosen, evidence = select_model(context(), 0, base, {"label": "Trendy"})
    assert chosen is base and evidence["from_backtest"]
    # Backtested selection can beat a generic age/ADI default without changing
    # the independent history flags or claiming the AI label validates it.
    chosen, evidence = select_model(context(age=7, adi=3), 0, base, {"label": "Seasonal"})
    assert chosen is base and evidence["from_backtest"]
    assert evidence["data_status"]["new"] and evidence["data_status"]["sparse"]
    chosen, evidence = select_model(context(), 0, base, {"label": "Trendy"}, drift=True)
    assert chosen.model_id.startswith("nowcast") and chosen.scope is None
    assert not evidence["from_backtest"]


def test_configurable_history_gate_and_manual_override() -> None:
    base = ChampionInfo("trend_damped@1.0.0", None, False, False)
    ctx = context(age=20)
    selected, _ = select_model(ctx, 0, base, {"label": "Trendy"}, min_history=30)
    assert selected.model_id.startswith("analog")
    ctx.group_source = {"SKU": "user"}
    selected, evidence = select_model(ctx, 0, base, {"label": "Normal"}, min_history=30)
    assert selected is base
    assert evidence["primary_label"] == "Normal"
