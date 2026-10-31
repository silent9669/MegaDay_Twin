import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { Goals } from "./Goals";
import { Campaigns } from "./Campaigns";
import { ModelComparison } from "./ModelComparison";
import { HQSelection } from "./selection";
import type { SourcePack } from "../studio/sources";

vi.mock("../lib/config", () => ({ config: { useFixtures: false } }));
vi.mock("../planner/SaleGoalPlanner", () => ({ SaleGoalPlanner: (props: unknown) => <output data-testid="planner">{JSON.stringify(props)}</output> }));
vi.mock("../planner/SkuGoalAssistant", () => ({ SkuGoalAssistant: () => null }));
vi.mock("../pages/compare/ComparePage", () => ({ ComparePage: ({ sku }: { sku: string }) => <output data-testid="compare">{sku}</output> }));
afterEach(() => vi.unstubAllGlobals());
const product = { sku: "GD0003", name: "Cooker", mode: "workspace" as const };
const pack: SourcePack = { schema: "hackathon.sources.v1", files: [{
  kind: "campaigns", name: "cau_truc_chien_dich.json", origin: "upload", hash: "test",
  text: JSON.stringify([{ ma_chien_dich: "CD002", ten_chien_dich: "Cooker campaign", sku: ["NOICHIEN2L"], ngan_sach_ngay: 1000 }]),
}] };

it("uses one resolved identity for product goals, comparisons and campaign membership", async () => {
  const open = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ data: { code: "GD0003", sku: "NOICHIEN2L", campaign_ids: ["CD002"] } })));
  render(<MemoryRouter><HQSelection.Provider value={{ product, open, tab: vi.fn(), log: vi.fn() }}>
    <Goals product={product} /><ModelComparison product={product} /><Campaigns pack={pack} revision={1} />
  </HQSelection.Provider></MemoryRouter>);
  await waitFor(() => expect(screen.getByTestId("compare")).toHaveTextContent("NOICHIEN2L"));
  expect(JSON.parse(screen.getByTestId("planner").textContent!)).toMatchObject({ focusSku: "NOICHIEN2L", campaignIds: ["CD002"] });
  expect(await screen.findByRole("heading", { name: "Cooker campaign" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Plan this campaign" })).toHaveAttribute("href", "/city-demo?district=hq&hq=goals&campaign=CD002");
  screen.getByRole("button", { name: "NOICHIEN2L" }).click();
  expect(open).toHaveBeenCalledWith(expect.objectContaining({ sku: "NOICHIEN2L", mode: "model" }), "details");
});

it("blocks model goals and simulations for unresolved shop codes", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ data: { code: "GD0003", sku: null, campaign_ids: [] } })));
  render(<MemoryRouter><Goals product={product} /><ModelComparison product={product} /></MemoryRouter>);
  await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
  expect(screen.queryByTestId("planner")).not.toBeInTheDocument();
  expect(screen.queryByTestId("compare")).not.toBeInTheDocument();
});
