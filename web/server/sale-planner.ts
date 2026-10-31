import { z } from "zod";
import { goalDraftSchema } from "../src/planner/goal-contract";
import type { PlannerContext, PlannerRunError } from "../src/planner/planner-api";
import { hashText } from "../src/studio/sources";
import { assistantSkuSchema } from "../src/planner/assistant-contract";

export type PlannerEnv = { DATA_BACKEND_URL?: string; DATA_BACKEND_USER?: string; DATA_BACKEND_ROLE?: string };
const constraints = ["max_spend_vnd", "max_loss_vnd", "sell_target_prob", "min_marginal_return"];
const confirmedSchema = z.object({
  campaignId: z.string().regex(/^[\w.-]{1,150}$/), draft: goalDraftSchema,
  confirmed: z.literal(true), acknowledgedUnresolved: z.boolean(),
  goalId: z.string().regex(/^[\w.-]{1,150}$/).optional(),
  focusSku: assistantSkuSchema.optional(),
}).strict();
export class BackendFailure extends Error {
  constructor(public status: number, public code: string, message: string, public details?: Record<string, unknown>) { super(message); }
}
export function plannerBackend(env: PlannerEnv) {
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
        body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(body === undefined ? 15000 : 90000),
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
    backendConfigured: !!api, canCreateGoals: false, referenceTime: new Date().toISOString(), dataAsOf: null, campaigns: [],
    notice: api ? null : "Backend mô phỏng chưa kết nối. Bạn vẫn có thể soạn và chỉnh mục tiêu; chưa thể tính phương án từ dữ liệu thật.",
  };
  if (api) {
    try {
      const [capability, me] = await Promise.all([api("goal-planner/capabilities"), api("me")]);
      if (!supportsPlanner(capability)) throw new BackendFailure(409, "BACKEND_UPGRADE_REQUIRED", "Backend cần cập nhật để áp dụng đầy đủ ràng buộc mục tiêu.");
      context.canCreateGoals = (me.data as { can_edit_goals?: boolean; can_run_scenarios?: boolean } | undefined)?.can_edit_goals === true &&
        (me.data as { can_run_scenarios?: boolean } | undefined)?.can_run_scenarios === true;
      if (!context.canCreateGoals) context.notice = "Tài khoản backend cần quyền đặt mục tiêu và chạy mô phỏng (lead).";
      let cursor: string | null = null;
      for (let page = 0; page < 20; page++) {
        const result = await api(`campaigns?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
        const campaigns = z.array(z.object({ campaign_id: z.string(), name: z.string() })).parse(result.data);
        context.campaigns.push(...campaigns.map((campaign) => ({ id: campaign.campaign_id, name: campaign.name })));
        context.dataAsOf = (result.meta as { data_as_of?: string | null } | undefined)?.data_as_of ?? context.dataAsOf;
        cursor = typeof result.next_cursor === "string" ? result.next_cursor : null;
        if (!cursor) break;
        if (page === 19) context.notice = "Đang hiển thị 2.000 chiến dịch đầu tiên; cần thu hẹp dữ liệu để xem thêm.";
      }
    } catch (error) {
      context.canCreateGoals = false;
      context.notice = error instanceof BackendFailure
        ? error.status === 404 ? "Backend cần cập nhật tính năng mục tiêu sale trước khi chạy." : error.message
        : "Không đọc được danh sách chiến dịch từ backend.";
    }
  }
  return Response.json(context, { headers: { "Cache-Control": "no-store" } });
}
export async function runSalePlan(request: Request, env: PlannerEnv): Promise<Response> {
  let goalId: string | null = null;
  let stage: PlannerRunError["stage"] = "validation";
  const failure = (status: number, code: string, message: string, details?: Record<string, unknown>) =>
    Response.json({ error: { code, message, details }, goalId, stage } satisfies PlannerRunError, { status });
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
    if (!me?.can_edit_goals || !me.can_run_scenarios)
      return failure(403, "FORBIDDEN", "Tài khoản backend chưa có quyền đặt mục tiêu và chạy mô phỏng.");
    const signature = await hashText(JSON.stringify({ campaignId, draft, ...(focusSku ? { focusSku } : {}) }));
    const params: Record<string, unknown> = {
      planner_request_hash: signature, planner_source_text: draft.text,
      planner_excluded_requirements: draft.unresolved, planner_start: draft.start,
    };
    if (focusSku) params.focus_sku = focusSku;
    if (draft.maxSpendVnd !== null) params.max_spend_vnd = draft.maxSpendVnd;
    if (draft.maxLossVnd !== null) params.max_loss_vnd = draft.maxLossVnd;
    if (draft.sellTargetProbability !== null) params.sell_target_prob = draft.sellTargetProbability;
    if (draft.minMarginalReturn !== null) params.min_marginal_return = draft.minMarginalReturn;
    const matches = (goal: Record<string, unknown>) => {
      const stored = goal.params as Record<string, unknown> | undefined;
      return goal.scope === "campaign" && goal.scope_id === campaignId && goal.active === true &&
        goal.kind === draft.kind && goal.target_units === draft.targetUnits &&
        typeof goal.deadline === "string" && Date.parse(goal.deadline) === end &&
        stored?.planner_request_hash === signature && stored?.focus_sku === params.focus_sku && constraints.every((key) => stored?.[key] === params[key]);
    };
    stage = "goal";
    if (parsed.data.goalId) {
      const existing = (await api(`goals/${encodeURIComponent(parsed.data.goalId)}`)).data as Record<string, unknown>;
      if (!matches(existing))
        return failure(409, "GOAL_CHANGED", "Mục tiêu đã thay đổi hoặc không khớp bản xác nhận. Hãy xem lại và tạo mục tiêu mới.");
      goalId = parsed.data.goalId;
    } else {
      // Recover a saved goal when an earlier response was lost; never blindly create on retry.
      const goals = await api(`goals?scope=campaign&scope_id=${encodeURIComponent(campaignId)}&active=true&limit=200`);
      const previous = (goals.data as Record<string, unknown>[]).find(matches);
      if (previous) goalId = z.string().min(1).parse(previous.goal_id);
      else {
        const saved = await api("goals", { scope: "campaign", scope_id: campaignId, kind: draft.kind,
          deadline: draft.deadline, target_units: draft.targetUnits, params });
        goalId = z.string().min(1).parse((saved.data as { goal_id?: unknown } | undefined)?.goal_id);
      }
    }
    stage = "simulation";
    const scenario = await api("scenarios/run", { campaign_id: campaignId, goal_id: goalId, start: draft.start,
      horizon_hours: horizon, samples: 1000, seed: 20261111 });
    const actual = z.object({ data: z.object({ scenario_id: z.string().min(1),
      request: z.object({ campaign_id: z.string(), goal_id: z.string() }), results: z.array(z.unknown()),
    }) }).parse(scenario);
    if (actual.data.request.campaign_id !== campaignId || actual.data.request.goal_id !== goalId)
      return failure(502, "SCENARIO_MISMATCH", "Kết quả không khớp mục tiêu đã xác nhận. Không sử dụng kết quả này.");
    if (focusSku) {
      const traced = z.object({ data: z.object({ input_hash: z.string().trim().min(1),
        calculation_trace: z.object({ version: z.literal(1), input_hash: z.string().trim().min(1),
          campaign_id: z.string(), focus_sku: z.string() }),
      }) }).safeParse(scenario);
      if (!traced.success || traced.data.data.calculation_trace.focus_sku !== focusSku ||
          traced.data.data.calculation_trace.campaign_id !== campaignId ||
          traced.data.data.calculation_trace.input_hash !== traced.data.data.input_hash)
        return failure(502, "CALCULATION_TRACE_MISMATCH", "Dấu vết tính toán thiếu hoặc không khớp SKU, chiến dịch và dữ liệu của lần tính. Không sử dụng kết quả này; mục tiêu đã lưu để thử lại.");
    }
    return Response.json({ goalId, scenario }, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    if (error instanceof BackendFailure) return failure(error.status, error.code, error.message, error.details);
    return failure(stage === "validation" ? 422 : 502, "PLANNER_ERROR", stage === "validation"
      ? "Yêu cầu chưa hợp lệ. Hãy kiểm tra lại mục tiêu."
      : "Không xác nhận được kết quả từ backend. Mục tiêu có thể đã lưu; thử lại với cùng nội dung để khôi phục.");
  }
}
