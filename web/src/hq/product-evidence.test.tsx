import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { vi, expect, it } from "vitest";
import { useState } from "react";
import { api } from "../api/client";
import { comparisonRun } from "./model-comparison.fixture";
import { ScenarioWorksheet } from "./ScenarioWorksheet";
import { ProductEconomics } from "./ProductEconomics";
import { requestJSON } from "../studio/account";
import { blankWorksheet, type Worksheet } from "./types";
import type { SourcePack } from "../studio/sources";
vi.mock("../lib/config", () => ({ config: { useFixtures: false } }));
vi.mock("../studio/account", () => ({requestJSON:vi.fn()}));
vi.mock("../api/client", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
const product = { sku: "GD0003", name: "Nồi chiên KD mini 2L", mode: "workspace" as const };
const stock = { code: product.sku, name: product.name, stock: 86, asOf: "2026-11-11 01:00" };

const pack: SourcePack = { schema: "hackathon.sources.v1", files: [{ name: "inventory.csv", kind: "inventory", text: "Mã nội bộ,Tên hàng,Tồn thực tế\nGD0003,Nồi chiên KD mini 2L,86", origin: "upload", hash: "stock" }] };
function Harness() {
  const [saved, onChange] = useState<Worksheet>({ ...blankWorksheet, continueUnits: 40, pauseUnits: 20, spend: 100000 });
  return <><ScenarioWorksheet product={product} pack={pack} stock={stock} revision={30} saved={saved} onChange={onChange} onSave={vi.fn()} busy={false} />
    <ProductEconomics product={product} pack={pack} stock={stock} revision={30} worksheet={saved} /></>;
}
it("autoloads accepted mapped SKU facts in both tabs, retaining workspace stock and explicit assumptions", async () => {
  vi.mocked(requestJSON).mockResolvedValue({data:{code:"GD0003",sku:"NOICHIEN2L",campaign_ids:["CD002"]}});
  vi.mocked(api.get).mockImplementation(async (path) => {
    if (path.startsWith("/parameters")) return {data:[{key:"platform_fee_rate",value:.12,scope:"shop",version:2,is_default:false}]} as never;
    if (path.startsWith("/sale-plans/context")) return {data:{active_goal:null,base_goal:null}} as never;
    expect(path).toBe("/skus/NOICHIEN2L");
    return { data: { sku: "NOICHIEN2L", as_of: "2026-11-11", campaign:{id:"CD002"}, stock_available: 999, price_effective_vnd: 690000, cost: { unit_cost_vnd: 455000, packaging_cost_vnd: 3000 } } } as never;
  });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Harness /></QueryClientProvider>);
  await waitFor(() => expect(screen.getByLabelText("Selling price (VND)")).toHaveValue(690000));
  expect(screen.getByLabelText("Available units")).toHaveValue(86);
  expect(screen.getByLabelText("Unit cost (VND)")).toHaveValue(455000);
  expect(screen.getByLabelText("Packaging / unit (VND)")).toHaveValue(3000);
  await waitFor(() => expect(screen.getByLabelText("Platform fee (%)")).toHaveValue(12));
  expect(screen.getByLabelText("Demand with ads (units)")).toHaveValue(40);
  expect(screen.getByRole("row", { name: /Unit cost/ })).toHaveTextContent("455,000 VND");
  fireEvent.change(screen.getByLabelText("Selling price (VND)"), { target: { value: "600000" } });
  expect(screen.getByLabelText("Selling price (VND)")).toHaveValue(600000);
  expect(screen.getByRole("row", { name: /Selling price after/ })).toHaveTextContent("690,000 VND");
  fireEvent.change(screen.getByLabelText("Selling price (VND)"), { target: { value: "" } });
  expect(screen.getByLabelText("Selling price (VND)")).toHaveValue(null);
  const run = comparisonRun();
  run.calculation_trace!.skus[0]!.sku_id = "NOICHIEN2L";
  run.results.forEach(row => {row.per_sku[0]!.sku_id = "NOICHIEN2L";});
  run.calculation_trace!.actions.forEach(row => {row.per_sku[0]!.sku_id = "NOICHIEN2L";});
  vi.mocked(api.post).mockResolvedValue({data:{scenario:run}});
  fireEvent.click(screen.getByRole("button", {name:"Load best suggestion"}));
  await screen.findByText("Backend model estimates · VND");
  expect(screen.getByRole("row",{name:"Units sold directly 80 63"})).toBeInTheDocument();
  expect(screen.getByRole("row",{name:/Allocated new advertising spend/})).toHaveTextContent("1,200,000 VND");
  expect(vi.mocked(api.post)).toHaveBeenCalledWith("/sale-plans/preview", expect.objectContaining({campaign_id:"CD002",expected_goal:null,horizon_hours:24,focus_sku:"NOICHIEN2L"}));
});
