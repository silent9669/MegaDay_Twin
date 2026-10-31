import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SaleGoalPlanner } from "./SaleGoalPlanner";
import type { GoalDraft } from "./goal-contract";
import type { PlannerContext, PlannerRunResponse, PlannerGoal } from "./planner-api";

const draft: GoalDraft = {
  text: "Tối ưu lợi nhuận", kind: "profit", targetUnits: null,
  start: "2026-11-11T08:00:00+07:00", deadline: "2026-11-11T20:00:00+07:00",
  maxSpendVnd: 2_000_000, maxLossVnd: null, sellTargetProbability: null,
  minMarginalReturn: null, unresolved: [],
};
const context: PlannerContext = {
  backendConfigured: true, canCreateGoals: true, canPreview: true, canApply: true, referenceTime: "2026-11-11T00:00:00Z",
  dataAsOf: "2026-11-11T00:00:00Z", notice: null,
  campaigns: [{ id: "campaign-real", name: "Chiến dịch thực" }, { id: "campaign-other", name: "Chiến dịch khác" }],
};
const dist = { mean: 12345, p10: 1000, p50: 10000, p90: 30000 };
const goal: PlannerGoal = { goal_id: "goal-preview", scope: "campaign", scope_id: "campaign-real", kind: "profit", target_units: null, deadline: draft.deadline!, params: { max_spend_vnd: 2000000 }, active: false, version: 1 };
const result: PlannerRunResponse = { previewId: "preview-1", goalId: "goal-preview", effectiveGoal: goal, baseGoal: null, changes: [{ field: "max_spend_vnd", before: null, after: 2000000 }], notices: [], canApply: true, appliedGoalId: null, scenario: { data: {
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
let applyAttempts: Record<string, unknown>[];
let applyResponse: () => Response;
let activeGoal: PlannerGoal | null;
function json(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
beforeEach(() => {
  currentContext = structuredClone(context); currentDraft = structuredClone(draft); runAttempts = []; applyAttempts = []; activeGoal = null;
  runResponse = () => json(result);
  applyResponse = () => json({ previewId: result.previewId, goal: { ...goal, active: true }, alreadyApplied: false });
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url, options) => {
    if (String(url).endsWith("/context")) return json(currentContext);
    if (String(url).includes("/active?")) return json({ campaignId: new URL(String(url), "https://test").searchParams.get("campaignId"), activeGoal, baseGoal: activeGoal ? { goal_id: activeGoal.goal_id, version: activeGoal.version } : null });
    if (String(url).endsWith("/interpret")) return json({ draft: currentDraft, method: "rules", notices: [] });
    if (String(url).endsWith("/run")) {
      runAttempts.push(JSON.parse(options!.body as string));
      return runResponse(runAttempts.length);
    }
    if (String(url).endsWith("/apply")) { applyAttempts.push(JSON.parse(options!.body as string)); return applyResponse(); }
    throw Error(`Unexpected request: ${url}`);
  });
});
afterEach(() => vi.unstubAllGlobals());
function mount() { render(<MemoryRouter><SaleGoalPlanner /></MemoryRouter>); }
async function review() {
  mount();
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Goal description"), { target: { value: draft.text } });
  fireEvent.click(screen.getByRole("button", { name: "Interpret goal" }));
  await screen.findByLabelText("Primary goal");
}
async function selectCampaign(id = "campaign-real") { fireEvent.change(screen.getByLabelText("Campaign"), { target: { value: id } }); await waitFor(() => expect(screen.queryByText("Checking the campaign goal…")).not.toBeInTheDocument()); }
const confirm = () => screen.getByRole("checkbox", { name: /I confirm these parameters for a preview/ });
const run = () => screen.getByRole("button", { name: "Preview simulation" });

describe("sale goal review and real simulation", () => {
  it("limits product goals to linked campaigns and selects the only match", async () => {
    currentContext.campaigns = [
      { id: "campaign-real", name: "Linked campaign", skus: ["NOICHIEN2L"] },
      { id: "campaign-other", name: "Unrelated campaign", skus: ["OTHER"] },
    ];
    render(<MemoryRouter><SaleGoalPlanner focusSku="NOICHIEN2L" /></MemoryRouter>);
    await waitFor(() => expect(screen.getByLabelText("Campaign")).toHaveValue("campaign-real"));
    expect(screen.queryByRole("option", { name: "Unrelated campaign" })).not.toBeInTheDocument();
  });
  it("allows interpretation without a backend and never substitutes fixture results", async () => {
    currentContext = { ...context, backendConfigured: false, canCreateGoals: false, campaigns: [] };
    await review();
    expect(screen.getByText("Decision backend not connected")).toBeInTheDocument();
    fireEvent.click(confirm());
    expect(run()).toBeDisabled();
    expect(runAttempts).toHaveLength(0);
    expect(screen.queryByText(/BACKEND RESULTS/)).not.toBeInTheDocument();
  });
  it("requires explicit confirmation and invalidates it after amount or campaign edits", async () => {
    await review(); await selectCampaign();
    expect(run()).toBeDisabled(); expect(runAttempts).toHaveLength(0);
    fireEvent.click(confirm()); expect(run()).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Maximum ad budget (₫)"), { target: { value: "0" } });
    expect(confirm()).not.toBeChecked(); expect(run()).toBeDisabled();
    fireEvent.click(confirm()); await selectCampaign("campaign-other");
    expect(confirm()).not.toBeChecked();
    fireEvent.click(confirm()); fireEvent.click(run());
    await screen.findByText("Options for Chiến dịch khác");
    expect(runAttempts[0]).toMatchObject({ campaignId: "campaign-other", confirmed: true, draft: { text: draft.text, maxSpendVnd: 0 } });
  });
  it("requires review of unsupported requirements and clears acknowledgement on edits", async () => {
    currentDraft.unresolved = ["Tự tạo voucher chưa được hỗ trợ"];
    await review(); await selectCampaign();
    expect(confirm()).toBeDisabled();
    expect(screen.getByText("Tự tạo voucher chưa được hỗ trợ")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /I have reviewed and agree/ }));
    fireEvent.click(confirm()); expect(run()).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Maximum expected loss (₫)"), { target: { value: "0" } });
    expect(confirm()).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /I have reviewed and agree/ })).not.toBeChecked();
    expect(runAttempts).toHaveLength(0);
  });
  it("retries failed previews without saving an active goal and clears results after edits", async () => {
    runResponse = (attempt) => attempt === 1 ? json({ goalId: null, stage: "preview", error: { code: "BLOCKING_DATA_ISSUE", message: "Thiếu dữ liệu tồn kho", details: { missing: ["inventory"] } } }, 422) : json(result);
    await review(); await selectCampaign(); fireEvent.click(confirm()); fireEvent.click(run());
    await screen.findByText("Thiếu dữ liệu tồn kho");
    expect(runAttempts).toHaveLength(1);
    expect(runAttempts[0]).not.toHaveProperty("goalId");
    fireEvent.click(run());
    await screen.findByText("Options for Chiến dịch thực");
    expect(runAttempts[1]).toMatchObject({ expectedGoal: null });
    expect(runAttempts[1]).not.toHaveProperty("goalId");
    expect(applyAttempts).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("Maximum ad budget (₫)"), { target: { value: "500000" } });
    expect(screen.queryByText("Options for Chiến dịch thực")).not.toBeInTheDocument();
    fireEvent.click(confirm()); fireEvent.click(run());
    await waitFor(() => expect(runAttempts).toHaveLength(3));
    expect(runAttempts[2]).not.toHaveProperty("goalId");
  });
  it("renders actual returned values with quantiles and calculation provenance", async () => {
    await review(); await selectCampaign(); fireEvent.click(confirm()); fireEvent.click(run());
    await screen.findByText("Options for Chiến dịch thực");
    expect(screen.getAllByText("12,345 ₫")).toHaveLength(3);
    expect(screen.getByText("2,345 ₫")).toBeInTheDocument();
    expect(screen.getByText("87%")).toBeInTheDocument();
    expect(screen.getByText("scenario-real")).toBeInTheDocument();
    expect(screen.getByText("results[0].objective")).toBeInTheDocument();
    expect(screen.getAllByText(/P10: 1,000 ₫ · P50: 10,000 ₫ · P90: 30,000 ₫/).length).toBeGreaterThan(0);
    expect(screen.getByText("No recommendation returned")).toBeInTheDocument();
    expect(fetchMock.mock.calls.every(([url]) => String(url).startsWith("/api/sale-planner/"))).toBe(true);
  });
  it("shows current versus proposed goals and requires separate confirmation to apply", async () => {
    activeGoal = { ...goal, goal_id: "active-1", active: true, version: 7, params: { max_spend_vnd: 5000000, max_loss_vnd: 0 } };
    runResponse = () => json({ ...result, baseGoal: activeGoal, notices: ["Giữ giới hạn lỗ của kế hoạch chung"], changes: [{ field: "max_spend_vnd", before: 5000000, after: 2000000 }] });
    await review(); await selectCampaign(); fireEvent.click(confirm()); fireEvent.click(run());
    await screen.findByText("Options for Chiến dịch thực");
    expect(runAttempts[0]).toMatchObject({ expectedGoal: { goal_id: "active-1", version: 7 } });
    expect(applyAttempts).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Apply goal" })).toBeDisabled();
    expect(screen.getByText("Giữ giới hạn lỗ của kế hoạch chung")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /I have reviewed the changes/ }));
    fireEvent.click(screen.getByRole("button", { name: "Apply goal" }));
    await screen.findByText(/Applied goal goal-preview/);
    expect(applyAttempts).toEqual([{ previewId: "preview-1", confirmed: true, expectedGoal: { goal_id: "active-1", version: 7 } }]);
  });
  it("discards a stale preview on apply conflict and requires a fresh comparison", async () => {
    applyResponse = () => json({ error: { code: "GOAL_CHANGED", message: "Mục tiêu đã đổi, hãy mô phỏng lại" } }, 409);
    await review(); await selectCampaign(); fireEvent.click(confirm()); fireEvent.click(run());
    await screen.findByText("Options for Chiến dịch thực");
    fireEvent.click(screen.getByRole("checkbox", { name: /I have reviewed the changes/ }));
    fireEvent.click(screen.getByRole("button", { name: "Apply goal" }));
    await screen.findByText("Mục tiêu đã đổi, hãy mô phỏng lại");
    expect(screen.queryByRole("button", { name: "Apply goal" })).not.toBeInTheDocument();
    expect(confirm()).not.toBeChecked();
    expect(run()).toBeDisabled();
    expect(fetchMock.mock.calls.filter(([path]) => String(path).includes("/active?"))).toHaveLength(2);
  });
  it("retains immutable preview on uncertain apply failure so retry cannot create another goal", async () => {
    applyResponse = () => applyAttempts.length === 1 ? json({ error: { code: "BACKEND_UNAVAILABLE", message: "Mất phản hồi" } }, 502) : json({ previewId: result.previewId, goal: { ...goal, active: true }, alreadyApplied: true });
    await review(); await selectCampaign(); fireEvent.click(confirm()); fireEvent.click(run());
    await screen.findByText("Options for Chiến dịch thực");
    fireEvent.click(screen.getByRole("checkbox", { name: /I have reviewed the changes/ }));
    fireEvent.click(screen.getByRole("button", { name: "Apply goal" }));
    await screen.findByText("Mất phản hồi");
    fireEvent.click(screen.getByRole("button", { name: "Apply goal" }));
    await screen.findByText(/Applied goal/);
    expect(applyAttempts).toHaveLength(2);
    expect(applyAttempts[0]).toEqual(applyAttempts[1]);
  });
  it("never offers apply for an ineligible preview and removes it when the draft changes", async () => {
    runResponse = () => json({ ...result, canApply: false });
    await review(); await selectCampaign(); fireEvent.click(confirm()); fireEvent.click(run());
    await screen.findByText("Options for Chiến dịch thực");
    expect(screen.getByRole("checkbox", { name: /I have reviewed the changes/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Apply goal" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Maximum ad budget (₫)"), { target: { value: "500000" } });
    expect(screen.queryByRole("button", { name: "Apply goal" })).not.toBeInTheDocument();
    expect(applyAttempts).toHaveLength(0);
  });
  it("keeps unsupported goal fields visible and blocks confirmation until corrected", async () => {
    currentDraft.targetUnits = 100;
    currentDraft.start = "2026-11-11T08:30:00+07:00";
    await review(); await selectCampaign();
    expect(screen.getByLabelText("Target units")).toHaveValue(100);
    expect(screen.getByText(/Units and target probability only apply/)).toBeInTheDocument();
    expect(screen.getByText("Use whole hours and a duration of 1 to 72 hours.")).toBeInTheDocument();
    expect(confirm()).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Primary goal"), { target: { value: "sell_target" } });
    fireEvent.change(screen.getByLabelText("Start (Vietnam time)"), { target: { value: "2026-11-11T08:00" } });
    expect(confirm()).toBeEnabled();
  });
  it("disables the run when backend capability or permission is missing", async () => {
    currentContext.canCreateGoals = false;
    currentContext.canPreview = false;
    currentContext.notice = "Backend cần nâng cấp";
    await review(); await selectCampaign(); fireEvent.click(confirm());
    expect(screen.getByText("Backend cần nâng cấp")).toBeInTheDocument();
    expect(run()).toBeDisabled(); expect(runAttempts).toHaveLength(0);
  });
  it("locks the draft during interpretation and provides manual input on failure", async () => {
    let finish!: (response: Response) => void;
    fetchMock.mockImplementation(async (url) => String(url).endsWith("/context") ? json(context) : new Promise((resolve) => { finish = resolve; }));
    mount(); await screen.findByText("Connected campaigns");
    fireEvent.change(screen.getByLabelText("Goal description"), { target: { value: draft.text } });
    fireEvent.click(screen.getByRole("button", { name: "Interpret goal" }));
    expect(screen.getByLabelText("Goal description")).toBeDisabled();
    await act(async () => finish(json({ error: { message: "Không thể diễn giải" } }, 503)));
    expect(await screen.findByRole("alert")).toHaveTextContent("Không thể diễn giải");
    fireEvent.click(screen.getByRole("button", { name: "Enter parameters manually" }));
    expect(screen.getByLabelText("Primary goal")).toHaveValue("");
    expect(screen.getByLabelText("Goal description")).toHaveValue(draft.text);
    expect(runAttempts).toHaveLength(0);
  });
});


it("offers goal entry inside HQ and requires confirmation for a supplied Milo preview", async () => {
  const view = render(<MemoryRouter><SaleGoalPlanner embedded focusSku="SKU-A" /></MemoryRouter>);
  await screen.findByText("Connected campaigns");
  fireEvent.change(screen.getByLabelText("Goal description"), { target: { value: "Sell 100 units" } });
  fireEvent.click(screen.getByRole("button", { name: "Enter parameters manually" }));
  expect(screen.getByText("Goal for SKU SKU-A")).toBeInTheDocument();
  expect(run()).toBeDisabled();
  view.rerender(<MemoryRouter><SaleGoalPlanner key="milo" embedded initialInterpretation={{ draft, method: "rules", notices: [] }} focusSku="SKU-A" /></MemoryRouter>);
  await screen.findByText("Connected campaigns");
  expect(screen.queryByLabelText("Goal description")).not.toBeInTheDocument();
  expect(run()).toBeDisabled();
  expect(runAttempts).toHaveLength(0);
});
