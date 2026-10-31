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
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Goal description"), { target: { value: draft.text } });
  fireEvent.click(screen.getByRole("button", { name: "Interpret goal" }));
  await screen.findByLabelText("Primary goal");
}
function selectCampaign(id = "campaign-real") { fireEvent.change(screen.getByLabelText("Campaign"), { target: { value: id } }); }
const confirm = () => screen.getByRole("checkbox", { name: /I confirm these parameters/ });
const run = () => screen.getByRole("button", { name: /Save goal and run simulation/ });

describe("sale goal review and real simulation", () => {
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
    await review(); selectCampaign();
    expect(run()).toBeDisabled(); expect(runAttempts).toHaveLength(0);
    fireEvent.click(confirm()); expect(run()).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Maximum ad budget (₫)"), { target: { value: "0" } });
    expect(confirm()).not.toBeChecked(); expect(run()).toBeDisabled();
    fireEvent.click(confirm()); selectCampaign("campaign-other");
    expect(confirm()).not.toBeChecked();
    fireEvent.click(confirm()); fireEvent.click(run());
    await screen.findByText("Options for Chiến dịch khác");
    expect(runAttempts[0]).toMatchObject({ campaignId: "campaign-other", confirmed: true, draft: { text: draft.text, maxSpendVnd: 0 } });
  });
  it("requires review of unsupported requirements and clears acknowledgement on edits", async () => {
    currentDraft.unresolved = ["Tự tạo voucher chưa được hỗ trợ"];
    await review(); selectCampaign();
    expect(confirm()).toBeDisabled();
    expect(screen.getByText("Tự tạo voucher chưa được hỗ trợ")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /I have reviewed and agree/ }));
    fireEvent.click(confirm()); expect(run()).toBeEnabled();
    fireEvent.change(screen.getByLabelText("Maximum expected loss (₫)"), { target: { value: "0" } });
    expect(confirm()).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /I have reviewed and agree/ })).not.toBeChecked();
    expect(runAttempts).toHaveLength(0);
  });
  it("retries a failed simulation using the saved goal and clears it after edits", async () => {
    runResponse = (attempt) => attempt === 1 ? json({ goalId: "goal-saved", stage: "simulation", error: { code: "BLOCKING_DATA_ISSUE", message: "Thiếu dữ liệu tồn kho", details: { missing: ["inventory"] } } }, 422) : json(result);
    await review(); selectCampaign(); fireEvent.click(confirm()); fireEvent.click(run());
    await screen.findByText("Thiếu dữ liệu tồn kho");
    expect(runAttempts).toHaveLength(1);
    expect(runAttempts[0]).not.toHaveProperty("goalId");
    fireEvent.click(screen.getByRole("button", { name: "Retry simulation" }));
    await screen.findByText("Options for Chiến dịch thực");
    expect(runAttempts[1]).toMatchObject({ goalId: "goal-saved" });
    fireEvent.change(screen.getByLabelText("Maximum ad budget (₫)"), { target: { value: "500000" } });
    expect(screen.queryByText("Options for Chiến dịch thực")).not.toBeInTheDocument();
    fireEvent.click(confirm()); fireEvent.click(run());
    await waitFor(() => expect(runAttempts).toHaveLength(3));
    expect(runAttempts[2]).not.toHaveProperty("goalId");
  });
  it("renders actual returned values with quantiles and calculation provenance", async () => {
    await review(); selectCampaign(); fireEvent.click(confirm()); fireEvent.click(run());
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
  it("keeps unsupported goal fields visible and blocks confirmation until corrected", async () => {
    currentDraft.targetUnits = 100;
    currentDraft.start = "2026-11-11T08:30:00+07:00";
    await review(); selectCampaign();
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
    currentContext.notice = "Backend cần nâng cấp";
    await review(); selectCampaign(); fireEvent.click(confirm());
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

it("offers full goal entry inside HQ while keeping a supplied Milo review read-only until confirmation", async () => {
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
