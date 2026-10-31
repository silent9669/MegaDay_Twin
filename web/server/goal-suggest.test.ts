import { suggestGoal } from "./goal-suggest";
import { modelJSON } from "./model-json";
import type * as ModelModule from "./model-json";
import type { GoalDraft } from "../src/planner/goal-contract";
vi.mock("./model-json", async importOriginal => ({ ...await importOriginal<typeof ModelModule>(), modelJSON: vi.fn() }));
const env = { DATA_BACKEND_URL: "https://backend.example/api/v1/", DATA_BACKEND_USER: "lead", DATA_BACKEND_ROLE: "lead",
  OPENAI_API_KEY: "test", OPENAI_MODEL_DECISION: "decision-test" };
const draft: GoalDraft = { text: "Sell this stock", kind: null, targetUnits: null, start: null, deadline: null,
  maxSpendVnd: null, maxLossVnd: null, sellTargetProbability: null, minMarginalReturn: null, unresolved: [] };
const complete: GoalDraft = { ...draft, kind: "sell_target", targetUnits: 20, start: "2026-11-11T01:00:00+07:00", deadline: "2026-11-11T13:00:00+07:00", maxSpendVnd: 500000 };
const evidence = { campaign: { campaign_id: "CD002", name: "Sale", skus: ["NOICHIEN2L"], daily_budget_vnd: 1000000, spend_today_vnd: 50000,
  sku_detail: [{ sku: "NOICHIEN2L", name: "Fryer", stock_available: 86, price_effective_vnd: 600000, unit_cost_vnd: 400000 }] }, dataAsOf: "2026-11-11T01:00:00+07:00", goal: null };
const request = (patch = {}, focusSku: string | undefined = "NOICHIEN2L") => new Request("https://app.test/api/sale-planner/suggest", {
  method: "POST", body: JSON.stringify({ campaignId: "CD002", focusSku, draft: { ...draft, ...patch } }),
});
beforeEach(() => {
  vi.mocked(modelJSON).mockReset().mockResolvedValue({ draft: complete, assumptions: ["Proposed 12 hours from the data clock; total budget is half the daily budget."] });
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(evidence)));
});
afterEach(() => vi.unstubAllGlobals());
it("uses the configured decision model with server-loaded campaign and focused inventory", async () => {
  const response = await suggestGoal(request(), env);
  expect(response.status).toBe(200);
  expect(await response.json()).toMatchObject({ method: "ai", draft: complete });
  expect(modelJSON).toHaveBeenCalledWith(env, "decision-test", expect.any(String), expect.objectContaining({ evidence, focusSku: "NOICHIEN2L" }), expect.any(Object), expect.any(AbortSignal));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(vi.mocked(fetch).mock.calls[0]![1]!.method).toBe("GET");
});
it("preserves existing user values including zero and exact source text", async () => {
  const response = await suggestGoal(request({ targetUnits: 7, maxSpendVnd: 0, maxLossVnd: 0 }), env);
  expect(await response.json()).toMatchObject({ draft: { targetUnits: 7, maxSpendVnd: 0, maxLossVnd: 0, text: draft.text } });
});
it("rejects missing required fields and invalid windows from the model", async () => {
  for (const patch of [{ targetUnits: null }, { deadline: "2026-11-15T13:00:00+07:00" }, { start: null }, { kind: "efficiency" }]) {
    vi.mocked(modelJSON).mockResolvedValue({ draft: { ...complete, ...patch }, assumptions: ["Proposal"] });
    expect((await suggestGoal(request(), env)).status).toBe(422);
  }
});
it("checks campaign membership before calling the model", async () => {
  expect((await suggestGoal(request({}, "UNRELATED"), env)).status).toBe(422);
  expect(modelJSON).not.toHaveBeenCalled();
});
it("reports model unavailability without substituting made-up suggestions", async () => {
  expect((await suggestGoal(request(), { ...env, OPENAI_MODEL_DECISION: undefined })).status).toBe(503);
  expect(modelJSON).not.toHaveBeenCalled();
  vi.mocked(modelJSON).mockRejectedValue(Error("No response"));
  expect((await suggestGoal(request(), env)).status).toBe(422);
});
it("does not accept client-supplied campaign evidence", async () => {
  const req = new Request("https://app.test/api/sale-planner/suggest", { method: "POST", body: JSON.stringify({ campaignId: "CD002", draft, evidence }) });
  expect((await suggestGoal(req, env)).status).toBe(422);
  expect(modelJSON).not.toHaveBeenCalled();
});
