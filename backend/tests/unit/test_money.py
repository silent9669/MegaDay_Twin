"""parse_vnd: cases from 02 §2.4 and 11 §1."""

from __future__ import annotations

import pytest

from app.core.money import format_vnd, parse_vnd


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 02 §2.4 mandatory cases
        ("45000", 45_000),
        ("45.000", 45_000),
        ("45k", 45_000),
        ("45.000đ", 45_000),
        ("455.000đ", 455_000),
        ("3k", 3_000),
        ("1,5tr", 1_500_000),
        # 11 §1
        ("1,2tr", 1_200_000),
        # whitespace / casing / currency variants
        ("  45000  ", 45_000),
        ("45 000", 45_000),
        ("45 000 đ", 45_000),
        ("45.000 đ", 45_000),
        ("45K", 45_000),
        ("45.000 VNĐ", 45_000),
        ("45.000vnd", 45_000),
        ("45.000 đồng", 45_000),
        ("₫45.000", 45_000),
        ("45.000₫", 45_000),
        ("1.234.567", 1_234_567),
        ("1.5tr", 1_500_000),
        ("2tr", 2_000_000),
        ("45,000", 45_000),  # comma as thousands, no unit
        ("12,5k", 12_500),
        ("0", 0),
        ("-3k", -3_000),
    ],
)
def test_parse_vnd_ok(text: str, expected: int) -> None:
    assert parse_vnd(text) == expected


@pytest.mark.parametrize(
    "text", ["", "   ", "abc", "đ", "k", "tr", "4 5 a", "45..000", "1.2.3", None]
)
def test_parse_vnd_unreadable_returns_none(text: str | None) -> None:
    assert parse_vnd(text) is None


def test_parse_vnd_numeric_passthrough() -> None:
    assert parse_vnd(45000) == 45000
    assert parse_vnd(45000.4) == 45000
    assert parse_vnd(float("nan")) is None
    assert parse_vnd(True) is None


def test_parse_vnd_returns_int() -> None:
    assert type(parse_vnd("1,5tr")) is int


def test_format_vnd() -> None:
    assert format_vnd(9280) == "9.280đ"
    assert format_vnd(-4_500_000) == "-4.500.000đ"
    assert format_vnd(0) == "0đ"
