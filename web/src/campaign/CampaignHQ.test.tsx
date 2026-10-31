import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { CampaignHQ } from "./CampaignHQ";
import type { CampaignContext, CampaignRun } from "./types";

vi.mock("./CampaignTables", () => ({ CampaignTables: () => <p>Bảng tính đã lưu</p> }));
vi.mock("./ReplayPanel", () => ({ ReplayPanel: () => null }));
const context = {
  campaign: { name: "CD002 demo", daily_budget_vnd: 1000, spend_today_vnd: 100, sku_detail: [] },
  goal: { goal_id: "confirmed", version: 2, kind: "profit", deadline: "2026-11-12T00:00:00+07:00", params: {}, confirmed_by: "lead" },
  sources: {}, history: [], revision: "r1", canRun: true, dataAsOf: "2026-11-11T15:00:00+07:00",
} as unknown as CampaignContext;
const run = { revision: "r1", goal: context.goal, end: context.goal!.deadline, progress: null,
  scenario: { scenario_id: "saved", input_hash: "hash", calculation_trace: { input_hash: "hash" },
    request: { campaign_id: "CD002", goal_id: "confirmed", start: context.dataAsOf }, results: [], recommendation: null },
} as unknown as CampaignRun;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
afterEach(() => vi.unstubAllGlobals());
it("inherits the goal, computes before AI and retains the table after refresh failure", async () => {
  const calls: string[] = []; let fail = false;
  vi.stubGlobal("fetch", vi.fn(async (url, init) => {
    calls.push(String(url));
    if (String(url).endsWith("/CD002")) return json(context);
    if (String(url).endsWith("/refresh")) {
      expect(JSON.parse(init.body)).toEqual({ revision: "r1", goal_id: "confirmed" });
      return fail ? json({ error: { message: "Dữ liệu chưa đủ" } }, 422) : json(run);
    }
    if (String(url).endsWith("/explain")) return json({ scenarioId: "saved", method: "rules", reasons: [], cautions: [] });
    throw Error("Unexpected endpoint");
  }));
  const watch = { context, error: "", acceptContext: vi.fn() };
  const { rerender } = render(<MemoryRouter><CampaignHQ watch={watch} /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "Cập nhật & đề xuất lại" }));
  await screen.findByText("Bảng tính đã lưu");
  await waitFor(() => expect(screen.getByRole("button", { name: "Cập nhật & đề xuất lại" })).toBeEnabled());
  expect(calls).toEqual(["/api/v1/campaign-analysis/CD002", "/api/v1/campaign-analysis/CD002/refresh", "/api/campaign-hq/explain"]);
  rerender(<MemoryRouter><CampaignHQ watch={{ ...watch, context: { ...context, revision: "r2" } }} /></MemoryRouter>);
  expect(screen.getByText(/Có dữ liệu hoặc mục tiêu mới/)).toBeInTheDocument();
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "Cập nhật & đề xuất lại" }));
  await screen.findByText("Dữ liệu chưa đủ");
  expect(screen.getByText("Bảng tính đã lưu")).toBeInTheDocument();
});
it("does not create a goal or manufacture a scenario without confirmed inputs", () => {
  render(<MemoryRouter><CampaignHQ watch={{ context: { ...context, goal: null }, error: "", acceptContext: vi.fn() }} /></MemoryRouter>);
  expect(screen.getByRole("button", { name: "Cập nhật & đề xuất lại" })).toBeDisabled();
  expect(screen.getByRole("link", { name: /Xem \/ chỉnh mục tiêu/ })).toHaveAttribute("href", "/sale-planner?campaign=CD002");
});
it("restores credited progress from the saved scenario, not the current goal", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ data: { ...run.scenario,
    calculation_trace: { input_hash: "hash", start: context.dataAsOf, horizon_hours: 9,
      goal: { kind: "sell_target", target_units: 80, params: { hq_revision: "r0", hq_goal_version: 1,
        hq_deadline: context.goal!.deadline, hq_progress: { units: 25, spend: 120000, ads_complete: true } } } },
  } })));
  render(<MemoryRouter><CampaignHQ watch={{ context: { ...context,
    history: [{ scenario_id: "saved", created_at: context.dataAsOf! }] }, error: "", acceptContext: vi.fn() }} /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: /Bản lưu/ }));
  expect(await screen.findByText(/Đã ghi nhận trong kỳ mục tiêu: 25 sản phẩm/)).toHaveTextContent("120.000");
  expect(screen.getByText(/Có dữ liệu hoặc mục tiêu mới/)).toBeInTheDocument();
});
