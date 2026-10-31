// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { goalDraftSchema } from "../src/planner/goal-contract";
import { interpretGoal, ruleGoalDraft } from "./goal-interpret";

afterEach(() => vi.unstubAllGlobals());
const request = (body: unknown) => new Request("https://example.test/api/planner/interpret", {
  method: "POST", body: JSON.stringify(body),
});

describe("conservative sale goal interpretation", () => {
  it("recognizes all four UI templates without inventing unspecified values", () => {
    expect(ruleGoalDraft("Tôi muốn tối ưu lợi nhuận của chiến dịch trong buổi sale.")).toMatchObject({
      kind: "profit", targetUnits: null, maxSpendVnd: null, maxLossVnd: null,
    });
    expect(ruleGoalDraft("Tôi muốn bán ít nhất 100 sản phẩm, ngân sách quảng cáo tối đa 2 triệu đồng.")).toMatchObject({
      kind: "sell_target", targetUnits: 100, maxSpendVnd: 2000000, sellTargetProbability: null,
    });
    expect(ruleGoalDraft("Tôi muốn xả hàng tồn, chấp nhận lỗ kỳ vọng tối đa 500 nghìn đồng.")).toMatchObject({
      kind: "clear_stock", maxLossVnd: 500000, maxSpendVnd: null,
    });
    expect(ruleGoalDraft("Tôi muốn tối ưu hiệu quả chi quảng cáo của chiến dịch.")).toMatchObject({
      kind: "efficiency", minMarginalReturn: null, start: null, deadline: null,
    });
  });
  it("extracts explicit product count and money without making up relative dates", () => {
    const draft = ruleGoalDraft("Tối nay bán 300 sản phẩm, ngân sách 5 triệu, không lỗ quá 1,5 triệu, từ 19h–23h.");
    expect(draft).toMatchObject({ kind: "sell_target", targetUnits: 300, maxSpendVnd: 5000000, maxLossVnd: 1500000, start: null, deadline: null, sellTargetProbability: null });
    expect(draft.unresolved.join(" ")).toContain("Thời gian tương đối");
    expect(goalDraftSchema.safeParse(draft).success).toBe(true);
  });
  it("does not turn an hour, order count, ambiguous currency, or daily budget into a product target or total budget", () => {
    expect(ruleGoalDraft("Bán 19h đến 23h với ngân sách 1.000 VND")).toMatchObject({ targetUnits: null, maxSpendVnd: null });
    expect(ruleGoalDraft("Bán 300 đơn")).toMatchObject({ targetUnits: null });
    expect(ruleGoalDraft("Sell 300 units with budget 5 million per day")).toMatchObject({ targetUnits: 300, maxSpendVnd: null });
  });
  it("keeps competing and unsupported requirements visible", () => {
    const draft = ruleGoalDraft("Tối đa lợi nhuận và bán 300 sản phẩm, doanh thu 50 triệu, giảm giá 10%, đảm bảo không lỗ, toàn shop, SKU A.");
    expect(draft).toMatchObject({ kind: "profit", targetUnits: 300, maxLossVnd: 0 });
    for (const term of ["nhiều mục tiêu", "doanh thu", "giảm giá", "bảo đảm", "nhiều chiến dịch", "sản phẩm"])
      expect(draft.unresolved.join(" ")).toContain(term);
    expect(ruleGoalDraft("Tối đa lợi nhuận không lỗ, lỗ tối đa 1 triệu").unresolved.join(" ")).toContain("đang khác nhau");
  });
  it("leaves unknown wording and missing values for human review", async () => {
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    const response = await interpretGoal(request({ text: "Giúp tôi có buổi sale tốt" }), {});
    expect(response.status).toBe(200);
    const result = await response.json();
    expect(result.method).toBe("rules");
    expect(result.draft).toMatchObject({ kind: null, targetUnits: null, maxSpendVnd: null });
    expect(result.draft.unresolved.length).toBeGreaterThan(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("rejects malformed, empty, oversized and unexpected request fields before external calls", async () => {
    for (const body of [{ text: " " }, { text: "a".repeat(4001) }, { text: "sale", goal_id: "auto-save" }, { text: "sale", referenceTime: "tomorrow" }])
      expect((await interpretGoal(request(body), {})).status).toBe(422);
    expect((await interpretGoal(new Request("https://example.test", { method: "POST", body: "{" }), {})).status).toBe(400);
  });
});

describe("AI sale goal boundary", () => {
  function mockAI(payload: unknown, status = "completed") {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ data: [{ id: "gpt-4.1", created: 1 }] }))
      .mockResolvedValueOnce(Response.json({ status, output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify(payload) }] }] }));
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }
  it("uses strict structured output with no tools and server reference time; keeps unsupported warnings", async () => {
    const text = "Maximize profit, revenue 500 million";
    const draft = { ...ruleGoalDraft(text), unresolved: [] };
    const fetchMock = mockAI(draft);
    const response = await interpretGoal(request({ text, referenceTime: "2000-01-01T00:00:00Z" }), { OPENAI_API_KEY: "secret" }, "2026-10-31T08:00:00Z");
    const result = await response.json();
    expect(result.method).toBe("ai");
    expect(result.draft.unresolved.join(" ")).toContain("doanh thu");
    const body = JSON.parse(fetchMock.mock.calls[1]![1].body);
    expect(body).toMatchObject({ store: false, text: { format: { strict: true, type: "json_schema" } } });
    expect(body.tools).toBeUndefined();
    expect(JSON.parse(body.input[0].content).referenceTime).toBe("2026-10-31T08:00:00Z");
  });
  it.each(["missing", "extra", "infinite", "changed-text", "zero-probability"])("falls back safely on invalid AI output: %s", async (variant) => {
    const draft: Record<string, unknown> = { ...ruleGoalDraft("Sell 300 units") };
    if (variant === "missing") delete draft.maxSpendVnd;
    if (variant === "extra") draft.command = "save";
    if (variant === "infinite") draft.maxSpendVnd = "Infinity";
    if (variant === "changed-text") draft.text = "Changed";
    if (variant === "zero-probability") draft.sellTargetProbability = 0;
    mockAI(draft);
    const response = await interpretGoal(request({ text: "Sell 300 units" }), { OPENAI_API_KEY: "secret" });
    expect((await response.json()).method).toBe("rules");
  });
  it("does not accept model guesses for relative dates or per-day budgets", async () => {
    const text = "Sell 300 units tomorrow, budget 5 million per day";
    mockAI({ ...ruleGoalDraft(text), start: "2026-11-01T10:00:00+07:00", deadline: "2026-11-01T20:00:00+07:00", maxSpendVnd: 5000000, unresolved: [] });
    const result = await (await interpretGoal(request({ text }), { OPENAI_API_KEY: "secret" })).json();
    expect(result.method).toBe("ai");
    expect(result.draft).toMatchObject({ start: null, deadline: null, maxSpendVnd: null });
    expect(result.draft.unresolved.join(" ")).toContain("giờ/ngày");
  });
  it("falls back on incomplete response and connection failures without exposing credentials or internal errors", async () => {
    mockAI(ruleGoalDraft("Sell 300 units"), "incomplete");
    expect((await (await interpretGoal(request({ text: "Sell 300 units" }), { OPENAI_API_KEY: "secret" })).json()).method).toBe("rules");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("secret upstream diagnostics")));
    const result = await (await interpretGoal(request({ text: "Sell 300 units" }), { OPENAI_API_KEY: "secret" })).json();
    expect(result.method).toBe("rules");
    expect(JSON.stringify(result)).not.toContain("secret");
  });
});
