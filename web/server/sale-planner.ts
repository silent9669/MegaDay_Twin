import { z } from "zod";
import { goalDraftSchema } from "../src/planner/goal-contract";
import type { PlannerContext, PlannerRunError } from "../src/planner/planner-api";
import { assistantSkuSchema } from "../src/planner/assistant-contract";

export type PlannerEnv = { DATA_BACKEND_URL?: string; DATA_BACKEND_USER?: string; DATA_BACKEND_ROLE?: string };
const constraints = ["max_spend_vnd", "max_loss_vnd", "sell_target_prob", "min_marginal_return", "preview_apply"];
const identifier = z.string().regex(/^[\w.-]{1,150}$/);
const revisionSchema = z.strictObject({ goal_id: identifier, version: z.number().int().positive() });
const goalSchema = z.object({ goal_id: identifier, scope: z.literal("campaign"), scope_id: identifier,
  kind: z.string(), deadline: z.string(), target_units: z.number().nullable(),
  params: z.record(z.string(), z.unknown()), active: z.boolean(), version: z.number().int(), label: z.string().optional() });
const confirmedSchema = z.object({
  campaignId: z.string().regex(/^[\w.-]{1,150}$/), draft: goalDraftSchema,
  confirmed: z.literal(true), acknowledgedUnresolved: z.boolean(),
  expectedGoal: revisionSchema.nullable(),
  focusSku: assistantSkuSchema.optional(),
}).strict();
export class BackendFailure extends Error {
  constructor(public status: number, public code: string, message: string, public details?: Record<string, unknown>) { super(message); }
}
export function plannerBackend(env: PlannerEnv, signal?: AbortSignal) {
  if (!env.DATA_BACKEND_URL || !env.DATA_BACKEND_USER || !env.DATA_BACKEND_ROLE) return null;
  let base: URL;
  try { base = new URL(env.DATA_BACKEND_URL.endsWith("/") ? env.DATA_BACKEND_URL : `${env.DATA_BACKEND_URL}/`); }
  catch { return null; }
  if (base.protocol !== "https:" && !(base.protocol === "http:" && ["localhost", "127.0.0.1"].includes(base.hostname))) return null;
  return async (path: string, body?: unknown): Promise<Record<string, unknown>> => {
    let response: Response;
    try {
      response = await fetch(new URL(path, base), {
        method: body === undefined ? "GET" : "POST", redirect: "error",
        headers: { "X-User": env.DATA_BACKEND_USER!, "X-Role": env.DATA_BACKEND_ROLE!, "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body), signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(body === undefined ? 15000 : 90000)]) : AbortSignal.timeout(body === undefined ? 15000 : 90000),
      });
    } catch { throw new BackendFailure(502, "BACKEND_UNAVAILABLE", "Không nhận được phản hồi từ backend. Dữ liệu đã lưu được giữ nguyên; hãy thử lại sau."); }
    let result: Record<string, unknown>;
    try { result = await response.json() as Record<string, unknown>; }
    catch { throw new BackendFailure(502, "BACKEND_RESPONSE", "Backend trả về phản hồi không hợp lệ."); }
    if (!response.ok) {
      const error = result.error as { message?: unknown; code?: unknown; details?: Record<string, unknown> } | undefined;
      throw new BackendFailure(response.status, typeof error?.code === "string" ? error.code : "BACKEND_ERROR",
        typeof error?.message === "string" ? error.message.slice(0, 1500) : "Backend chưa thể xử lý yêu cầu.", error?.details);
    }
    return result;
  };
}
function supportsPlanner(result: Record<string, unknown>) {
  const data = result.data as { version?: number; constraints?: string[] } | undefined;
  return data?.version === 1 && constraints.every((key) => data.constraints?.includes(key));
}
export async function plannerContext(env: PlannerEnv): Promise<Response> {
  const api = plannerBackend(env);
  const context: PlannerContext = {
    backendConfigured: !!api, canCreateGoals: false, canPreview: false, canApply: false, referenceTime: new Date().toISOString(), dataAsOf: null, campaigns: [],
    notice: api ? null : "You can draft and review goals. Connect the decision backend to simulate with real data.",
  };
  if (api) {
    try {
      const [capability, me] = await Promise.all([api("goal-planner/capabilities"), api("me")]);
      if (!supportsPlanner(capability)) throw new BackendFailure(409, "BACKEND_UPGRADE_REQUIRED", "Backend cần cập nhật để áp dụng đầy đủ ràng buộc mục tiêu.");
      const permissions = me.data as { can_edit_goals?: boolean; can_run_scenarios?: boolean } | undefined;
      context.canPreview = permissions?.can_run_scenarios === true;
      context.canApply = permissions?.can_edit_goals === true;
      context.canCreateGoals = context.canApply;
      if (!context.canPreview) context.notice = "This backend account cannot preview simulations.";
      else if (!context.canApply) context.notice = "You can preview simulations. Applying a goal requires lead permission.";
      let cursor: string | null = null;
      for (let page = 0; page < 20; page++) {
        const result = await api(`campaigns?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
        const campaigns = z.array(z.object({ campaign_id: z.string(), name: z.string() })).parse(result.data);
        context.campaigns.push(...campaigns.map((campaign) => ({ id: campaign.campaign_id, name: campaign.name })));
        context.dataAsOf = (result.meta as { data_as_of?: string | null } | undefined)?.data_as_of ?? context.dataAsOf;
        cursor = typeof result.next_cursor === "string" ? result.next_cursor : null;
        if (!cursor) break;
        if (page === 19) context.notice = "Showing the first 2,000 campaigns. Narrow the data to see more.";
      }
    } catch (error) {
      context.canCreateGoals = false;
      context.canPreview = false; context.canApply = false;
      context.notice = error instanceof BackendFailure
        ? error.status === 404 ? "Update the backend goal-planning capabilities before running." : error.message
        : "Could not read campaigns from the decision backend.";
    }
  }
  return Response.json(context, { headers: { "Cache-Control": "no-store" } });
}
export async function runSalePlan(request: Request, env: PlannerEnv): Promise<Response> {
  let previewId: string | null = null;
  let stage: PlannerRunError["stage"] = "validation";
  const failure = (status: number, code: string, message: string, details?: Record<string, unknown>) =>
    Response.json({ error: { code, message, details }, goalId: null, previewId, stage } satisfies PlannerRunError, { status });
  try {
    const raw = await request.text();
    if (raw.length > 24000) return failure(413, "TOO_LARGE", "Mục tiêu quá dài.");
    const parsed = confirmedSchema.safeParse(JSON.parse(raw));
    if (!parsed.success) return failure(422, "INVALID_PLAN", "Hãy kiểm tra các trường mục tiêu và xác nhận lại.");
    const { campaignId, draft, acknowledgedUnresolved, focusSku } = parsed.data;
    if (!draft.kind || !draft.start || !draft.deadline)
      return failure(422, "MISSING_GOAL_FIELDS", "Cần chọn loại mục tiêu, giờ bắt đầu và hạn kết thúc.");
    const start = Date.parse(draft.start), end = Date.parse(draft.deadline);
    const horizon = (end - start) / 3600000;
    if (!Number.isFinite(start) || !Number.isFinite(end) || start % 3600000 || end % 3600000 || !Number.isInteger(horizon) || horizon < 1 || horizon > 72)
      return failure(422, "INVALID_WINDOW", "Khoảng mô phỏng phải từ 1–72 giờ, bắt đầu và kết thúc ở giờ tròn, có múi giờ.");
    if (draft.kind === "sell_target" && (!Number.isSafeInteger(draft.targetUnits) || draft.targetUnits! <= 0))
      return failure(422, "MISSING_TARGET", "Mục tiêu số lượng cần số sản phẩm nguyên lớn hơn 0.");
    if (draft.kind !== "sell_target" && draft.targetUnits !== null)
      return failure(422, "UNUSED_TARGET", "Chọn mục tiêu bán đủ số lượng hoặc bỏ chỉ tiêu số lượng chưa được áp dụng.");
    if (draft.sellTargetProbability !== null && draft.kind !== "sell_target")
      return failure(422, "UNUSED_PROBABILITY", "Xác suất đạt số lượng chỉ áp dụng cho mục tiêu bán đủ số lượng.");
    if (draft.minMarginalReturn !== null && draft.kind !== "efficiency")
      return failure(422, "UNUSED_EFFICIENCY", "Điều kiện hiệu quả chi chỉ áp dụng khi chọn mục tiêu hiệu quả chi.");
    if (draft.unresolved.length && !acknowledgedUnresolved)
      return failure(422, "UNRESOLVED_GOAL", "Xác nhận rõ những yêu cầu chưa được mô phỏng trước khi tiếp tục.");
    const api = plannerBackend(env);
    if (!api) return failure(503, "BACKEND_UNCONFIGURED", "Backend chưa kết nối; không thể tạo kết quả mô phỏng thật.");
    const capability = await api("goal-planner/capabilities");
    if (!supportsPlanner(capability))
      return failure(409, "BACKEND_UPGRADE_REQUIRED", "Backend chưa hỗ trợ đầy đủ ràng buộc mục tiêu.");
    if (focusSku) {
      const supported = (capability.data as { constraints?: string[] } | undefined)?.constraints;
      if (!supported?.includes("focus_sku") || !supported.includes("calculation_trace"))
        return failure(409, "BACKEND_UPGRADE_REQUIRED", "Backend cần hỗ trợ mục tiêu theo SKU và dấu vết tính toán trước khi chạy.");
      const campaign = (await api(`campaigns/${encodeURIComponent(campaignId)}`)).data as { skus?: string[] } | undefined;
      if (!campaign?.skus?.includes(focusSku))
        return failure(422, "SKU_CAMPAIGN_MISMATCH", "SKU đã chọn không thuộc chiến dịch này. Hãy tải lại dữ liệu và chọn đúng chiến dịch.");
    }
    const me = (await api("me")).data as { can_edit_goals?: boolean; can_run_scenarios?: boolean } | undefined;
    if (!me?.can_run_scenarios)
      return failure(403, "FORBIDDEN", "Tài khoản backend chưa có quyền mô phỏng thử.");
    const params: Record<string, unknown> = {
      planner_source_text: draft.text,
      planner_excluded_requirements: draft.unresolved, planner_start: draft.start,
    };
    if (focusSku) params.focus_sku = focusSku;
    if (draft.maxSpendVnd !== null) params.max_spend_vnd = draft.maxSpendVnd;
    if (draft.maxLossVnd !== null) params.max_loss_vnd = draft.maxLossVnd;
    if (draft.sellTargetProbability !== null) params.sell_target_prob = draft.sellTargetProbability;
    if (draft.minMarginalReturn !== null) params.min_marginal_return = draft.minMarginalReturn;
    stage = "preview";
    // The backend owns inactive draft storage, constraint inheritance and CAS checks.
    // This gateway must never POST /goals or run a standard (active) recommendation.
    const preview = await api("sale-plans/preview", { campaign_id: campaignId,
      goal: { kind: draft.kind, deadline: draft.deadline, target_units: draft.targetUnits, params },
      expected_goal: parsed.data.expectedGoal, ...(focusSku ? { focus_sku: focusSku } : {}),
      start: draft.start, horizon_hours: horizon, samples: 1000, seed: 20261111 });
    const saved = z.object({ preview_id: identifier, base_goal: goalSchema.nullable(), effective_goal: goalSchema,
      scenario: z.record(z.string(), z.unknown()), changes: z.array(z.object({ field: z.string(), before: z.unknown(), after: z.unknown() })),
      notices: z.array(z.string()), can_apply: z.boolean(), applied_goal_id: identifier.nullable() }).parse(preview.data);
    previewId = saved.preview_id;
    const goalId = saved.effective_goal.goal_id;
    if (saved.effective_goal.scope_id !== campaignId || saved.effective_goal.active ||
      (saved.effective_goal.params.focus_sku ?? null) !== (focusSku ?? null))
      return failure(502, "PREVIEW_MISMATCH", "Bản mô phỏng không khớp phạm vi đã chọn. Mục tiêu đang áp dụng không bị thay đổi.");
    const expected = parsed.data.expectedGoal;
    if ((saved.base_goal && (!saved.base_goal.active || saved.base_goal.scope_id !== campaignId)) ||
      (saved.base_goal?.goal_id ?? null) !== (expected?.goal_id ?? null) ||
      (saved.base_goal?.version ?? null) !== (expected?.version ?? null))
      return failure(502, "PREVIEW_BASE_MISMATCH", "Bản mô phỏng không khớp mục tiêu bạn đã xem. Hãy tải lại kế hoạch.");
    const scenario = { data: saved.scenario, meta: preview.meta };
    const actual = z.object({ data: z.object({ scenario_id: z.string().min(1),
      request: z.object({ campaign_id: z.string(), goal_id: z.string() }), results: z.array(z.unknown()),
    }) }).parse(scenario);
    if (actual.data.request.campaign_id !== campaignId || actual.data.request.goal_id !== goalId)
      return failure(502, "SCENARIO_MISMATCH", "Kết quả không khớp mục tiêu đã xác nhận. Không sử dụng kết quả này.");
    if (saved.scenario.recommendation != null && !z.object({ active: z.literal(false), what_if: z.literal(true) }).safeParse(saved.scenario.recommendation).success)
      return failure(502, "PREVIEW_RECOMMENDATION_MISMATCH", "Backend chưa xác nhận đây là đề xuất mô phỏng thử. Hãy tải lại kế hoạch hiện tại trước khi tiếp tục.");
    {
      const traced = z.object({ data: z.object({ input_hash: z.string().trim().min(1),
        calculation_trace: z.object({ version: z.literal(1), input_hash: z.string().trim().min(1),
          campaign_id: z.string(), focus_sku: z.string().nullable() }),
      }) }).safeParse(scenario);
      if (!traced.success || traced.data.data.calculation_trace.focus_sku !== (focusSku ?? null) ||
          traced.data.data.calculation_trace.campaign_id !== campaignId ||
          traced.data.data.calculation_trace.input_hash !== traced.data.data.input_hash)
        return failure(502, "CALCULATION_TRACE_MISMATCH", "Dấu vết tính toán thiếu hoặc không khớp lần mô phỏng. Mục tiêu đang áp dụng không bị thay đổi.");
    }
    return Response.json({ previewId, goalId, scenario, baseGoal: saved.base_goal, effectiveGoal: saved.effective_goal,
      changes: saved.changes, notices: saved.notices, canApply: saved.can_apply && me.can_edit_goals === true,
      appliedGoalId: saved.applied_goal_id }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    if (error instanceof BackendFailure) return failure(error.status, error.code, error.message, error.details);
    return failure(stage === "validation" ? 422 : 502, "PLANNER_ERROR", stage === "validation"
      ? "Yêu cầu chưa hợp lệ. Hãy kiểm tra lại mục tiêu."
      : "Không xác nhận được bản mô phỏng từ backend. Mục tiêu đang áp dụng không bị thay đổi; bạn có thể mô phỏng lại.");
  }
}

function plannerFailure(error: unknown, stage: "validation" | "apply") {
  const known = error instanceof BackendFailure;
  return Response.json({ error: { code: known ? error.code : "INVALID_PLAN",
    message: known ? error.message : "Yêu cầu hoặc phản hồi kế hoạch chưa hợp lệ.", ...(known && error.details ? { details: error.details } : {}) },
    goalId: null, previewId: null, stage }, { status: known ? error.status : 422 });
}
export async function activeSalePlan(request: Request, env: PlannerEnv): Promise<Response> {
  try {
    const campaignId = identifier.parse(new URL(request.url).searchParams.get("campaignId"));
    const api = plannerBackend(env);
    if (!api) throw new BackendFailure(503, "BACKEND_UNCONFIGURED", "Backend chưa kết nối.");
    if (!supportsPlanner(await api("goal-planner/capabilities")))
      throw new BackendFailure(409, "BACKEND_UPGRADE_REQUIRED", "Backend cần hỗ trợ mô phỏng thử và áp dụng riêng.");
    const result = await api(`sale-plans/context?campaign_id=${encodeURIComponent(campaignId)}`);
    const data = z.object({ campaign_id: identifier, active_goal: goalSchema.nullable(), base_goal: revisionSchema.nullable() }).parse(result.data);
    if (data.campaign_id !== campaignId || (data.active_goal && (!data.active_goal.active || data.active_goal.scope_id !== campaignId)) ||
      (data.active_goal?.goal_id ?? null) !== (data.base_goal?.goal_id ?? null) ||
      (data.active_goal?.version ?? null) !== (data.base_goal?.version ?? null))
      throw new BackendFailure(502, "GOAL_CONTEXT_MISMATCH", "Mục tiêu trả về không khớp chiến dịch. Hãy tải lại.");
    return Response.json({ campaignId, activeGoal: data.active_goal, baseGoal: data.base_goal }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) { return plannerFailure(error, "validation"); }
}
export async function applySalePlan(request: Request, env: PlannerEnv): Promise<Response> {
  let previewId: string | null = null;
  let applying = false;
  try {
    const raw = await request.text();
    if (raw.length > 3000) throw new BackendFailure(413, "TOO_LARGE", "Yêu cầu áp dụng quá dài.");
    const body = z.strictObject({ previewId: identifier, confirmed: z.literal(true), expectedGoal: revisionSchema.nullable() }).parse(JSON.parse(raw));
    previewId = body.previewId;
    const api = plannerBackend(env);
    if (!api) throw new BackendFailure(503, "BACKEND_UNCONFIGURED", "Backend chưa kết nối.");
    if (!supportsPlanner(await api("goal-planner/capabilities")))
      throw new BackendFailure(409, "BACKEND_UPGRADE_REQUIRED", "Backend cần hỗ trợ áp dụng bản mô phỏng có kiểm tra phiên bản.");
    const me = (await api("me")).data as { can_edit_goals?: boolean } | undefined;
    if (!me?.can_edit_goals) throw new BackendFailure(403, "FORBIDDEN", "Cần quyền lead để áp dụng mục tiêu.");
    applying = true;
    const result = await api(`sale-plans/${encodeURIComponent(body.previewId)}/apply`, { confirmed: true, expected_goal: body.expectedGoal });
    const data = z.object({ preview_id: identifier, goal: goalSchema, already_applied: z.boolean() }).parse(result.data);
    if (data.preview_id !== body.previewId || !data.goal.active)
      throw new BackendFailure(502, "APPLY_RESPONSE_MISMATCH", "Chưa xác nhận được kết quả áp dụng. Hãy tải lại mục tiêu hiện tại trước khi tiếp tục.");
    return Response.json({ previewId: data.preview_id, goal: data.goal, alreadyApplied: data.already_applied }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    if (applying && (!(error instanceof BackendFailure) || error.status >= 500))
      return Response.json({ error: { code: "APPLY_OUTCOME_UNKNOWN", message: "Chưa xác nhận được kết quả áp dụng. Hãy tải lại mục tiêu hiện tại hoặc thử lại cùng bản mô phỏng; không tạo yêu cầu mới." },
        goalId: null, previewId, stage: "apply" }, { status: 502 });
    return plannerFailure(error, "apply");
  }
}
