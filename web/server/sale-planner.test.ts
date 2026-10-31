// @vitest-environment node
import { createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { activeSalePlan, applySalePlan, plannerContext, runSalePlan, type PlannerEnv } from "./sale-planner";
import type { GoalDraft } from "../src/planner/goal-contract";
import worker from "../worker";

const env: PlannerEnv = { DATA_BACKEND_URL: "https://backend.test/api/v1", DATA_BACKEND_USER: "lead-1", DATA_BACKEND_ROLE: "lead" };
const draft: GoalDraft = { text: "Bán đủ 200 sản phẩm, chi tối đa 3 triệu", kind: "sell_target", targetUnits: 200,
  start: "2026-11-11T19:00:00+07:00", deadline: "2026-11-11T23:00:00+07:00", maxSpendVnd: 3000000,
  maxLossVnd: 0, sellTargetProbability: 0.8, minMarginalReturn: null, unresolved: [] };
const active = { goal_id: "goal-current", scope: "campaign", scope_id: "CD002", kind: "profit", deadline: draft.deadline!,
  target_units: null, params: { max_spend_vnd: 5000000 }, active: true, version: 4 };
const expectedGoal = { goal_id: active.goal_id, version: active.version };
const payload = { campaignId: "CD002", draft, confirmed: true, acknowledgedUnresolved: false, expectedGoal };
const request = (body: unknown = payload, path = "run") => new Request(`https://app.test/api/sale-planner/${path}`, { method: "POST", body: JSON.stringify(body) });
afterEach(() => vi.unstubAllGlobals());
function backend(options: { old?: boolean; lead?: boolean; operator?: boolean; failPreview?: boolean; stale?: boolean;
  trace?: Record<string, unknown> | null; base?: Record<string, unknown> | null; applied?: boolean; badApply?: boolean; activeRec?: boolean } = {}) {
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  const fetch = vi.fn(async (url: URL | string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    if (body) writes.push({ path, body });
    if (path.endsWith("/goal-planner/capabilities")) return Response.json({ data: { version: 1,
      constraints: ["max_spend_vnd", "max_loss_vnd", "sell_target_prob", "min_marginal_return", "focus_sku", "calculation_trace", ...(options.old ? [] : ["preview_apply"])] } });
    if (path.endsWith("/me")) return Response.json({ data: { can_edit_goals: options.lead !== false, can_run_scenarios: options.operator !== false } });
    if (path.endsWith("/campaigns")) return Response.json({ data: [{ campaign_id: "CD002", name: "Sale" }], next_cursor: null });
    if (path.endsWith("/campaigns/CD002")) return Response.json({ data: { skus: ["SKU1", "SKU2"] } });
    if (path.endsWith("/sale-plans/context")) return Response.json({ data: { campaign_id: "CD002", active_goal: active, base_goal: expectedGoal } });
    if (options.stale && body) return Response.json({ error: { code: "GOAL_CHANGED", message: "Mục tiêu đã thay đổi" } }, { status: 409 });
    if (path.endsWith("/sale-plans/preview")) {
      if (options.failPreview) return Response.json({ error: { code: "BLOCKING_DATA_ISSUE", message: "Thiếu giá vốn", details: { reasons: ["SKU1 thiếu giá vốn"] } } }, { status: 422 });
      return Response.json({ data: {
        preview_id: "preview-1", base_goal: options.base === undefined ? active : options.base,
        effective_goal: { ...active, ...body!.goal as object, goal_id: "goal-draft", active: false, version: 1 },
        changes: [{ field: "kind", before: "profit", after: "sell_target" }], notices: [], can_apply: true, applied_goal_id: null,
        scenario: { scenario_id: "scn-1", request: { campaign_id: "CD002", goal_id: "goal-draft" }, results: [],
          recommendation: options.activeRec ? { active: true, what_if: false } : { active: false, what_if: true },
          input_hash: "hash-1", calculation_trace: options.trace === undefined
            ? { version: 1, input_hash: "hash-1", campaign_id: "CD002", focus_sku: body!.focus_sku ?? null } : options.trace },
      }, meta: {} });
    }
    if (path.endsWith("/sale-plans/preview-1/apply")) return Response.json(options.badApply ? {} : {
      data: { preview_id: "preview-1", goal: { ...active, goal_id: "goal-applied", version: 5 }, already_applied: options.applied ?? false } });
    throw Error(`Unexpected backend path: ${path}`);
  });
  vi.stubGlobal("fetch", fetch);
  return { fetch, writes };
}
describe("sale plan preview/apply gateway", () => {
  it("loads active goal and revision for comparison", async () => {
    backend();
    expect(await (await activeSalePlan(new Request("https://app.test/api/sale-planner/active?campaignId=CD002"), env)).json())
      .toMatchObject({ campaignId: "CD002", activeGoal: active, baseGoal: expectedGoal });
  });
  it("only calls preview, with no active goal or normal recommendation write", async () => {
    const { writes } = backend();
    expect(await (await runSalePlan(request({ ...payload, focusSku: "SKU1" }), env)).json())
      .toMatchObject({ previewId: "preview-1", baseGoal: active, effectiveGoal: { active: false }, canApply: true });
    expect(writes).toHaveLength(1);
    expect(writes[0]).toMatchObject({ path: "/api/v1/sale-plans/preview", body: { expected_goal: expectedGoal, focus_sku: "SKU1",
      goal: { params: { max_spend_vnd: 3000000, max_loss_vnd: 0, focus_sku: "SKU1" } }, horizon_hours: 4 } });
  });
  it("rejects old save-and-run callers and invalid drafts before any backend call", async () => {
    const { fetch } = backend();
    for (const body of [{ ...payload, expectedGoal: undefined }, { ...payload, goalId: "old" }, { ...payload, confirmed: false },
      { ...payload, draft: { ...draft, unresolved: ["Giảm giá chưa hỗ trợ"] } }, { ...payload, draft: { ...draft, kind: "profit" } },
      { ...payload, draft: { ...draft, start: "2026-11-11T19:30:00+07:00" } }, { ...payload, draft: { ...draft, minMarginalReturn: 2 } }])
      expect((await runSalePlan(request(body), env)).status).toBe(422);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("requires preview capability without falling back to active writes", async () => {
    const { writes } = backend({ old: true });
    expect((await runSalePlan(request(), env)).status).toBe(409);
    expect(await (await plannerContext(env)).json()).toMatchObject({ canPreview: false, canApply: false });
    expect(writes).toEqual([]);
  });
  it("lets operators preview but only leads apply", async () => {
    const { writes } = backend({ lead: false });
    expect(await (await plannerContext(env)).json()).toMatchObject({ canPreview: true, canApply: false });
    expect(await (await runSalePlan(request(), env)).json()).toMatchObject({ canApply: false });
    expect((await applySalePlan(request({ previewId: "preview-1", confirmed: true, expectedGoal }), env)).status).toBe(403);
    expect(writes).toHaveLength(1);
    backend({ operator: false }); expect((await runSalePlan(request(), env)).status).toBe(403);
  });
  it("keeps missing-data explanations and retries preview without saving active goals", async () => {
    const options = { failPreview: true }; const { writes } = backend(options);
    expect(await (await runSalePlan(request(), env)).json()).toMatchObject({ goalId: null, stage: "preview", error: { details: { reasons: ["SKU1 thiếu giá vốn"] } } });
    options.failPreview = false; expect((await runSalePlan(request(), env)).status).toBe(200);
    expect(writes.every((w) => w.path === "/api/v1/sale-plans/preview")).toBe(true);
  });
  it("propagates conflicts and rejects mismatched baseline or active preview recommendations", async () => {
    backend({ stale: true }); expect((await runSalePlan(request(), env)).status).toBe(409);
    expect((await applySalePlan(request({ previewId: "preview-1", confirmed: true, expectedGoal }), env)).status).toBe(409);
    for (const base of [{ ...active, version: 5 }, { ...active, scope_id: "OTHER" }, { ...active, active: false }]) {
      backend({ base }); expect((await runSalePlan(request(), env)).status).toBe(502);
    }
    backend({ activeRec: true }); expect((await runSalePlan(request(), env)).status).toBe(502);
  });
  it("requires explicit apply confirmation and only forwards stored preview identifiers", async () => {
    const { writes } = backend({ applied: true });
    for (const body of [{ previewId: "preview-1", expectedGoal }, { previewId: "preview-1", confirmed: true },
      { previewId: "preview-1", expectedGoal, confirmed: true, draft }]) expect((await applySalePlan(request(body), env)).status).toBe(422);
    expect(writes).toEqual([]);
    expect(await (await applySalePlan(request({ previewId: "preview-1", confirmed: true, expectedGoal }), env)).json())
      .toMatchObject({ goal: { active: true, version: 5 }, alreadyApplied: true });
    expect(writes[0]).toEqual({ path: "/api/v1/sale-plans/preview-1/apply", body: { confirmed: true, expected_goal: expectedGoal } });
  });
  it("reports uncertain apply outcome as retriable, never as a validated failed mutation", async () => {
    backend({ badApply: true }); const response = await applySalePlan(request({ previewId: "preview-1", confirmed: true, expectedGoal }), env);
    expect(response.status).toBe(502); expect(await response.json()).toMatchObject({ previewId: "preview-1", stage: "apply" });
  });
  it("validates evidence in both campaign and SKU previews", async () => {
    for (const trace of [null, { version: 1, input_hash: "wrong", campaign_id: "CD002", focus_sku: null }]) {
      backend({ trace }); expect((await runSalePlan(request(), env)).status).toBe(502);
    }
    const { writes } = backend(); expect((await runSalePlan(request({ ...payload, focusSku: "OTHER" }), env)).status).toBe(422);
    expect(writes).toEqual([]);
  });
  it("never invents outcomes without backend", async () => {
    const { fetch } = backend(); expect((await runSalePlan(request(), {})).status).toBe(503);
    expect(await (await plannerContext({})).json()).toMatchObject({ backendConfigured: false, canPreview: false });
    expect(fetch).not.toHaveBeenCalled();
  });
  it("authenticates and checks same-origin for interpretation, preview and apply", async () => {
    const secret = "test-sale-planner-session-secret-32-bytes";
    const auth = { SESSION_SECRET: secret, ADMIN_PASSWORD_HASH: "configured", DB: { prepare: () => ({ first: async () => ({ ok: true }) }) }, ASSETS: { fetch: async () => new Response("app") } };
    const signed = `admin.${Math.floor(Date.now() / 1000) + 3600}.nonce`;
    const cookie = `megaday_session=${signed}.${createHmac("sha256", secret).update(signed).digest("base64url")}`;
    const { fetch } = backend();
    for (const route of ["interpret", "run", "apply"]) {
      expect((await worker.fetch(new Request(`https://app.test/api/sale-planner/${route}`, { method: "POST", body: "{}" }), auth)).status).toBe(401);
      expect((await worker.fetch(new Request(`https://app.test/api/sale-planner/${route}`, { method: "POST", headers: { Cookie: cookie, Origin: "https://evil.test" }, body: "{}" }), auth)).status).toBe(403);
    }
    expect(fetch).not.toHaveBeenCalled();
  });
});
