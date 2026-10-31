import { z } from "zod";
import { assistantSkuSchema, assistantTurnSchema, type AssistantContext, type AssistantSkuList, type AssistantTurnResponse } from "../src/planner/assistant-contract";
import { goalInterpretResponseSchema, type GoalDraft } from "../src/planner/goal-contract";
import { interpretGoal, ruleGoalDraft } from "./goal-interpret";
import { BackendFailure, plannerBackend, type PlannerEnv } from "./sale-planner";
import type { PMEnv } from "./pm";

const numeric = z.number().finite().nullable().optional();
const skuData = z.object({
  sku: z.string(), name: z.string(), variant: z.string().default(""), stock_available: numeric,
  sold_today_units: numeric, price_effective_vnd: numeric, contribution_per_unit_vnd: numeric,
  demand_group: z.string().nullable().optional(), as_of: z.string().nullable().optional(),
  source: z.object({ id: z.string(), kind: z.string(), label: z.string(), data_as_of: z.string().nullable().optional(),
    tool: z.string().nullable().optional(), href: z.string().nullable().optional() }).nullable().optional(),
});
const campaignData = z.array(z.object({ campaign_id: z.string(), name: z.string(), skus: z.array(z.string()) }));
const requiredCapabilities = ["max_spend_vnd", "max_loss_vnd", "sell_target_prob", "min_marginal_return", "focus_sku", "calculation_trace"];
const noStore = { "Cache-Control": "no-store" };
function failed(error: unknown): Response {
  if (error instanceof BackendFailure) return Response.json({ error: { code: error.code, message: error.message } }, { status: error.status, headers: noStore });
  if (error instanceof z.ZodError || error instanceof SyntaxError)
    return Response.json({ error: { code: "INVALID_ASSISTANT_DATA", message: "Thông tin trợ lý hoặc dữ liệu backend chưa hợp lệ. Hãy kiểm tra và thử lại." } }, { status: 422, headers: noStore });
  return Response.json({ error: { code: "ASSISTANT_UNAVAILABLE", message: "Chưa đọc được dữ liệu cho trợ lý SKU. Hãy thử lại sau." } }, { status: 502, headers: noStore });
}
function connection(env: PlannerEnv, signal?: AbortSignal) {
  const api = plannerBackend(env, signal);
  if (!api) throw new BackendFailure(503, "BACKEND_UNCONFIGURED", "Chưa kết nối backend. Trợ lý SKU cần dữ liệu thật đã gửi từ Data Studio; không sử dụng sản phẩm mẫu thay thế.");
  return api;
}
function asOf(result: Record<string, unknown>): string | null {
  const value = (result.meta as { data_as_of?: unknown } | undefined)?.data_as_of;
  return typeof value === "string" ? value : null;
}
export async function assistantSkus(request: Request, env: PlannerEnv): Promise<Response> {
  try {
    const url = new URL(request.url);
    const query = z.string().max(200).parse(url.searchParams.get("q") ?? "").trim();
    const cursor = z.string().max(500).parse(url.searchParams.get("cursor") ?? "");
    const api = connection(env);
    const params = new URLSearchParams({ limit: "50", sort: "sku", q: query });
    if (cursor) params.set("cursor", cursor);
    const result = await api(`skus?${params}`);
    const rows = z.array(skuData).parse(result.data);
    const response: AssistantSkuList = {
      items: rows.map((row) => ({ sku: row.sku, name: row.name, variant: row.variant, stockAvailable: row.stock_available ?? null })),
      nextCursor: typeof result.next_cursor === "string" ? result.next_cursor : null, dataAsOf: asOf(result),
    };
    return Response.json(response, { headers: noStore });
  } catch (error) { return failed(error); }
}
export async function loadContext(sku: string, env: PlannerEnv, signal?: AbortSignal): Promise<AssistantContext> {
  const api = connection(env, signal);
  const result = await api(`skus/${encodeURIComponent(sku)}`);
  const row = skuData.parse(result.data);
  if (row.sku !== sku) throw new BackendFailure(502, "SKU_MISMATCH", "Backend trả về sản phẩm khác SKU đã chọn. Hãy tải lại danh sách.");
  const campaigns: AssistantContext["campaigns"] = [];
  const notices: string[] = [];
  let cursor: string | null = null;
  let complete = true;
  // SKU detail exposes only its primary campaign. Traverse the campaign list to find all memberships.
  for (let page = 0; page < 20; page++) {
    const list = await api(`campaigns?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
    for (const campaign of campaignData.parse(list.data)) if (campaign.skus.includes(sku))
      campaigns.push({ id: campaign.campaign_id, name: campaign.name, skus: campaign.skus });
    cursor = typeof list.next_cursor === "string" ? list.next_cursor : null;
    if (!cursor) break;
    if (page === 19) { complete = false; notices.push("Danh sách vượt 2.000 chiến dịch nên chưa xác nhận được toàn bộ liên kết của SKU. Chưa cho phép lập phương án từ danh sách chưa đầy đủ."); }
  }
  let canPlan = false;
  try {
    const [capability, me] = await Promise.all([api("goal-planner/capabilities"), api("me")]);
    const caps = capability.data as { version?: unknown; constraints?: string[] } | undefined;
    const user = me.data as { can_edit_goals?: boolean; can_run_scenarios?: boolean } | undefined;
    const supported = caps?.version === 1 && requiredCapabilities.every((key) => caps.constraints?.includes(key));
    canPlan = !!supported && user?.can_edit_goals === true && user.can_run_scenarios === true && campaigns.length > 0 && complete;
    if (!supported) notices.push("Backend cần cập nhật mục tiêu theo SKU và dấu vết tính toán trước khi mô phỏng.");
    if (!user?.can_edit_goals || !user.can_run_scenarios) notices.push("Tài khoản backend cần quyền lead để lưu mục tiêu và chạy mô phỏng.");
  } catch { notices.push("Chưa xác nhận được khả năng mô phỏng và quyền của backend. Bạn có thể trao đổi mục tiêu nhưng chưa chạy được."); }
  if (!campaigns.length) notices.push("SKU chưa thuộc chiến dịch nào. Cần liên kết dữ liệu chiến dịch trước khi chạy phương án quảng cáo.");
  if (campaigns.length > 1) notices.push("SKU thuộc nhiều chiến dịch. Bạn cần chọn một chiến dịch cụ thể; hệ thống không tự chọn hoặc tối ưu chung các chiến dịch.");
  if (campaigns.some((campaign) => campaign.skus.length > 1)) notices.push("Có chiến dịch chứa nhiều SKU. Thay đổi quảng cáo tác động toàn chiến dịch; ngân sách, mức lỗ và chỉ số J không phải số riêng của sản phẩm này.");
  notices.push("Mục tiêu số lượng/xả hàng dùng số bán của SKU đã chọn. Lợi nhuận, hiệu quả chi, ngân sách và giới hạn lỗ vẫn ở phạm vi toàn chiến dịch.");
  return {
    sku: { sku: row.sku, name: row.name, variant: row.variant, stockAvailable: row.stock_available ?? null,
      soldTodayUnits: row.sold_today_units ?? null, priceEffectiveVnd: row.price_effective_vnd ?? null,
      contributionPerUnitVnd: row.contribution_per_unit_vnd ?? null, demandGroup: row.demand_group ?? null },
    campaigns, dataAsOf: row.as_of ?? asOf(result), source: row.source ?? null, canPlan, notices,
    opening: `Với ${row.name} (${row.sku}), buổi sale này bạn muốn ưu tiên bán đủ số lượng, xả hàng, lợi nhuận hay hiệu quả chi? Bạn có thể mô tả mục tiêu bằng lời.`,
  };
}
export async function assistantContext(request: Request, env: PlannerEnv): Promise<Response> {
  try {
    const sku = assistantSkuSchema.parse(new URL(request.url).searchParams.get("sku"));
    return Response.json(await loadContext(sku, env), { headers: noStore });
  } catch (error) { return failed(error); }
}
function nextQuestion(draft: GoalDraft): string {
  if (!draft.kind) return "Bạn muốn ưu tiên bán đủ số lượng, xả hàng, lợi nhuận hay hiệu quả chi? Hãy chọn một mục tiêu chính.";
  if (draft.kind === "sell_target" && draft.targetUnits === null) return "Bạn muốn bán ít nhất bao nhiêu sản phẩm của SKU này? Ví dụ: bán ít nhất 100 sản phẩm.";
  if (draft.maxSpendVnd === null) return "Bạn muốn giới hạn tổng ngân sách quảng cáo cho cả buổi sale là bao nhiêu? Ví dụ: ngân sách 2 triệu đồng. Có thể để trống trong bản nháp nếu không thêm giới hạn.";
  if (!draft.start || !draft.deadline) return "Buổi sale bắt đầu và kết thúc khi nào? Bạn có thể điền ngày, giờ Việt Nam ở phần kiểm tra mục tiêu bên dưới; hệ thống không tự đoán lịch sale.";
  if (draft.unresolved.length) return "Có yêu cầu chưa rõ hoặc chưa được mô hình hỗ trợ. Hãy kiểm tra các mục cần làm rõ trong bản nháp và chỉnh thông số trước khi xác nhận.";
  return "Đã có bản nháp mục tiêu. Hãy kiểm tra chiến dịch, thông số và giới hạn ngân sách/lỗ ở bên dưới, rồi xác nhận để lưu mục tiêu và chạy mô phỏng.";
}
const normalized = (value: string) => value.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d");
/** Infer a short answer's slot only from preceding user requirements, never assistant text. */
function contextualize(turns: { content: string }[]): { text: string; correction: GoalDraft | null; unresolved: string[] } {
  const lines: string[] = [];
  const unresolved: string[] = [];
  let correction: GoalDraft | null = null;
  for (const turn of turns) {
    const original = turn.content, value = normalized(original);
    const previous = ruleGoalDraft(lines.join("\n"));
    let extracted = original;
    if (/^(?:toi muon |muon |uu tien )?(?:ban du so luong|ban theo so luong|ban du|so luong)(?: san pham nay)?(?: trong buoi sale)?[.!?\s]*$/.test(value))
      extracted = `${original}\nMục tiêu sản lượng`;
    else if (/^(?:toi muon |muon |uu tien )?(?:loi nhuan|toi uu loi nhuan)[.!?\s]*$/.test(value))
      extracted = `${original}\nTối ưu lợi nhuận`;
    else if (/^(?:toi muon |muon |uu tien )?(?:hieu qua chi|hieu qua quang cao)[.!?\s]*$/.test(value))
      extracted = `${original}\nTối ưu hiệu quả quảng cáo`;
    else if (previous.kind === "sell_target" && previous.targetUnits === null && /^\d+\s*(?:san pham|sp|cai)?[.!?\s]*$/.test(value))
      extracted = `Bán ${value.match(/^\d+/)![0]} sản phẩm`;
    else if (previous.kind && (previous.kind !== "sell_target" || previous.targetUnits !== null) && previous.maxSpendVnd === null && /^\d+(?:[.,]\d+)?\s*(?:trieu|tr|nghin|ngan|k|dong|vnd)(?:\s+dong)?[.!?\s]*$/.test(value))
      extracted = `Ngân sách ${original}`;
    const revisedBudget = value.match(/^(?:doi|sua|thay|cap nhat)\s+(?:lai\s+)?ngan sach(?:\s+quang cao)?\s+(?:thanh|la|sang)\s+(\d+(?:[.,]\d+)?\s*(?:trieu|tr|nghin|ngan|k|dong|vnd)(?:\s+dong)?)[.!?\s]*$/);
    if (revisedBudget) correction = ruleGoalDraft(`Ngân sách ${revisedBudget[1]}`);
    else if (ruleGoalDraft(extracted).maxSpendVnd !== null) correction = null;
    if (extracted === original && /^\d+(?:[.,]\d+)?\s*(?:trieu|tr|nghin|ngan|k|dong|vnd|san pham|sp|cai)?[.!?\s]*$/.test(value))
      unresolved.push(`Chưa xác định được câu trả lời “${original}” sửa thông số nào. Hãy ghi rõ tên thông số hoặc chỉnh bản nháp.`);
    lines.push(extracted);
  }
  return { text: lines.join("\n"), correction, unresolved };
}
export async function assistantTurn(request: Request, env: PlannerEnv & PMEnv): Promise<Response> {
  try {
    const raw = await request.text();
    if (raw.length > 70000) throw new BackendFailure(413, "TOO_LARGE", "Hội thoại quá dài. Hãy bắt đầu lại bằng mục tiêu đã tóm tắt.");
    const input = assistantTurnSchema.parse(JSON.parse(raw));
    if (input.messages.at(-1)?.role !== "user") throw new BackendFailure(422, "LAST_TURN_REQUIRED", "Cần một tin nhắn mới từ người dùng.");
    // Assistant text comes from an untrusted browser. Only user requirements enter interpretation.
    const userTurns = input.messages.filter((message) => message.role === "user");
    const originalText = userTurns.map((message) => message.content).join("\n");
    const { text, correction, unresolved } = contextualize(userTurns);
    if (text.length > 4000 || originalText.length > 4000) throw new BackendFailure(422, "CONVERSATION_TOO_LONG", "Mục tiêu trong hội thoại vượt 4.000 ký tự. Hãy bắt đầu lại với bản tóm tắt để không mất ràng buộc cũ.");
    const context = await loadContext(input.sku, env);
    const interpreted = await interpretGoal(new Request("https://internal/interpret", { method: "POST", body: JSON.stringify({ text }) }), env);
    if (!interpreted.ok) throw new BackendFailure(interpreted.status, "INTERPRET_FAILED", "Chưa diễn giải được mục tiêu. Bạn có thể thử lại hoặc nhập thông số thủ công.");
    const interpretation = goalInterpretResponseSchema.parse(await interpreted.json());
    // Preserve the exact user conversation in the audit, despite explicit slot expansion above.
    interpretation.draft.text = originalText;
    interpretation.draft.unresolved = [...new Set([...interpretation.draft.unresolved, ...unresolved])];
    if (correction) {
      interpretation.draft.maxSpendVnd = correction.maxSpendVnd;
      if (correction.maxSpendVnd === null) interpretation.draft.unresolved.push("Lần sửa ngân sách có số tiền chưa rõ; đã bỏ giá trị cũ. Hãy điền lại tổng ngân sách bằng số VND.");
      else interpretation.notices.push("Đã áp dụng lần sửa ngân sách được nêu rõ trong hội thoại. Hãy kiểm tra giá trị cuối trước khi xác nhận.");
    }
    // Never silently overwrite an earlier condition when a later turn contains a correction.
    if (userTurns.length > 1) interpretation.draft.unresolved = [...new Set([...interpretation.draft.unresolved,
      "Hội thoại có nhiều lượt. Hãy đối chiếu mọi yêu cầu và các thay đổi với thông số cuối; nếu có nhiều giá trị, chọn lại giá trị đúng trước khi xác nhận.",
    ])];
    const scope = interpretation.draft.kind === "sell_target" || interpretation.draft.kind === "clear_stock"
      ? "Số lượng mục tiêu áp dụng cho SKU đang chọn; chi quảng cáo và chỉ số kinh tế J vẫn tính cho toàn chiến dịch."
      : "Mục tiêu lợi nhuận/hiệu quả chi được đánh giá cho toàn chiến dịch, không phân bổ chi quảng cáo riêng cho SKU.";
    const reply = `${nextQuestion(interpretation.draft)}\n\n${scope}\n\nĐây mới là bản nháp; chưa lưu mục tiêu, chạy mô phỏng hay thay đổi quảng cáo.`;
    return Response.json({ reply, interpretation, context } satisfies AssistantTurnResponse, { headers: noStore });
  } catch (error) { return failed(error); }
}
