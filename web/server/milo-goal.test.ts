// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { interpretMiloGoal, ruleMiloGoal, type MiloGoalMessage } from "./milo-goal";

const user = (content: string): MiloGoalMessage => ({ role: "user", content });
const assistant = (content: string): MiloGoalMessage => ({ role: "assistant", content });
afterEach(() => vi.unstubAllGlobals());

describe("Milo conversation goals", () => {
  it("does not turn a stock question or assistant suggestion into a user goal", async () => {
    expect(await interpretMiloGoal([user("Mình muốn biết tồn kho hiện tại bao nhiêu?")], {})).toBeNull();
    expect(ruleMiloGoal([user("Chào Milo"), assistant("Bạn nên bán 100 sản phẩm với ngân sách 2 triệu")])).toBeNull();
  });
  it("retains target and loss constraints when a later message corrects the budget", () => {
    const result = ruleMiloGoal([
      user("Mình muốn bán 100 sản phẩm, ngân sách 2 triệu, không lỗ"),
      assistant("Bạn có thể kiểm tra lịch sale."),
      user("Đổi ngân sách thành 3 triệu"),
    ]);
    expect(result?.draft).toMatchObject({ kind: "sell_target", targetUnits: 100, maxSpendVnd: 3000000, maxLossVnd: 0, start: null, deadline: null });
    expect(result?.draft.unresolved.join(" ")).not.toContain("nhiều mức ngân sách");
  });
  it("fills short answers using the preceding slot question and keeps earlier answers", () => {
    const result = ruleMiloGoal([
      user("Mình muốn bán đủ số lượng"), assistant("Bạn muốn bán bao nhiêu sản phẩm?"), user("100"),
      assistant("Tổng ngân sách quảng cáo cho cả buổi sale là bao nhiêu?"), user("2 triệu"),
      user("Số lượng thành 150 sản phẩm"),
    ]);
    expect(result?.draft).toMatchObject({ kind: "sell_target", targetUnits: 150, maxSpendVnd: 2000000 });
  });
  it("does not guess a slot when the assistant asked two different money questions", () => {
    const result = ruleMiloGoal([user("Bán 100 sản phẩm"), assistant("Ngân sách và giới hạn lỗ là bao nhiêu?"), user("2 triệu")]);
    expect(result?.draft).toMatchObject({ maxSpendVnd: null, maxLossVnd: null });
    expect(result?.draft.unresolved.join(" ")).toContain("Chưa rõ");
  });
  it("keeps goals when the user asks about current data instead of changing them", () => {
    const result = ruleMiloGoal([user("Bán 100 sản phẩm, ngân sách 2 triệu"), user("Ngân sách hiện tại bao nhiêu?")]);
    expect(result?.draft).toMatchObject({ targetUnits: 100, maxSpendVnd: 2000000 });
  });
  it("clears only an explicitly removed constraint", () => {
    const result = ruleMiloGoal([user("Bán 100 sản phẩm, ngân sách 2 triệu, không lỗ"), user("Bỏ giới hạn ngân sách")]);
    expect(result?.draft).toMatchObject({ targetUnits: 100, maxSpendVnd: null, maxLossVnd: 0 });
  });
  it("rejects daily budgets and ambiguous corrected money while preserving other slots", () => {
    const result = ruleMiloGoal([user("Bán 100 sản phẩm, ngân sách 2 triệu"), user("Đổi ngân sách thành 1.000 VND")]);
    expect(result?.draft).toMatchObject({ targetUnits: 100, maxSpendVnd: null });
    expect(ruleMiloGoal([user("Bán 100 sản phẩm, ngân sách 2 triệu mỗi ngày")])?.draft.maxSpendVnd).toBeNull();
  });
  it("does not guess relative dates and accepts explicit absolute replacements", () => {
    const result = ruleMiloGoal([user("Bán 100 sản phẩm tối nay"), user("Bắt đầu 2026-11-11T19:00:00+07:00, kết thúc 2026-11-11T23:00:00+07:00")]);
    expect(result?.draft).toMatchObject({ start: "2026-11-11T19:00:00+07:00", deadline: "2026-11-11T23:00:00+07:00" });
    expect(result?.draft.unresolved.join(" ")).not.toContain("Thời gian tương đối");
  });
  it("keeps earlier requirements beyond the 16-turn PM window", () => {
    const result = ruleMiloGoal([user("Bán 100 sản phẩm, ngân sách 2 triệu, không lỗ"), ...Array.from({ length: 30 }, () => user("Đã rõ, cảm ơn")), user("Đổi ngân sách thành 3 triệu")]);
    expect(result?.draft).toMatchObject({ targetUnits: 100, maxLossVnd: 0, maxSpendVnd: 3000000 });
  });
  it("retains constraints in a long conversation and explicitly marks shortened audit text", async () => {
    const result = await interpretMiloGoal([user("Bán 100 sản phẩm, ngân sách 2 triệu"), ...Array.from({ length: 40 }, () => user("Cảm ơn Milo. ".repeat(10))), user("Không lỗ")], {});
    expect(result?.draft).toMatchObject({ targetUnits: 100, maxSpendVnd: 2000000, maxLossVnd: 0 });
    expect(result!.draft.text.length).toBeLessThanOrEqual(4000);
    expect(result?.draft.unresolved.join(" ")).toContain("Hội thoại dài");
  });
  it("uses structured AI extraction and retains the exact user audit", async () => {
    const turns = [user("Mình muốn bán 100 sản phẩm"), assistant("Ngân sách của bạn là bao nhiêu?"), user("2 triệu")];
    const draft = ruleMiloGoal(turns)!.draft;
    const calls = vi.fn().mockResolvedValueOnce(Response.json({ data: [{ id: "gpt-6", created: 1 }] })).mockResolvedValueOnce(Response.json({ status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ draft }) }] }] }));
    vi.stubGlobal("fetch", calls);
    const result = await interpretMiloGoal(turns, { OPENAI_API_KEY: "test" });
    expect(result?.method).toBe("ai");
    expect(result?.draft).toMatchObject({ targetUnits: 100, maxSpendVnd: 2000000 });
    const payload = JSON.parse(calls.mock.calls[1]![1].body);
    expect(payload).toMatchObject({ store: false, text: { format: { type: "json_schema", strict: true } } });
    expect(payload.tools).toBeUndefined();
    expect(JSON.parse(payload.input[0].content).messages).toEqual(turns);
  });
  it("falls back on invalid AI output without losing the user's correction", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ data: [{ id: "gpt-6", created: 1 }] })).mockResolvedValueOnce(Response.json({ status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: "{}" }] }] })));
    const result = await interpretMiloGoal([user("Bán 100 sản phẩm, ngân sách 2 triệu"), user("Đổi ngân sách thành 3 triệu")], { OPENAI_API_KEY: "test" });
    expect(result?.method).toBe("rules");
    expect(result?.draft.maxSpendVnd).toBe(3000000);
  });
  it("lets AI discover a natural business need beyond heuristic keywords for a selected SKU", async () => {
    const text = "Đợt này hàng còn nhiều quá, mình muốn đẩy bớt cho nhẹ kho";
    expect(ruleMiloGoal([user(text)])).toBeNull();
    const draft = { ...ruleMiloGoal([user("Tôi muốn xả hàng")])!.draft, text, unresolved: [] };
    const calls = vi.fn().mockResolvedValueOnce(Response.json({ data: [{ id: "gpt-6", created: 1 }] })).mockResolvedValueOnce(Response.json({ status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ draft }) }] }] }));
    vi.stubGlobal("fetch", calls);
    const result = await interpretMiloGoal([user(text)], { OPENAI_API_KEY: "test" }, undefined, { discoverGoal: true });
    expect(result?.method).toBe("ai");
    expect(result?.draft).toMatchObject({ kind: "clear_stock", targetUnits: null, maxSpendVnd: null });
    expect(calls).toHaveBeenCalledTimes(2);
  });
  it("allows a factual SKU question to have no sale goal after AI discovery", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ data: [{ id: "gpt-6", created: 1 }] })).mockResolvedValueOnce(Response.json({ status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ draft: null }) }] }] })));
    expect(await interpretMiloGoal([user("Sản phẩm này còn bao nhiêu hàng?")], { OPENAI_API_KEY: "test" }, undefined, { discoverGoal: true })).toBeNull();
  });
  it("keeps unsupported requirements even when a valid AI draft omits its warnings", async () => {
    const turns = [user("Bán 100 sản phẩm, doanh thu 50 triệu, giảm giá 10%, tất cả chiến dịch")];
    const draft = { ...ruleMiloGoal(turns)!.draft, unresolved: [] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ data: [{ id: "gpt-6", created: 1 }] })).mockResolvedValueOnce(Response.json({ status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ draft }) }] }] })));
    const result = await interpretMiloGoal(turns, { OPENAI_API_KEY: "test" });
    const warnings = result!.draft.unresolved.join(" ");
    expect(result?.method).toBe("ai");
    for (const term of ["doanh thu", "giảm giá", "nhiều chiến dịch"]) expect(warnings).toContain(term);
  });
  it("retires only a condition explicitly removed in a later turn", async () => {
    const turns = [user("Bán 100 sản phẩm, doanh thu 50 triệu, giảm giá 10%"), user("Bỏ mục tiêu doanh thu, vẫn giữ giảm giá 10%")];
    const fallback = ruleMiloGoal(turns)!;
    expect(fallback.draft.unresolved.join(" ")).not.toContain("Mục tiêu doanh thu");
    expect(fallback.draft.unresolved.join(" ")).toContain("giảm giá");
    const draft = { ...fallback.draft, unresolved: [] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ data: [{ id: "gpt-6", created: 1 }] })).mockResolvedValueOnce(Response.json({ status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ draft }) }] }] })));
    const result = await interpretMiloGoal(turns, { OPENAI_API_KEY: "test" });
    expect(result?.draft.unresolved.join(" ")).not.toContain("Mục tiêu doanh thu");
    expect(result?.draft.unresolved.join(" ")).toContain("giảm giá");
  });
  it("does not silently omit probability and return constraints in fallback", () => {
    const turns = [user("Bán 100 sản phẩm, xác suất 90%, marginal return 1.5")];
    const result = ruleMiloGoal(turns)!;
    expect(result.draft).toMatchObject({ targetUnits: 100, sellTargetProbability: null, minMarginalReturn: null });
    expect(result.draft.unresolved.join(" ")).toContain("yêu cầu xác suất");
    expect(result.draft.unresolved.join(" ")).toContain("hiệu quả biên");
    const removed = ruleMiloGoal([...turns, user("Không cần xác suất; bỏ marginal return")])!;
    expect(removed.draft.unresolved.join(" ")).not.toMatch(/xác suất|hiệu quả biên/);
  });
  it("accepts AI-filled probability and return fields without a false fallback warning", async () => {
    const turns = [user("Bán 100 sản phẩm, xác suất 90%, marginal return 1.5")];
    const draft = { ...ruleMiloGoal(turns)!.draft, sellTargetProbability: 0.9, minMarginalReturn: 1.5, unresolved: [] };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(Response.json({ data: [{ id: "gpt-6", created: 1 }] })).mockResolvedValueOnce(Response.json({ status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ draft }) }] }] })));
    const result = await interpretMiloGoal(turns, { OPENAI_API_KEY: "test" });
    expect(result?.draft).toMatchObject({ sellTargetProbability: 0.9, minMarginalReturn: 1.5 });
    expect(result?.draft.unresolved.join(" ")).not.toMatch(/xác suất|hiệu quả biên/);
  });
});
