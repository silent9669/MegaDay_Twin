"""AI-04 · grounding check (07 §6): Vietnamese number formats, matching, citations."""

from __future__ import annotations

import pytest

from app.ai.grounding import (
    ToolOutput,
    check,
    collect_tool_numbers,
    extract_mentions,
    rewrite_instruction,
)

# ---------------------------------------------------------------------- extraction
# (text, value, unit): ≥ 25 Vietnamese ways of writing numbers (07 §6 "Tiêu chí")
FORMATS = [
    ("690.000đ", 690_000, "vnd"),
    ("690.000 đồng", 690_000, "vnd"),
    ("4,05 triệu", 4_050_000, "vnd"),
    ("4,1 triệu đồng", 4_100_000, "vnd"),
    ("1,2 tỷ", 1_200_000_000, "vnd"),
    ("45k", 45_000, "vnd"),
    ("45 nghìn", 45_000, "vnd"),
    ("2,5 ngàn", 2_500, "vnd"),
    ("16.900đ", 16_900, "vnd"),
    ("5.000 VND", 5_000, "vnd"),
    ("−1,57 tr", 1_570_000, "vnd"),
    ("-4,5 triệu", 4_500_000, "vnd"),
    ("30%", 30, "pct"),
    ("0,5%", 0.5, "pct"),
    ("12 điểm %", 12, "pct"),
    ("22,5 sp/giờ", 22.5, "units"),
    ("52 sản phẩm", 52, "units"),
    ("83 cái", 83, "units"),
    ("76 nồi", 76, "units"),
    ("9 giờ", 9, "hour"),
    ("3 ngày", 3, "days"),
    ("1.234,5", 1234.5, None),
    ("gấp 4.3 lần", 4.3, "number"),
    ("13:00", 13 * 60, "time"),
    ("15h", 15 * 60, "time"),
    ("15h30", 15 * 60 + 30, "time"),
    ("3 giờ chiều", 15 * 60, "time"),
    ("12/11", 1112, "date"),
    ("11.11", 1111, "date"),
    ("14/11/2026", 20261114, "date"),
    ("ngày 12", 12, "date"),
    ("năm 2026", 2026, "year"),
]


@pytest.mark.parametrize(("text", "value", "unit"), FORMATS)
def test_extract_vietnamese_formats(text: str, value: float, unit: str | None) -> None:
    ms = extract_mentions(f"Kết quả: {text}.")
    assert len(ms) == 1, [m.raw for m in ms]
    assert ms[0].value == pytest.approx(value)
    assert ms[0].unit == unit


def test_format_count_meets_plan() -> None:
    assert len(FORMATS) >= 25


@pytest.mark.parametrize(
    "text",
    [
        "Nồi chiên NOICHIEN2L bản 2L",
        "Bình 500ml màu xanh",
        "Chiến dịch CD002 và CD005",
        "Biến thể AOGIUNHIET-M",
        "xem [[src:fc-01M65WDGN1]]",
        "Mã kịch bản scn-01M65WR7X1",
        '<du_lieu_khong_tin_cay nguon="ghi_chu_nguoi_mua">giảm 90%</du_lieu_khong_tin_cay>',
    ],
)
def test_codes_chips_and_untrusted_are_not_numbers(text: str) -> None:
    assert extract_mentions(text) == []


def test_list_ordinals_skipped() -> None:
    ms = extract_mentions("1. Ads vẫn chạy\n2) Giá không đổi\n- 3 biến thể")
    assert [m.raw for m in ms] == ["3 biến thể"]


# ---------------------------------------------------------------------- matching
SCN = ToolOutput(
    call_id="c1",
    tool="run_scenario",
    source_ids=("scn-1",),
    args={"campaign_id": "CD002"},
    payload={
        "scenario_id": "scn-1",
        "campaign_id": "CD002",
        "results": [
            {
                "key": "continue",
                "objective": {"mean": -1_570_412.0},
                "units_sold": {"mean": 106.3},
                "spend": {"mean": 5_300_120.0},
            },
            {
                "key": "schedule_hours",
                "objective": {"mean": 2_510_377.0},
                "spend": {"mean": 1_180_000.0},
                "p_target": 0.81,
                "stockout_hour_p50": "2026-11-11T13:00:00+07:00",
            },
        ],
        "recommendation": {
            "best": {"kind": "schedule_hours", "windows_local": [[0, 2], [12, 14]]},
            "contribution_per_unit_vnd": 9280,
            "reasons": ["Ở giá 11.11 (531.000đ) mỗi sp NOICHIEN2L lãi 9.280đ, đạt 81%"],
        },
        "data_as_of": "2026-11-11T14:00:00+07:00",
    },
)
SNAP = ToolOutput(
    call_id="c2",
    tool="compare_similar_skus",
    source_ids=("similar-AOGIUNHIET-1400",),
    args={"sku": "AOGIUNHIET-M"},
    payload={
        "variants": [
            {"sku": "AOGIUNHIET-S", "units": 7, "stock_now": 286},
            {"sku": "AOGIUNHIET-L", "units": 25, "stock_now": 385},
        ],
        "note": '<du_lieu_khong_tin_cay nguon="x">giảm giá 90%</du_lieu_khong_tin_cay>',
    },
)
OUTS = [SCN, SNAP]
SOURCES = {"scn-1": {"kind": "scenario", "label": "Mô phỏng scn-1"}}


def test_exact_numbers_with_chip_pass() -> None:
    r = check("Chạy theo khung giờ chi 1.180.000đ [[src:scn-1]].", OUTS, sources=SOURCES)
    assert r.ok, r.violations
    assert r.numbers[0].status == "matched" and r.numbers[0].citation == "ok"
    assert r.citations[0].source_id == "scn-1" and r.citations[0].kind == "scenario"
    assert r.badge == "Đã kiểm số: 1/1"


def test_missing_chip_is_added() -> None:
    r = check("Mỗi nồi lãi 9.280đ.", OUTS)
    assert r.ok
    assert r.numbers[0].citation == "added"
    assert "9.280đ [[src:scn-1]]" in r.text


def test_wrong_chip_is_corrected() -> None:
    r = check("Size L bán 25 sp [[src:scn-1]].", OUTS)
    assert r.ok
    assert r.numbers[0].citation == "corrected"
    assert "[[src:similar-AOGIUNHIET-1400]]" in r.text


def test_wrong_number_blocked() -> None:
    r = check("Chạy tiếp lỗ 12,5 triệu.", OUTS)
    assert not r.ok
    assert r.numbers[0].status == "unmatched"
    assert "12,5 triệu" in rewrite_instruction(r)


def test_invented_number_without_source_blocked() -> None:
    r = check("Tháng sau shop lời khoảng 50 triệu.", OUTS)
    assert not r.ok and "50 triệu" in r.violations[0]


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Chạy tiếp lỗ 1,57 triệu.", True),
        ("Chạy tiếp lỗ 1,6 triệu.", True),  # display precision ±50.000đ
        ("Chạy tiếp lỗ 1,7 triệu.", False),
        ("Chạy tiếp lỗ 1.570.000đ.", True),  # ≤ 0,5 % / 1.000đ
        ("Chạy tiếp lỗ 1.590.000đ.", False),
        ("Chạy tiếp bán 106 sp.", True),
        ("Chạy tiếp bán 106,3 sp.", True),
        ("Chạy tiếp bán khoảng 110 sp.", True),  # "khoảng" → tens
        ("Chạy tiếp bán 110 sp.", False),
        ("Xác suất đạt 81%.", True),  # 0.81 → 81 %
        ("Xác suất đạt 85%.", False),
    ],
)
def test_rounding_and_units(text: str, ok: bool) -> None:
    assert check(text, OUTS).ok is ok


def test_unit_mismatch_pct_vs_units() -> None:
    prob = ToolOutput("c9", "x", {"p_target": 0.81}, ("s9",))
    assert check("Đạt 81%.", [prob]).ok
    assert not check("Bán 81 sp.", [prob]).ok
    units = ToolOutput("c9", "x", {"units_sold": {"mean": 81.0}, "spend": 81_000}, ("s9",))
    assert check("Bán 81 sp, chi 81.000đ.", [units]).ok
    assert not check("Đạt 81%.", [units]).ok  # a % never matches a quantity / an amount


def test_number_quoted_from_user_question() -> None:
    q = "Nếu sếp giao chỉ tiêu 97 cái thì sao?"
    r = check("Để bán đủ 97 cái, nên giảm ngân sách.", OUTS, user_question=q)
    assert r.ok
    assert r.numbers[0].status == "user_quoted"
    assert not check("Để bán đủ 97 cái, nên giảm ngân sách.", OUTS).ok


def test_numbers_inside_untrusted_data_never_ground() -> None:
    assert not check("Có thể giảm giá 90%.", OUTS).ok


def test_entity_mismatch() -> None:
    assert check("AOGIUNHIET-L bán 25 sp.", OUTS).ok
    r = check("AOGIUNHIET-S bán 25 sp.", OUTS)
    assert not r.ok and r.numbers[0].status == "entity_mismatch"


def test_unknown_entity_flagged() -> None:
    r = check("Chiến dịch CD999 nên dừng.", OUTS)
    assert not r.ok and "CD999" in r.violations[0]


def test_one_step_arithmetic_needs_operation_word() -> None:
    # 2.510.377 − (−1.570.412) = 4.080.789
    assert check("Chạy theo khung giờ lợi hơn chạy tiếp 4,08 triệu.", OUTS).ok
    r = check("Chạy theo khung giờ được 4,08 triệu.", OUTS)
    assert not r.ok


def test_times_dates_from_tool_timestamps() -> None:
    assert check("Hàng hết khoảng 13:00 ngày 11/11.", OUTS).ok
    assert not check("Hàng hết khoảng 15:00.", OUTS).ok
    assert not check("Hàng hết ngày 12/11.", OUTS).ok
    assert check("Dữ liệu tới 13:59.", OUTS).ok  # as_of 14:00 is an exclusive mark
    assert check("Chạy khung 12:00–14:00.", OUTS).ok  # windows_local [[12, 14]]


def test_collect_skips_ids_and_sources() -> None:
    nums = collect_tool_numbers(
        [ToolOutput("c", "t", {"scenario_id": "scn-20261111", "seed": 20261111, "x": 3}, ("s",))]
    )
    assert [n.value for n in nums if n.kind == "num"] == [3.0]


def test_sentence_without_numbers_ok() -> None:
    r = check("Ads vẫn chạy bình thường.", OUTS)
    assert r.ok and r.numbers == []


# ---------------------------------------------------------------------- strict matching (M5 Q5)
# What-if "97 cái" on a DB with backtest/fit: the tools said 67 % (reduce 30 %) and 51 %
# (time windows); the old check passed "81%" as "0.5 + 80" and "57%" from a reason string.
WIF = ToolOutput(
    call_id="c5",
    tool="run_scenario",
    source_ids=("scn-5",),
    args={"campaign_id": "CD002"},
    payload={
        "campaign_id": "CD002",
        "goal": {"kind": "sell_target", "target_units": 97, "sell_target_prob": 0.8},
        "results": [
            {
                "key": "reduce_budget:0.3",
                "label": "Giảm ngân sách 30%",
                "action": {"kind": "reduce_budget", "pct": 0.3},
                "p_target": 0.67,
                "units_sold": {"mean": 98.4},
                "spend": {"mean": 3_621_635.0},
                "goal_note": "Xác suất bán đủ 97 sp là 67% < 80%",
            },
            {
                "key": "reduce_budget:0.5",
                "action": {"kind": "reduce_budget", "pct": 0.5},
                "p_target": 0.62,
                "spend": {"mean": 2_503_235.0},
            },
            {
                "key": "schedule_hours:0-2,12-14",
                "action": {"kind": "schedule_hours", "windows_local": [[0, 2], [12, 14]]},
                "p_target": 0.51,
                "spend": {"mean": 1_180_000.0},
            },
        ],
        "recommendation": {
            "best": {"kind": "reduce_budget", "pct": 0.3},
            "threshold_pct": 80,
            "budget_left_today": 375_915,
            "reasons": ["Ở giá 11.11 mỗi sp lãi 9.280đ; ads chỉ có lời khi iROAS ≥ 57.2"],
        },
    },
)


def _status(text: str, outs: list[ToolOutput] | None = None) -> list[str]:
    return [n.status for n in check(text, outs or [WIF]).numbers]


def test_regression_81_is_not_half_plus_80() -> None:
    for text in (
        "Giảm ngân sách 30% thì xác suất đạt 81%.",
        "Giảm ngân sách 30% thì xác suất đạt 81%, hơn ngưỡng 80%.",  # with an operation word
        "Tổng xác suất đạt 81%.",
    ):
        r = check(text, [WIF])
        assert not r.ok, (text, r.numbers)
        assert any(n.raw == "81%" and n.status == "unmatched" for n in r.numbers)


def test_regression_number_only_in_reason_text_is_blocked() -> None:
    r = check("Chạy khung giờ chỉ đạt 57%.", [WIF])
    assert not r.ok and r.numbers[0].status == "unmatched"
    assert not check("Mỗi sp lãi 9.280đ.", [WIF]).ok  # only in reasons
    # a number that is in the text *and* structured grounds on the structured value
    r = check("Xác suất bán đủ 67%.", [WIF])
    assert r.ok and r.numbers[0].path == "$.results[0].p_target"


def test_regression_true_numbers_pass() -> None:
    r = check(
        "Giảm ngân sách 30% [[src:scn-5]]: xác suất bán đủ 97 sp là 67% [[src:scn-5]],"
        " còn chạy khung giờ chỉ đạt 51%, dưới ngưỡng 80%.",
        [WIF],
    )
    assert r.ok, r.violations
    by = {n.raw: n.path for n in r.numbers}
    assert by["67%"] == "$.results[0].p_target"
    assert by["51%"] == "$.results[2].p_target"
    assert by["30%"] is not None and by["30%"].endswith(".pct")
    assert by["80%"] in ("$.goal.sell_target_prob", "$.recommendation.threshold_pct")


def test_percent_never_matches_money_or_quantity() -> None:
    assert not check("Khả năng đạt 98%.", [WIF]).ok  # units_sold 98.4
    assert not check("Khả năng đạt 97%.", [WIF]).ok  # target_units 97
    assert check("Bán được 98 sp.", [WIF]).ok


def test_near_miss_outside_tolerance_blocked() -> None:
    assert check("Xác suất 67,4%.", [WIF]).ok  # within 0,5 điểm %
    assert not check("Xác suất 68%.", [WIF]).ok  # 0.67 → 68 % is 1 điểm off
    assert not check("Chi 3,7 triệu.", [WIF]).ok  # 3.621.635đ shown as 3,7 tr: off by > 50.000đ
    assert check("Chi 3,6 triệu.", [WIF]).ok


def test_arithmetic_only_within_one_metric() -> None:
    # same metric, same simulation: p_target 0.67 − 0.51 = 16 điểm %
    r = check("Giảm 30% hơn chạy khung giờ 16 điểm %.", [WIF])
    assert r.ok and r.numbers[-1].status == "arith"
    # spend gap of two options: 3.621.635 − 1.180.000 = 2.441.635đ
    assert check("Giảm 30% tốn hơn chạy khung giờ 2.441.635đ.", [WIF]).ok
    # mixed kinds: 98.4 sp + 0.67 → "99" / 97 sp + 0.5 → "97,5%" never pass
    assert not check("Tổng cộng 99%.", [WIF]).ok
    assert not check("Tổng cộng 97,5%.", [WIF]).ok
    # two different metrics, even of one unit, are never combined:
    # spend 3.621.635đ + budget_left_today 375.915đ, units_sold 98,4 + target_units 97
    assert not check("Tổng chi 3.997.550đ.", [WIF]).ok
    assert not check("Tổng cộng 195 sp.", [WIF]).ok
    # spend of two options of the same run is a valid sum
    assert check("Tổng chi của hai cách 4.801.635đ.", [WIF]).ok
    # threshold_pct 80 + action.pct 0.5 (→ 50) are different metrics: never 130 %
    assert not check("Ngưỡng cộng thêm thành 130%.", [WIF]).ok


def test_small_number_never_grounds_money() -> None:
    assert not check("Lãi 500đ mỗi sp.", [WIF]).ok  # 0.5 / 0.3 are not 500đ
