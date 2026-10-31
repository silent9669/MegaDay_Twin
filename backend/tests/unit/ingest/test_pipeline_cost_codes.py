"""Cost lines typed with the same name become separate aliases ``row:<n>``; unique
names keep the stable key ``(cost, '', name)`` (M1-pipeline "Hoàn thiện M1")."""

from __future__ import annotations

from app.ingest.pipeline import _cost_codes


def test_only_repeated_names_keep_their_row_code() -> None:
    names = ["Móc dán - gói 3", "Bình nước", "Móc dán - gói 3", "Áo"]
    codes = ["row:2", "row:3", "row:4", "row:5"]
    assert _cost_codes(names, codes) == ["row:2", "", "row:4", ""]


def test_codes_without_row_prefix_are_ignored() -> None:
    assert _cost_codes(["a", "a"], ["", "x"]) == ["", ""]
