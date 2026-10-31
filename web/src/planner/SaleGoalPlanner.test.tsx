import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SaleGoalPlanner } from "./SaleGoalPlanner";
import type { GoalDraft } from "./goal-contract";
import type { PlannerContext, PlannerRunResponse } from "./planner-api";

const draft: GoalDraft = {
  text: "Tối ưu lợi nhuận", kind: "profit", targetUnits: null,
  start: "2026-11-11T08:00:00+07:00", deadline: "2026-11-11T20:00:00+07:00",
  maxSpendVnd: 2_000_000, maxLossVnd: null, sellTargetProbability: null,
  minMarginalReturn: null, unresolved: [],
};
const context: PlannerContext = {
  backendConfigured: true, canCreateGoals: true, referenceTime: "2026-11-11T00:00:00Z",
  dataAsOf: "2026-11-11T00:00:00Z", notice: null,
  campaigns: [{ id: "campaign-real", name: "Chiến dịch thực" }, { id: "campaign-other", name: "Chiến dịch khác" }],
};
const dist = { mean: 12345, p10: 1000, p50: 10000, p90: 30000 };
const result: PlannerRunResponse = { goalId: "goal-saved", scenario: { data: {
  scenario_id: "scenario-real", cache_hit: false, blocked: false,
  request: { campaign_id: "campaign-real", start: draft.start!, horizon_hours: 12, goal_id: "goal-saved", actions: [], param_overrides: {}, samples: 1000, seed: 123 },
  recommendation: null,
  results: [{ action: { kind: "continue" }, feasible: true, violations: [], goal_eligible: true, objective: dist, contribution: dist, spend: { ...dist, mean: 2345 }, units_sold: { ...dist, mean: 45 }, leftover_units: dist, leftover_value: dist, stockout_hour_p50: null, per_sku: [], key: "continue", label: "Continue", p_target: 0.87 }],
} } };
const fetchMock = vi.fn<typeof fetch>();
let currentContext: PlannerContext;
let currentDraft: GoalDraft;
let runAttempts: Record<string, unknown>[];
let runResponse: (attempt: number) => Response;
function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
beforeEach(() => {
  currentContext = structuredClone(context); currentDraft = structuredClone(draft); runAttempts = [];
  runResponse = () => json(result);
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url, options) => {
    if (String(url).endsWith("/context")) return json(currentContext);
    if (String(url).endsWith("/interpret")) return json({ draft: currentDraft, method: "rules", notices: [] });
    if (String(url).endsWith("/run")) {
      runAttempts.push(JSON.parse(options!.body as string));
      return runResponse(runAttempts.length);
    }
    throw Error(`Unexpected request: ${url}`);
  });
});
afterEach(() => vi.unstubAllGlobals());
function mount() { render(<MemoryRouter><SaleGoalPlanner /></MemoryRouter>); }
async function review() {
  mount();
  await waitFor(() => expect(screen.getByRole("button", { name: "Kiểm tra lại" })).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Mô tả mục tiêu"), { target: { value: draft.text } });
  fireEvent.click(screen.getByRole("button", { name: "Phân tích mục tiêu" }));
  await screen.findByLabelText("Mục tiêu chính");
}
function selectCampaign(id = "campaign-real") { fireEvent.change(screen.getByLabelText("Chiến dịch"), { target: { value: id } }); }
const confirm = () => screen.getByRole("checkbox", { name: /Tôi xác nhận các thông số trên/ });
const run = () => screen.getByRole("button", { name: /Lưu mục tiêu và chạy mô phỏng/ });

describe("sale goal review and real simulation", () => {
  it("allows interpretation without a backend and never substitutes fixture results", async () => {
    currentContext = { ...context, backendConfigured: false, canCreateGoals: false, campaigns: [] };
    await review();
    expect(screen.getByText("Chưa kết nối backend tính toán")).toBeInTheDocument();
    fireEvent.click(confirm());
    expect(run()).toBeDisabled();
    expect(runAttempts).toHaveLength(0);
    expect(screen.queryByText(/KẾT QUẢ TỪ BACKEND/)).not.toBeInTheDocument();
  });
  it("requires explicit confirmation and invalidates it after amount or campaign edits", async () => {
    await review(); selectCampaign();
    expect(run()).toBeDisabled(); expect(runAttempts).toHaveLength(0);
    fireEvent.click(confirm()); expect(run()).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Ngân sách quảng cáo tối đa (₫)"), { target: { value: "0" } });
    expect(confirm()).not.toBeChecked(); expect(run()).toBeDisabled();
    fireEvent.click(confirm()); selectCampaign("campaign-other");
    expect(confirm()).not.toBeChecked();
    fireEvent.click(confirm()); fireEvent.click(run());
    await screen.findByText("Phương án cho Chiến dịch khác");
    expect(runAttempts[0]).toMatchObject({ campaignId: "campaign-other", confirmed: true, draft: { text: draft.text, maxSpendVnd: 0 } });
  });
  it("requires review of unsupported requirements and clears acknowledgement on edits", async () => {
    currentDraft.unresolved = ["Tự tạo voucher chưa được hỗ trợ"];
    await review(); selectCampaign();
    expect(confirm()).toBeDisabled();
    expect(screen.getByText("Tự tạo voucher chưa được hỗ trợ")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /Tôi đã kiểm tra và đồng ý/ }));
    fireEvent.click(confirm()); expect(run()).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Mức lỗ kỳ vọng tối đa (₫)"), { target: { value: "0" } });
    expect(confirm()).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /Tôi đã kiểm tra và đồng ý/ })).not.toBeChecked();
    expect(runAttempts).toHaveLength(0);
  });
  it("retries a failed simulation using the saved goal and clears it after edits", async () => {
    runResponse = (attempt) => attempt === 1 ? json({ goalId: "goal-saved", stage: "simulation", error: { code: "BLOCKING_DATA_ISSUE", message: "Thiếu dữ liệu tồn kho", details: { missing: ["inventory"] } } }, 422) : json(result);
    await review(); selectCampaign(); fireEvent.click(confirm()); fireEvent.click(run());
    await screen.findByText("Thiếu dữ liệu tồn kho");
    expect(runAttempts).toHaveLength(1);
    expect(runAttempts[0]).not.toHaveProperty("goalId");
    fireEvent.click(screen.getByRole("button", { name: "Thử lại mô phỏng" }));
    await screen.findByText("Phương án cho Chiến dịch thực");
    expect(runAttempts[1]).toMatchObject({ goalId: "goal-saved" });
    fireEvent.change(screen.getByLabelText("Ngân sách quảng cáo tối đa (₫)"), { target: { value: "500000" } });
    expect(screen.queryByText("Phương án cho Chiến dịch thực")).not.toBeInTheDocument();
    fireEvent.click(confirm()); fireEvent.click(run());
    await waitFor(() => expect(runAttempts).toHaveLength(3));
    expect(runAttempts[2]).not.toHaveProperty("goalId");
  });
  it("renders actual returned values with quantiles and calculation provenance", async () => {
    await review(); selectCampaign(); fireEvent.click(confirm()); fireEvent.click(run());
    await screen.findByText("Phương án cho Chiến dịch thực");
    expect(screen.getAllByText("12.345 ₫")).toHaveLength(3);
    expect(screen.getByText("2.345 ₫")).toBeInTheDocument();
    expect(screen.getByText("87%")).toBeInTheDocument();
    expect(screen.getByText("scenario-real")).toBeInTheDocument();
    expect(screen.getByText("results[0].objective")).toBeInTheDocument();
    expect(screen.getAllByText(/P10: 1.000 ₫ · P50: 10.000 ₫ · P90: 30.000 ₫/).length).toBeGreaterThan(0);
    expect(screen.getByText("Backend chưa trả về đề xuất")).toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([url]) => String(url).startsWith("/api/sale-planner/"))).toBe(true);
  });
  it("keeps unsupported goal fields visible and blocks confirmation until corrected", async () => {
    currentDraft.targetUnits = 100;
    currentDraft.start = "2026-11-11T08:30:00+07:00";
    await review(); selectCampaign();
    expect(screen.getByLabelText("Số sản phẩm mục tiêu")).toHaveValue(100);
    expect(screen.getByText(/Số lượng và xác suất đạt chỉ áp dụng/)).toBeInTheDocument();
    expect(screen.getByText("Chọn giờ tròn, thời lượng từ 1 đến 72 giờ.")).toBeInTheDocument();
    expect(confirm()).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Mục tiêu chính"), { target: { value: "sell_target" } });
    fireEvent.change(screen.getByLabelText("Bắt đầu (giờ Việt Nam)"), { target: { value: "2026-11-11T08:00" } });
    expect(confirm()).toBeEnabled();
  });
  it("disables the run when backend capability or permission is missing", async () => {
    currentContext.canCreateGoals = false;
    currentContext.notice = "Backend cần nâng cấp";
    await review(); selectCampaign(); fireEvent.click(confirm());
    expect(screen.getByText("Backend cần nâng cấp")).toBeInTheDocument();
    expect(run()).toBeDisabled(); expect(runAttempts).toHaveLength(0);
  });
  it("locks the draft during interpretation and provides manual input on failure", async () => {
    let finish!: (response: Response) => void;
    fetchMock.mockImplementation(async (url) => String(url).endsWith("/context") ? json(context) : new Promise((resolve) => { finish = resolve; }));
    mount(); await screen.findByText("Chiến dịch từ backend");
    fireEvent.change(screen.getByLabelText("Mô tả mục tiêu"), { target: { value: draft.text } });
    fireEvent.click(screen.getByRole("button", { name: "Phân tích mục tiêu" }));
    expect(screen.getByLabelText("Mô tả mục tiêu")).toBeDisabled();
    await act(async () => finish(json({ error: { message: "Không thể diễn giải" } }, 503)));
    expect(await screen.findByRole("alert")).toHaveTextContent("Không thể diễn giải");
    fireEvent.click(screen.getByRole("button", { name: "Nhập thông số thủ công" }));
    expect(screen.getByLabelText("Mục tiêu chính")).toHaveValue("");
    expect(screen.getByLabelText("Mô tả mục tiêu")).toHaveValue(draft.text);
    expect(runAttempts).toHaveLength(0);
  });
});
