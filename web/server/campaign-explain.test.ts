import { explainCampaign } from "./campaign-explain";
import { modelJSON } from "./model-json";
vi.mock("./model-json", () => ({ modelJSON: vi.fn() }));
const env = { DATA_BACKEND_URL: "https://backend.example/api/v1/", DATA_BACKEND_USER: "lead", DATA_BACKEND_ROLE: "lead",
  OPENAI_API_KEY: "test", OPENAI_MODEL_DECISION: "test-model" };
const scenario = { scenario_id: "scn", blocked: false, input_hash: "hash", request: { campaign_id: "CD002", goal_id: "g" },
  calculation_trace: { input_hash: "hash", goal: { kind: "profit" } },
  recommendation: { status: "recommend", best: { kind: "pause" }, reasons: ["Căn cứ A", "Căn cứ B"], flip_conditions: [{ text: "Điều kiện C" }] } };
const request = () => new Request("https://example.test/api/campaign-hq/explain", { method: "POST", body: JSON.stringify({ scenarioId: "scn" }) });
beforeEach(() => { vi.mocked(modelJSON).mockReset(); vi.stubGlobal("fetch", vi.fn(async () => Response.json({ data: scenario }))); });
afterEach(() => vi.unstubAllGlobals());
it("only returns simulator-owned evidence selected by AI", async () => {
  vi.mocked(modelJSON).mockResolvedValue({ reasonIndices: [1], cautionIndices: [0] });
  expect(await (await explainCampaign(request(), env)).json()).toEqual({ scenarioId: "scn", method: "ai", reasons: ["Căn cứ B"], cautions: ["Điều kiện C"] });
});
it("falls back honestly when AI invents an evidence reference", async () => {
  vi.mocked(modelJSON).mockResolvedValue({ reasonIndices: [900], cautionIndices: [] });
  const body = await (await explainCampaign(request(), env)).json();
  expect(body.method).toBe("rules"); expect(body.reasons).toEqual(scenario.recommendation.reasons);
});
it("does not call AI for a mismatched stored scenario", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ data: { ...scenario, scenario_id: "other" } })));
  expect((await explainCampaign(request(), env)).status).toBe(409);
  expect(modelJSON).not.toHaveBeenCalled();
});
