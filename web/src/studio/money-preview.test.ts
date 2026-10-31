import { describe, expect, it } from "vitest";
import { previewVnd } from "./money-preview";

describe("display-only VND previews", () => {
  // Contract examples from backend/tests/unit/test_money.py, plus exact rounding.
  it.each([
    ["45000", 45000], ["45.000", 45000], ["45k", 45000], ["45.000đ", 45000],
    ["455.000đ", 455000], ["3k", 3000], ["1,5tr", 1500000], ["1,2tr", 1200000],
    ["45 000 đ", 45000], ["45K", 45000], ["45.000 VNĐ", 45000],
    ["45.000 đồng", 45000], ["₫45.000", 45000], ["45.000₫", 45000],
    ["1.234.567", 1234567], ["1.5tr", 1500000], ["45,000", 45000],
    ["12,5k", 12500], ["0", 0], ["-3k", -3000], ["-1,5", -2],
    ["1,2345k", 1235], ["1.005tr", 1005000000],
  ] as const)("previews %s as %i VND", (value, expected) => {
    expect(previewVnd(value)).toBe(expected);
  });
  it.each(["", "   ", "abc", "đ", "k", "tr", "4 5 a", "45..000", "1.2.3", "9007199254740992", "1e6", "USD45"])(
    "keeps %s pending review rather than guessing", (value) => expect(previewVnd(value)).toBeNull(),
  );
});
