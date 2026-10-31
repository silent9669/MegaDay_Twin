import { render, screen, within } from "@testing-library/react";
import type { ScenarioRun } from "../api/types";
import { CampaignTables } from "./CampaignTables";

vi.mock("../planner/CalculationEvidence", () => ({ CalculationEvidence: () => null }));
const dist = (mean: number) => ({ mean, p10: mean, p50: mean, p90: mean });
function sample(): ScenarioRun {
  return { input_hash: "hash", calculation_trace: { input_hash: "hash", start: "2026-11-11T15:00:00+07:00", horizon_hours: 9,
    skus: [{ sku_id: "POT", in_campaign: true, is_bundle: false, stock_start: 83, restock: [],
      economics_by_day: [{ day: "2026-11-11", eff_price: 600000, unit_cost: 480000, fee_rate: .12, packaging_cost: 3000, m: 45000 }] }],
    actions: ["continue", "pause"].map(key => ({ key,
      metrics: { revenue: dist(49800000), selling_cost: dist(46065000), contribution: dist(3735000), spend: dist(key === "pause" ? 0 : 1200000), after_ads: dist(key === "pause" ? 3735000 : 2535000) },
      per_sku: [{ sku_id: "POT", units_sold: dist(83), leftover_units: dist(0) }] })),
  }, results: ["continue", "pause"].map(key => ({ key, feasible: true, violations: [] })) } as unknown as ScenarioRun;
}
it("renders the agreed rows from trace amounts, not objective J", () => {
  render(<CampaignTables scenario={sample()} selectedSku="POT" onSelect={() => {}} />);
  expect(screen.getByText("45.000đ")).toBeInTheDocument();
  expect(screen.getByText("2.535.000đ")).toBeInTheDocument();
  expect(screen.getByText("Giá vốn hàng đã bán + phí sàn + đóng gói")).toBeInTheDocument();
});
it("blocks pause presentation during committed flash hours", () => {
  const run = sample(); run.results[1]!.feasible = false; run.results[1]!.violations = ["campaign_must_run"];
  render(<CampaignTables scenario={run} selectedSku="POT" onSelect={() => {}} />);
  expect(screen.getByText("Không hợp lệ do cam kết flash sale")).toBeInTheDocument();
  const row = screen.getByText("Chi quảng cáo mới dự kiến").closest("tr")!;
  expect(within(row).getByText("Không áp dụng")).toBeInTheDocument();
  expect(within(row).queryByText("0đ")).not.toBeInTheDocument();
});
it("does not reconstruct missing revenue from an old trace", () => {
  const run = sample(); delete run.calculation_trace!.actions[0]!.metrics.revenue;
  render(<CampaignTables scenario={run} selectedSku="POT" onSelect={() => {}} />);
  expect(within(screen.getByText("Doanh thu").closest("tr")!).getByText("Chưa có dữ liệu")).toBeInTheDocument();
});
