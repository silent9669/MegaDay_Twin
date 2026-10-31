// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildMiloContext } from "./milo-context";
import { loadContext } from "./sku-assistant";
vi.mock("./sku-assistant", () => ({ loadContext: vi.fn() }));
const env = { DATA_BACKEND_URL: "https://backend.test/api/v1/", DATA_BACKEND_USER: "lead", DATA_BACKEND_ROLE: "lead" };
const dist = { mean: 100, p10: 50, p50: 100, p90: 150 };
const trace = {
  version: 1, input_hash: "hash-1", campaign_id: "CD1", focus_sku: "SKU1", quantity_scope: "sku", economic_scope: "campaign",
  start: "2026-11-11T08:00:00+07:00", horizon_hours: 4, samples: 1000, seed: 11, data_as_of: "2026-11-11T07:00:00+07:00",
  goal: { kind: "sell_target", target_units: 100, params: { max_spend_vnd: 5000000, max_loss_vnd: 0, private_extra: "omit" } },
  equations: [{ id: "J", formula: "contribution - spend + leftover_value - stockout_penalty", explanation: "Objective from simulation samples" }],
  actions: [{ key: "keep", metrics: { objective: dist, contribution: dist, spend: dist, leftover_value: dist,
    stockout_penalty: dist, units_sold: dist, focus_units_sold: dist },
    reconciliation: { sum_components_mean: 100, objective_mean: 100, residual_vnd: 0 }, private_extra: "omit" }],
  sources: { batches: [{ batch_id: "B1", source_type: "orders", file_name: "orders.csv", file_sha256: "sha-1", as_of: null }],
    forecasts: [{ forecast_id: "F1", sku_id: "SKU1", model_id: "ewma", data_as_of: null,
      model_selection: { model_id: "ewma", primary_label: "Seasonal", forecast_origin: "2026-11-11T08:00:00+07:00",
        reasons: ["The season is not identified; use the baseline model."] } }] }, limitations: ["Not a guarantee."],
};
function scenario(overrides = {}) { return { scenario_id: "SC1", input_hash: "hash-1", request: { campaign_id: "CD1" },
  calculation_trace: trace, blocked: false, ...overrides }; }
function backend(data: unknown) {
  const fetch = vi.fn(async (_url: URL | string, init?: RequestInit) => {
    expect(init?.method).toBe("GET");
    expect(init?.redirect).toBe("manual");
    return Response.json({ data });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.resetAllMocks(); });

describe("Milo authoritative business context", () => {
  it("keeps missing backend explicit and never substitutes demo data", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const result = await buildMiloContext({ sku: "SKU1" }, {});
    expect(result.skuContext).toBeNull();
    expect(result.notices.join(" ")).toContain("Chưa kết nối backend");
    expect(fetch).not.toHaveBeenCalled();
    expect(loadContext).not.toHaveBeenCalled();
  });
  it("preserves source-backed SKU facts while unrelated campaign reads fail", async () => {
    vi.mocked(loadContext).mockResolvedValue({ sku: { sku: "SKU1", name: "Bình", variant: "", stockAvailable: 51,
      soldTodayUnits: 7, priceEffectiveVnd: 200000, contributionPerUnitVnd: 50000, demandGroup: "normal" },
      campaigns: [], source: null, dataAsOf: "2026-11-11", canPlan: false, notices: [], opening: "hello" });
    backend({ campaign_id: "OTHER", name: "Other campaign", skus: ["SKU1"], daily_budget_vnd: 999999 });
    const result = await buildMiloContext({ sku: "SKU1", campaignId: "CD1" }, env);
    expect(result.skuContext?.sku.stockAvailable).toBe(51);
    expect(result.campaign).toBeNull();
    expect(JSON.stringify(result)).not.toContain("999999");
    expect(result.notices).toHaveLength(2);
  });
  it("does not expose campaign budgets for an unrelated selected SKU", async () => {
    vi.mocked(loadContext).mockRejectedValue(Error("offline"));
    backend({ campaign_id: "CD1", name: "Shared", skus: ["SKU2"], daily_budget_vnd: 999999 });
    const result = await buildMiloContext({ sku: "SKU1", campaignId: "CD1" }, env);
    expect(result.campaign).toBeNull();
    expect(result.skuContext).toBeNull();
    expect(result.notices).toHaveLength(3);
  });
  it("reads the sole verified campaign budget without selecting or running an action", async () => {
    vi.mocked(loadContext).mockResolvedValue({ sku: { sku: "SKU1", name: "Bình", variant: "", stockAvailable: 51,
      soldTodayUnits: 7, priceEffectiveVnd: 200000, contributionPerUnitVnd: 50000, demandGroup: "normal" },
      campaigns: [{ id: "CD1", name: "Sale", skus: ["SKU1"] }], source: null, dataAsOf: "2026-11-11", canPlan: true, notices: [], opening: "hello" });
    const fetch = backend({ campaign_id: "CD1", name: "Sale", skus: ["SKU1"], daily_budget_vnd: 2000000 });
    const result = await buildMiloContext({ sku: "SKU1" }, env);
    expect(result.campaign).toMatchObject({ campaign_id: "CD1", daily_budget_vnd: 2000000, context_basis: "sole-sku-membership-read-only" });
    expect(result.selection.campaignId).toBeUndefined();
    expect(fetch.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
  });
  it("passes immutable scenario numbers only after validating identity and scope", async () => {
    backend(scenario());
    const result = await buildMiloContext({ scenarioId: "SC1" }, env);
    expect(result.scenario?.trace.actions[0]?.metrics.objective).toEqual(dist);
    expect(result.scenario?.trace.input_hash).toBe("hash-1");
    expect(result.scenario?.trace.sources.forecasts[0]?.model_selection).toEqual(trace.sources.forecasts[0]?.model_selection);
    expect(result.scenario?.trace.goal.params).toEqual({ max_spend_vnd: 5000000, max_loss_vnd: 0 });
    expect(JSON.stringify(result)).not.toContain("private_extra");
  });
  it("resolves versioned active constraints without trusting unrelated goal metadata", async () => {
    const goal = { goal_id: "G1", kind: "profit", label: "Profit", deadline: "2026-11-11T20:00:00+07:00",
      target_units: null, scope: "campaign", scope_id: "CD1", active: true, version: 3,
      created_at: "2026-11-10T08:00:00+07:00", confirmed_by: "lead",
      params: { max_spend_vnd: 5000000, max_loss_vnd: 0, planner_start: "2026-11-11T08:00:00+07:00", private_note: "omit" } };
    const fetch = vi.fn(async (target: URL | string, init?: RequestInit) => {
      expect(init?.method).toBe("GET");
      return Response.json({ data: new URL(target).pathname.includes("/goals/") ? goal
        : { campaign_id: "CD1", name: "Sale", skus: ["SKU1"], goal } });
    });
    vi.stubGlobal("fetch", fetch);
    const result = await buildMiloContext({ campaignId: "CD1" }, env);
    expect(result.campaign?.goal).toMatchObject({ goal_id: "G1", version: 3, detail_verified: true,
      params: { max_spend_vnd: 5000000, max_loss_vnd: 0 } });
    expect(JSON.stringify(result.campaign)).not.toContain("private_note");
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it.each([{ active: false }, { scope_id: "OTHER" }, { goal_id: "OTHER" }, { params: { max_spend_vnd: "5000000" } }])(
    "does not describe changed or invalid goal constraints as the current plan", async (change) => {
      const goal = { goal_id: "G1", kind: "profit", label: "Profit", deadline: "2026-11-11T20:00:00+07:00",
        target_units: null, scope: "campaign", scope_id: "CD1", active: true, version: 1, params: { max_loss_vnd: 0 } };
      vi.stubGlobal("fetch", vi.fn(async (target: URL | string) => Response.json({
        data: new URL(target).pathname.includes("/goals/") ? { ...goal, ...change }
          : { campaign_id: "CD1", name: "Sale", skus: ["SKU1"], goal },
      })));
      const result = await buildMiloContext({ campaignId: "CD1" }, env);
      expect(result.campaign?.goal).toBeNull();
      expect(result.notices.join(" ")).toContain("Mục tiêu đang áp dụng chưa được xác minh");
    },
  );
  it("loads real daily history without inventing aggregate totals or dropping provisional flags", async () => {
    vi.mocked(loadContext).mockRejectedValue(Error("offline"));
    const history = { sku: "SKU1", grain: "day", start: "2026-11-04T08:00:00+07:00", end: "2026-11-11T08:00:00+07:00",
      points: [{ t: "2026-11-10T00:00:00+07:00", units: 20, units_provisional: 3, revenue_net_vnd: 120000,
        stock_end: null, stockout: true, ad_spend_vnd: null, ad_spend_estimated: true, flags: ["missing_stock"] }] };
    const fetch = backend(history);
    const result = await buildMiloContext({ sku: "SKU1" }, env);
    expect(result.salesHistory).toEqual(history);
    expect(String(fetch.mock.calls[0]?.[0])).toContain("/skus/SKU1/panel?grain=day");
  });
  it("shares a 12-second abort deadline across contextual backend requests", async () => {
    const controller = new AbortController();
    const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(controller.signal);
    vi.mocked(loadContext).mockRejectedValue(Error("offline"));
    vi.stubGlobal("fetch", vi.fn((_target: unknown, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal!.addEventListener("abort", () => reject(Error("deadline")), { once: true });
    })));
    const pending = buildMiloContext({ sku: "SKU1", campaignId: "CD1" }, env);
    controller.abort();
    const result = await pending;
    expect(timeout).toHaveBeenCalledWith(12000);
    expect(result.salesHistory).toBeNull();
    expect(result.campaign).toBeNull();
  });
  it.each([
    scenario({ scenario_id: "OTHER" }),
    scenario({ input_hash: "changed" }),
    scenario({ calculation_trace: null }),
    scenario({ blocked: true }),
    scenario({ request: { campaign_id: "OTHER" } }),
  ])("rejects stale, legacy, blocked and mismatched traces", async (data) => {
    backend(data);
    const result = await buildMiloContext({ scenarioId: "SC1" }, env);
    expect(result.scenario).toBeNull();
    expect(result.notices.join(" ")).toContain("dấu vết tính toán");
  });
  it("does not reinterpret another SKU's simulation as the selected product", async () => {
    backend(scenario());
    vi.mocked(loadContext).mockRejectedValue(Error("offline"));
    const result = await buildMiloContext({ scenarioId: "SC1", sku: "SKU2" }, env);
    expect(result.scenario).toBeNull();
  });
  it("bounds action/source evidence and marks truncation without changing numbers", async () => {
    backend(scenario({ calculation_trace: { ...trace, actions: Array.from({ length: 10 }, () => trace.actions[0]),
      sources: { ...trace.sources, batches: Array.from({ length: 15 }, () => trace.sources.batches[0]) } } }));
    const result = await buildMiloContext({ scenarioId: "SC1" }, env);
    expect(result.scenario?.trace.actions).toHaveLength(8);
    expect(result.scenario?.trace.sources.batches).toHaveLength(10);
    expect(result.scenario?.truncated).toBe(true);
  });
  it("retains bounded historical cost inputs, assumptions and parameter provenance", async () => {
    const sku = { sku_id: "SKU1", in_campaign: true, is_bundle: false, stock_start: 51, stock_unlimited: false,
      unit_cost_vnd: 70000, price_hourly: Array(48).fill(100000), contribution_per_unit_hourly: Array(48).fill(20000),
      forecast_mean_hourly: Array(48).fill(5), forecast_id: "F1", forecast_label: "normal", dispersion_k: 2, sigma: 0.3,
      leftover_value_per_unit: 40000, restock: [], economics_by_day: [{ sku_id: "SKU1", day: "2026-11-11",
        eff_price: 100000, price_source: "sale", unit_cost: 70000, packaging_cost: 5000, fee_rate: 0.05, m: 20000, is_bundle: false }] };
    const assumptions = { stockout_penalty_per_unit: 1000, rank_loss: 0.1, adstock_start: 1,
      budget_source: "campaign", daily_budget: [2000000], must_run_hours: [1, 2] };
    const iroas = { source: "estimate", estimate_id: "IR1", confidence: "medium", delta: 0.5, spend_scale: 1000000 };
    const parameters = [
      { key: "stockout_penalty", value: 1000, version: 2, scope: "global", scope_id: null, set_by: "lead" },
      { key: "reduce_budget_options", value: [0.5, 0.75], version: 0, scope: "default", scope_id: null, set_by: null },
    ];
    backend(scenario({ calculation_trace: { ...trace, skus: [sku], assumptions,
      sources: { ...trace.sources, parameters, iroas } } }));
    const result = await buildMiloContext({ scenarioId: "SC1" }, env);
    const evidence = result.scenario?.trace;
    expect(evidence?.skus?.[0]?.economics_by_day).toEqual(sku.economics_by_day);
    expect(evidence?.skus?.[0]?.price_hourly).toHaveLength(24);
    expect(evidence?.assumptions).toEqual(assumptions);
    expect(evidence?.sources.parameters).toEqual(parameters);
    expect(evidence?.sources.iroas).toEqual(iroas);
    expect(result.scenario?.truncated).toBe(true);
  });
  it("reads account sources and labels sandbox observations separately from analytics", async () => {
    const storage = { fetch: vi.fn(async (request: Request) => Response.json({ revision: 3, updatedAt: "2026-11-11T00:00:00Z",
      data: new URL(request.url).pathname.endsWith("sandbox") ? { hour: 12, event: "observe" } : {
        schema: "hackathon.sources.v1", files: [{ kind: "inventory", name: "inventory.csv", hash: "sha", origin: "upload",
          text: "Mã nội bộ,Tồn thực tế,Tên hàng,Ngày\nSKU1,51,Bình nước,2026-11-11\nSKU2,999,Other product,2026-11-11" }] },
    })) };
    const result = await buildMiloContext({ sku: "SKU1" }, {}, storage, "https://app.test");
    expect(result.workspace).toMatchObject({ sourceRevision: 3, sandboxHour: 12, observationOnly: true,
      selectedStock: { code: "SKU1", stock: 51, name: "Bình nước" } });
    expect(JSON.stringify(result.workspace)).not.toContain("Other product");
    expect(JSON.stringify(result.workspace)).not.toContain("Mã nội bộ");
  });
});
