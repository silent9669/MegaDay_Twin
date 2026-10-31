// @vitest-environment node
import { createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { plannerContext, runSalePlan, type PlannerEnv } from "./sale-planner";
import { hashText } from "../src/studio/sources";
import type { GoalDraft } from "../src/planner/goal-contract";
import worker from "../worker";

const env: PlannerEnv = { DATA_BACKEND_URL: "https://backend.test/api/v1", DATA_BACKEND_USER: "lead-1", DATA_BACKEND_ROLE: "lead" };
const draft: GoalDraft = { text: "Bán đủ 200 sản phẩm, chi tối đa 3 triệu, không lỗ kỳ vọng", kind: "sell_target", targetUnits: 200,
  start: "2026-11-11T19:00:00+07:00", deadline: "2026-11-11T23:00:00+07:00", maxSpendVnd: 3000000,
  maxLossVnd: 0, sellTargetProbability: 0.8, minMarginalReturn: null, unresolved: [] };
const payload = { campaignId: "CD002", draft, confirmed: true, acknowledgedUnresolved: false };
const request = (body: unknown = payload) => new Request("https://app.test/api/sale-planner/run", { method: "POST", body: JSON.stringify(body) });
afterEach(() => vi.unstubAllGlobals());
function mockBackend(options: { failSimulation?: boolean; oldBackend?: boolean; lead?: boolean; focusSupported?: boolean; traceOverride?: Record<string, unknown> | null; scenarioHash?: string | null } = {}) {
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  let saved: Record<string, unknown> | null = null;
  const fetch = vi.fn(async (url: URL | string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    if (body) writes.push({ path, body });
    if (path.endsWith("/goal-planner/capabilities")) return Response.json(options.oldBackend ? { data: { version: 0 } } : { data: { version: 1, constraints: ["max_spend_vnd", "max_loss_vnd", "sell_target_prob", "min_marginal_return", ...(options.focusSupported === false ? [] : ["focus_sku", "calculation_trace"])] } });
    if (path.endsWith("/me")) return Response.json({ data: { can_edit_goals: options.lead !== false, can_run_scenarios: true } });
    if (path.endsWith("/campaigns")) return Response.json({ data: [{ campaign_id: "CD002", name: "Sale campaign" }], meta: { data_as_of: "2026-11-11T18:00:00+07:00" }, next_cursor: null });
    if (path.endsWith("/campaigns/CD002")) return Response.json({ data: { skus: ["SKU1", "SKU2"] } });
    if (path.endsWith("/goals/goal-1")) return Response.json({ data: saved });
    if (path.endsWith("/goals")) {
      if (body) { saved = { ...body, goal_id: "goal-1", active: true }; return Response.json({ data: saved }); }
      return Response.json({ data: saved ? [saved] : [] });
    }
    if (path.endsWith("/scenarios/run")) return options.failSimulation
      ? Response.json({ error: { code: "BLOCKING_DATA_ISSUE", message: "Thiếu giá vốn", details: { reasons: ["SKU1 thiếu giá vốn"] } } }, { status: 422 })
      : Response.json({ data: { scenario_id: "scn-1", request: body, results: [], recommendation: null,
        input_hash: options.scenarioHash === undefined ? "input-hash-1" : options.scenarioHash,
        calculation_trace: options.traceOverride === undefined ? { version: 1, campaign_id: "CD002",
          focus_sku: (saved?.params as Record<string, unknown> | undefined)?.focus_sku ?? null, input_hash: "input-hash-1" } : options.traceOverride,
      }, meta: {} });
    throw Error(`Unexpected backend path: ${path}`);
  });
  vi.stubGlobal("fetch", fetch);
  return { fetch, writes, options, setSaved: (value: Record<string, unknown>) => { saved = value; } };
}
describe("confirmed sale planner gateway", () => {
  it("rejects missing or mismatched SKU traces while preserving the saved goal for retry", async () => {
    const valid = { version: 1, input_hash: "input-hash-1", campaign_id: "CD002", focus_sku: "SKU1" };
    for (const traceOverride of [null, { ...valid, version: 2 }, { ...valid, focus_sku: "SKU2" },
      { ...valid, campaign_id: "OTHER" }, { ...valid, input_hash: "stale-hash" }]) {
      const state = mockBackend({ traceOverride });
      const result = await runSalePlan(request({ ...payload, focusSku: "SKU1" }), env);
      expect(result.status).toBe(502);
      expect(await result.json()).toMatchObject({ goalId: "goal-1", stage: "simulation", error: { code: "CALCULATION_TRACE_MISMATCH" } });
      expect(state.writes).toHaveLength(2);
    }
    mockBackend({ scenarioHash: "" });
    expect((await runSalePlan(request({ ...payload, focusSku: "SKU1" }), env)).status).toBe(502);
    mockBackend({ traceOverride: null, scenarioHash: null });
    expect((await runSalePlan(request(), env)).status).toBe(200);
  });
  it("requires focus capabilities and actual membership before storing an SKU goal", async () => {
    const older = mockBackend({ focusSupported: false });
    expect((await runSalePlan(request({ ...payload, focusSku: "SKU1" }), env)).status).toBe(409);
    expect(older.writes).toHaveLength(0);
    const state = mockBackend();
    expect((await runSalePlan(request({ ...payload, focusSku: "OTHER" }), env)).status).toBe(422);
    expect(state.writes).toHaveLength(0);
    expect((await runSalePlan(request({ ...payload, focusSku: "SKU1" }), env)).status).toBe(200);
    expect(state.writes[0]!.body.params).toMatchObject({ focus_sku: "SKU1", max_spend_vnd: 3000000 });
    expect((await runSalePlan(request({ ...payload, focusSku: "SKU1", goalId: "goal-1" }), env)).status).toBe(200);
    expect(state.writes.filter((w) => w.path.endsWith("/goals"))).toHaveLength(1);
    expect((await runSalePlan(request({ ...payload, focusSku: "SKU2", goalId: "goal-1" }), env)).status).toBe(409);
  });
  it("never substitutes demo outcomes when backend is unavailable", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    expect(await (await plannerContext({})).json()).toMatchObject({ backendConfigured: false, canCreateGoals: false, campaigns: [] });
    expect((await runSalePlan(request(), {})).status).toBe(503);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("rejects unconfirmed, ambiguous, wrong-kind and non-hour windows before any backend action", async () => {
    const { fetch } = mockBackend();
    for (const body of [
      { ...payload, confirmed: false },
      { ...payload, draft: { ...draft, unresolved: ["Giảm giá chưa hỗ trợ"] } },
      { ...payload, draft: { ...draft, kind: "profit" } },
      { ...payload, draft: { ...draft, start: "2026-11-11T19:30:00+07:00" } },
      { ...payload, draft: { ...draft, deadline: "2026-11-15T23:00:00+07:00" } },
      { ...payload, draft: { ...draft, minMarginalReturn: 2 } },
    ]) expect((await runSalePlan(request(body), env)).status).toBe(422);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("requires upgraded backend and authorized lead before creating a goal", async () => {
    const older = mockBackend({ oldBackend: true });
    expect((await runSalePlan(request(), env)).status).toBe(409);
    expect(older.writes).toHaveLength(0);
    const operator = mockBackend({ lead: false });
    expect((await runSalePlan(request(), env)).status).toBe(403);
    expect(operator.writes).toHaveLength(0);
    expect(await (await plannerContext(env)).json()).toMatchObject({ backendConfigured: true, canCreateGoals: false });
  });
  it("sends every confirmed numeric constraint to the engine and returns only actual results", async () => {
    const { writes } = mockBackend();
    const result = await runSalePlan(request(), env);
    expect(result.status).toBe(200);
    expect(await result.json()).toMatchObject({ goalId: "goal-1", scenario: { data: { scenario_id: "scn-1" } } });
    expect(writes[0]!.body).toMatchObject({ scope: "campaign", scope_id: "CD002", kind: "sell_target", target_units: 200,
      params: { max_spend_vnd: 3000000, max_loss_vnd: 0, sell_target_prob: 0.8, planner_source_text: draft.text } });
    expect(writes[1]!.body).toMatchObject({ campaign_id: "CD002", goal_id: "goal-1", start: draft.start, horizon_hours: 4 });
  });
  it("retains the saved goal and missing-data explanation, then retries simulation without creating another goal", async () => {
    const state = mockBackend({ failSimulation: true });
    const failed = await runSalePlan(request(), env);
    expect(failed.status).toBe(422);
    expect(await failed.json()).toMatchObject({ goalId: "goal-1", stage: "simulation", error: { details: { reasons: ["SKU1 thiếu giá vốn"] } } });
    state.options.failSimulation = false;
    expect((await runSalePlan(request({ ...payload, goalId: "goal-1" }), env)).status).toBe(200);
    expect(state.writes.filter((w) => w.path.endsWith("/goals"))).toHaveLength(1);
  });
  it("recovers a matching active goal after a lost response and refuses reuse for changed constraints", async () => {
    const state = mockBackend();
    state.setSaved({ goal_id: "goal-1", scope: "campaign", scope_id: "CD002", active: true,
      kind: draft.kind, target_units: draft.targetUnits, deadline: draft.deadline,
      params: { planner_request_hash: await hashText(JSON.stringify({ campaignId: "CD002", draft })),
        max_spend_vnd: 3000000, max_loss_vnd: 0, sell_target_prob: 0.8 } });
    expect((await runSalePlan(request(), env)).status).toBe(200);
    expect(state.writes.filter((w) => w.path.endsWith("/goals"))).toHaveLength(0);
    expect((await runSalePlan(request({ ...payload, goalId: "goal-1", draft: { ...draft, maxSpendVnd: 1 } }), env)).status).toBe(409);
    state.setSaved({ goal_id: "goal-1", scope: "campaign", scope_id: "CD002", active: true,
      kind: draft.kind, target_units: draft.targetUnits, deadline: draft.deadline,
      params: { planner_request_hash: await hashText(JSON.stringify({ campaignId: "CD002", draft })),
        max_spend_vnd: 9999999, max_loss_vnd: 0, sell_target_prob: 0.8 } });
    expect((await runSalePlan(request({ ...payload, goalId: "goal-1" }), env)).status).toBe(409);
  });
  it("retains explicitly excluded unsupported requests for the goal audit", async () => {
    const { writes } = mockBackend();
    expect((await runSalePlan(request({ ...payload, acknowledgedUnresolved: true, draft: { ...draft, unresolved: ["Giảm giá 50% chưa mô phỏng"] } }), env)).status).toBe(200);
    expect((writes[0]!.body.params as Record<string, unknown>).planner_excluded_requirements).toEqual(["Giảm giá 50% chưa mô phỏng"]);
  });
  it("requires session and same-origin writes for both interpretation and simulation", async () => {
    const secret = "test-sale-planner-session-secret-32-bytes";
    const auth = { SESSION_SECRET: secret, ADMIN_PASSWORD_HASH: "configured", DB: { prepare: () => ({ first: async () => ({ ok: true }) }) }, ASSETS: { fetch: async () => new Response("app") } };
    const signed = `admin.${Math.floor(Date.now() / 1000) + 3600}.nonce`;
    const cookie = `megaday_session=${signed}.${createHmac("sha256", secret).update(signed).digest("base64url")}`;
    const { fetch } = mockBackend();
    for (const route of ["interpret", "run"]) {
      expect((await worker.fetch(new Request(`https://app.test/api/sale-planner/${route}`, { method: "POST", body: "{}" }), auth)).status).toBe(401);
      expect((await worker.fetch(new Request(`https://app.test/api/sale-planner/${route}`, { method: "POST", headers: { Cookie: cookie, Origin: "https://evil.test" }, body: "{}" }), auth)).status).toBe(403);
    }
    expect(fetch).not.toHaveBeenCalled();
  });
});
