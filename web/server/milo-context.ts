import { z } from "zod";
import { modelSelectionSchema } from "../src/planner/demand-contract";
import { loadContext } from "./sku-assistant";
import { plannerBackend, type PlannerEnv } from "./sale-planner";
import { snapshot } from "../src/city/monitor/changes";
import type { SourcePack } from "../src/studio/sources";
import type { Doc, Sandbox } from "../src/studio/workspace-types";
import type { AssistantContext } from "../src/planner/assistant-contract";

export type MiloSelection = { screen?: string; sku?: string; campaignId?: string; scenarioId?: string };
type Storage = { fetch(request: Request): Promise<Response> };
const text = z.string().max(500);
const optionalNumber = z.number().finite().nullable().optional();
const activeGoalParamsSchema = z.object({
  max_spend_vnd: z.number().finite().nonnegative().optional(),
  max_loss_vnd: z.number().finite().nonnegative().optional(),
  sell_target_prob: z.number().finite().positive().max(1).optional(),
  min_marginal_return: z.number().finite().nonnegative().optional(),
  focus_sku: text.optional(),
  planner_start: text.optional(),
  planner_parent_goal_id: text.optional(),
  planner_parent_goal_version: z.number().int().positive().optional(),
  planner_parent_goal_kind: text.optional(),
  hq_progress: z.object({ units: z.number().finite(), spend: z.number().finite(),
    cash: z.number().finite(), ads_complete: z.boolean() }).optional(),
});
const goalBriefSchema = z.object({ goal_id: text, kind: text, label: text, deadline: text,
  target_units: optionalNumber, version: z.number().int().positive().optional(),
  params: activeGoalParamsSchema.optional(), created_at: text.optional(), confirmed_by: text.optional(),
  detail_verified: z.boolean().optional(),
});
const activeGoalSchema = goalBriefSchema.extend({ scope: z.literal("campaign"), scope_id: text,
  active: z.literal(true), version: z.number().int().positive(), params: activeGoalParamsSchema,
});
const campaignSchema = z.object({
  campaign_id: text, name: text, skus: z.array(text).max(2000),
  context_basis: z.enum(["selected", "sole-sku-membership-read-only"]).optional(),
  daily_budget_vnd: optionalNumber, spend_today_vnd: optionalNumber,
  status: text.nullable().optional(), status_at: text.nullable().optional(),
  goal: goalBriefSchema.nullable().optional(),
});
const distribution = z.object({ mean: z.number().finite(), p10: z.number().finite(), p50: z.number().finite(), p90: z.number().finite() });
const finiteSeries = z.array(z.number().finite()).max(168);
const primitive = z.union([z.string().max(500), z.number().finite(), z.boolean(), z.null()]);
const traceSkuSchema = z.object({ sku_id: text, in_campaign: z.boolean(), is_bundle: z.boolean(),
  stock_start: z.number().finite().nullable(), stock_unlimited: z.boolean(), unit_cost_vnd: z.number().finite().nullable(),
  price_hourly: finiteSeries, contribution_per_unit_hourly: finiteSeries, forecast_mean_hourly: finiteSeries,
  forecast_id: text.nullable(), forecast_label: text.nullable(), dispersion_k: z.number().finite(), sigma: z.number().finite(),
  leftover_value_per_unit: z.number().finite(), restock: z.array(z.object({ hour: z.number().finite(), units: z.number().finite() })).max(200),
  economics_by_day: z.array(z.object({ sku_id: text, day: text, eff_price: z.number().finite(), price_source: text,
    unit_cost: z.number().finite(), packaging_cost: z.number().finite(), fee_rate: z.number().finite(),
    m: z.number().finite(), is_bundle: z.boolean() })).max(200).optional(),
});
const traceSchema = z.object({
  version: z.literal(1), input_hash: text.min(1), campaign_id: text, focus_sku: text.nullable(),
  quantity_scope: z.enum(["sku", "campaign"]), economic_scope: z.literal("campaign"),
  start: text, horizon_hours: z.number().finite(), samples: z.number().finite(), seed: z.number().finite(),
  data_as_of: text.nullable(),
  goal: z.object({ kind: text, target_units: optionalNumber, params: activeGoalParamsSchema.optional() }),
  skus: z.array(traceSkuSchema).max(2000).optional(),
  assumptions: z.object({ stockout_penalty_per_unit: z.number().finite(), rank_loss: z.number().finite(),
    adstock_start: z.number().finite(), budget_source: text, daily_budget: finiteSeries, must_run_hours: finiteSeries }).optional(),
  equations: z.array(z.object({ id: text, formula: text, explanation: z.string().max(2000) })).max(30),
  actions: z.array(z.object({
    key: text, feasible: z.boolean().optional(), violations: z.array(text).optional(),
    metrics: z.object({ objective: distribution, contribution: distribution, spend: distribution,
      revenue: distribution.optional(), selling_cost: distribution.optional(), after_ads: distribution.optional(),
      leftover_value: distribution, stockout_penalty: distribution, units_sold: distribution,
      focus_units_sold: distribution.nullable() }),
    reconciliation: z.object({ sum_components_mean: z.number().finite(), objective_mean: z.number().finite(), residual_vnd: z.number().finite() }),
  })).max(500),
  sources: z.object({ batches: z.array(z.object({ batch_id: text, source_type: text, file_name: text,
    file_sha256: text.nullable(), as_of: text.nullable() })).max(200),
    forecasts: z.array(z.object({ forecast_id: text, sku_id: text, model_id: text, data_as_of: text.nullable(), model_selection: modelSelectionSchema.optional() })).max(2000),
    parameters: z.array(z.object({ key: text, value: z.union([primitive, z.array(primitive).max(100)]), version: primitive,
      scope: text.nullable(), scope_id: text.nullable(), set_by: text.nullable() })).max(2000).optional(),
    iroas: z.object({ source: text, estimate_id: text.nullable(), confidence: text.nullable(),
      delta: z.number().finite(), spend_scale: z.number().finite() }).optional(),
  }),
  limitations: z.array(z.string().max(2000)).max(50),
});
const scenarioSchema = z.object({ scenario_id: text, input_hash: text.nullable(),
  request: z.object({ campaign_id: text }), calculation_trace: traceSchema.nullable().optional(),
  created_at: text.nullable().optional(), blocked: z.boolean().optional(),
});
const historySchema = z.object({ sku: text, grain: z.literal("day"), start: text, end: text,
  points: z.array(z.object({ t: text, units: z.number().finite(), units_provisional: z.number().finite(),
    revenue_net_vnd: z.number().finite(), stock_end: optionalNumber, stockout: z.boolean(),
    ad_spend_vnd: optionalNumber, ad_spend_estimated: z.boolean(), flags: z.array(text).max(50) })).max(10),
});
type WorkspaceFacts = { sourceRevision: number; updatedAt: string; sandboxHour: number; observationOnly: true;
  orderRowsAtSandboxHour: number; campaignRows: number;
  selectedStock: { code: string; name: string; stock: number | null; asOf: string } | null;
  sources: { kind: string; name: string; hash: string; issueCount: number; issues: string[] }[] };
export type MiloContext = { selection: MiloSelection; skuContext: AssistantContext | null;
  salesHistory: z.infer<typeof historySchema> | null;
  campaign: z.infer<typeof campaignSchema> | null;
  scenario: { id: string; createdAt: string | null; trace: z.infer<typeof traceSchema>; truncated: boolean } | null;
  workspace: WorkspaceFacts | null; notices: string[] };

/** Read-only, bounded facts. Imported strings are data, never assistant instructions. */
export async function buildMiloContext(
  selection: MiloSelection, env: PlannerEnv, storage?: Storage, origin = "https://workspace.invalid",
): Promise<MiloContext> {
  const notices: string[] = [];
  const signal = AbortSignal.timeout(12000);
  let skuContext: AssistantContext | null = null;
  let salesHistory: z.infer<typeof historySchema> | null = null;
  let campaign: z.infer<typeof campaignSchema> | null = null;
  let scenario: { id: string; createdAt: string | null; trace: z.infer<typeof traceSchema>; truncated: boolean } | null = null;
  let workspace: WorkspaceFacts | null = null;
  const api = plannerBackend(env, signal);
  await Promise.all([
    (async () => {
      if (!storage) return;
      try {
        const [sourceResponse, sandboxResponse] = await Promise.all(["sources", "sandbox"].map((key) =>
          storage.fetch(new Request(new URL(`/api/workspace/${key}`, origin)))));
        if (!sourceResponse?.ok || !sandboxResponse?.ok) throw Error("unavailable");
        const sources = await sourceResponse.json() as Doc<SourcePack>;
        const sandbox = await sandboxResponse.json() as Doc<Sandbox>;
        const current = snapshot(sources, sandbox);
        const stock = selection.sku ? current.stock.find((row) => row.code === selection.sku) : undefined;
        workspace = { sourceRevision: current.sourceRevision, updatedAt: sources.updatedAt,
          sandboxHour: current.hour, observationOnly: true, orderRowsAtSandboxHour: current.orders,
          campaignRows: current.campaigns, selectedStock: stock ? { code: stock.code.slice(0, 200),
            name: stock.name.slice(0, 200), stock: stock.stock, asOf: stock.asOf.slice(0, 100) } : null,
          sources: current.sources.slice(0, 30).map((source) => ({ kind: source.kind,
            name: source.name.slice(0, 200), hash: source.hash.slice(0, 100), issueCount: source.issues.length,
            issues: source.issues.slice(0, 8).map((issue) => issue.slice(0, 200)) })),
        };
      } catch { notices.push("Chưa đọc được dữ liệu Data Studio hiện tại; không suy đoán số liệu từ dữ liệu cũ."); }
    })(),
    (async () => {
      if (!selection.sku) return;
      if (!api) { notices.push("Chưa kết nối backend phân tích. Chỉ có thể trao đổi mục tiêu, chưa có số liệu mô phỏng thật."); return; }
      try {
        const context = await loadContext(selection.sku, env, signal);
        const truncated = context.campaigns.length > 12 || context.campaigns.some((row) => row.skus.length > 20);
        skuContext = { ...context,
          sku: { ...context.sku, name: context.sku.name.slice(0, 200), variant: context.sku.variant.slice(0, 200) },
          opening: context.opening.slice(0, 800),
          source: context.source ? { id: context.source.id.slice(0, 200), kind: context.source.kind.slice(0, 100), label: context.source.label.slice(0, 200), data_as_of: context.source.data_as_of?.slice(0, 100) ?? null } : null,
          campaigns: context.campaigns.slice(0, 12).map((row) => ({ id: row.id.slice(0, 200), name: row.name.slice(0, 200), skus: row.skus.slice(0, 20).map((sku) => sku.slice(0, 200)) })),
          canPlan: context.canPlan && !truncated,
          notices: [...context.notices.slice(0, 10).map((notice) => notice.slice(0, 500)), ...(truncated ? ["Liên kết chiến dịch vượt giới hạn ngữ cảnh. Cần thu hẹp dữ liệu trước khi lập phương án; danh sách này chưa đầy đủ."] : [])],
        };
      }
      catch { notices.push("Chưa đọc được dữ liệu và liên kết chiến dịch của SKU đã chọn; không dùng sản phẩm mẫu thay thế."); }
    })(),
    (async () => {
      if (!selection.sku || !api) return;
      try {
        const history = historySchema.parse((await api(`skus/${encodeURIComponent(selection.sku)}/panel?grain=day`)).data);
        if (history.sku !== selection.sku) throw Error("history mismatch");
        salesHistory = history;
      } catch { notices.push("Chưa đọc được lịch sử bán 7 ngày của SKU; không suy diễn xu hướng từ riêng số bán hôm nay."); }
    })(),
    (async () => {
      if (!selection.campaignId || !api) return;
      try {
        const data = campaignSchema.parse((await api(`campaigns/${encodeURIComponent(selection.campaignId)}`)).data);
        if (data.campaign_id !== selection.campaignId || (selection.sku && !data.skus.includes(selection.sku)))
          throw Error("scope mismatch");
        campaign = { ...data, skus: data.skus.slice(0, 20), context_basis: "selected" };
        if (data.skus.length > 20) notices.push("Chiến dịch có hơn 20 SKU; ngữ cảnh chỉ hiển thị một phần danh sách sản phẩm, số tiền vẫn ở phạm vi toàn chiến dịch.");
      } catch { notices.push("Chưa xác minh được chiến dịch thuộc SKU đang chọn; không dùng ngân sách hay mục tiêu của chiến dịch khác."); }
    })(),
  ]);
  // Reading the sole verified membership is not permission to save a goal or run it.
  const selectedSku = skuContext as AssistantContext | null;
  if (!selection.campaignId && api && selectedSku?.canPlan && selectedSku.campaigns.length === 1) {
    try {
      const id = selectedSku.campaigns[0]!.id;
      const data = campaignSchema.parse((await api(`campaigns/${encodeURIComponent(id)}`)).data);
      if (data.campaign_id !== id || !selection.sku || !data.skus.includes(selection.sku)) throw Error("scope mismatch");
      campaign = { ...data, skus: data.skus.slice(0, 20), context_basis: "sole-sku-membership-read-only" };
      if (data.skus.length > 20) notices.push("Chiến dịch có hơn 20 SKU; số tiền vẫn ở phạm vi toàn chiến dịch.");
    } catch { notices.push("Chưa đọc được ngân sách của chiến dịch liên kết. Không tự đặt một mức ngân sách thay thế."); }
  }
  const selectedCampaign = campaign as z.infer<typeof campaignSchema> | null;
  if (api && selectedCampaign?.goal) {
    try {
      const detail = activeGoalSchema.parse((await api(`goals/${encodeURIComponent(selectedCampaign.goal.goal_id)}`)).data);
      if (detail.scope_id !== selectedCampaign.campaign_id || detail.goal_id !== selectedCampaign.goal.goal_id)
        throw Error("active goal scope mismatch");
      campaign = { ...selectedCampaign, goal: goalBriefSchema.parse({ ...detail, detail_verified: true }) };
    } catch {
      campaign = { ...selectedCampaign, goal: null };
      notices.push("Mục tiêu đang áp dụng chưa được xác minh hoặc vừa thay đổi. Cần tải lại kế hoạch trước khi đối chiếu hay áp dụng; không dùng ràng buộc cũ làm mục tiêu hiện tại.");
    }
  }
  if (selection.scenarioId && api) {
    try {
      const data = scenarioSchema.parse((await api(`scenarios/${encodeURIComponent(selection.scenarioId)}`)).data);
      const trace = data.calculation_trace;
      if (data.scenario_id !== selection.scenarioId || !trace || data.blocked ||
        trace.input_hash !== data.input_hash || trace.campaign_id !== data.request.campaign_id ||
        (selection.campaignId && trace.campaign_id !== selection.campaignId) ||
        (selection.sku && trace.focus_sku !== selection.sku)) throw Error("trace mismatch");
      scenario = { id: data.scenario_id, createdAt: data.created_at ?? null,
        trace: { ...trace, actions: trace.actions.slice(0, 8),
          skus: trace.skus?.filter((row) => !selection.sku || row.sku_id === selection.sku).slice(0, 8).map((row) => ({ ...row,
            price_hourly: row.price_hourly.slice(0, 24), contribution_per_unit_hourly: row.contribution_per_unit_hourly.slice(0, 24),
            forecast_mean_hourly: row.forecast_mean_hourly.slice(0, 24), restock: row.restock.slice(0, 8),
            economics_by_day: row.economics_by_day?.slice(0, 8) })),
          sources: { ...trace.sources, batches: trace.sources.batches.slice(0, 10),
            parameters: trace.sources.parameters?.slice(0, 20),
            forecasts: trace.sources.forecasts.filter((row) => !selection.sku || row.sku_id === selection.sku).slice(0, 10) } },
        truncated: trace.actions.length > 8 || trace.sources.batches.length > 10 || trace.sources.forecasts.length > 10 ||
          (trace.sources.parameters?.length ?? 0) > 20 || (trace.skus?.length ?? 0) > 8 ||
          !!trace.skus?.some((row) => row.price_hourly.length > 24 || row.forecast_mean_hourly.length > 24 ||
            row.contribution_per_unit_hourly.length > 24 || row.restock.length > 8 || (row.economics_by_day?.length ?? 0) > 8),
      };
      if (!trace.skus?.length) notices.push("Dấu vết này chưa có giá vốn/giá bán theo SKU; chỉ giải thích được công thức tổng hợp đã lưu, không tự dựng chi phí thành phần.");
    } catch { notices.push("Kịch bản chưa có dấu vết tính toán hợp lệ cho ngữ cảnh này. Không thể giải thích số bằng dữ liệu hiện tại thay cho dữ liệu lúc chạy."); }
  }
  return { selection, skuContext, salesHistory, campaign, scenario, workspace, notices };
}
