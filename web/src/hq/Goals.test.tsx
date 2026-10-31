import { render, screen } from "@testing-library/react";
import { Goals } from "./Goals";
import type { TrackedProduct } from "./types";

vi.mock("../planner/SaleGoalPlanner", () => ({ SaleGoalPlanner: (props: { campaignIds?: string[]; focusSku?: string }) =>
  <output data-testid="planner">{JSON.stringify(props)}</output> }));
vi.mock("../planner/SkuGoalAssistant", () => ({ SkuGoalAssistant: () => null }));

it("keeps the campaign scope when arriving from CD002 despite an existing product selection", () => {
  render(<Goals campaignId="CD002" product={{ sku: "NOICHIEN2L", name: "Mini cooker" } as TrackedProduct} />);
  expect(screen.getByLabelText("Goal quantity scope")).toHaveValue("campaign");
  const props = JSON.parse(screen.getByTestId("planner").textContent!);
  expect(props.campaignIds).toEqual(["CD002"]);
  expect(props.focusSku).toBeUndefined();
});

it("keeps the existing product workflow without a campaign link", () => {
  render(<Goals product={{ sku: "NOICHIEN2L", name: "Mini cooker" } as TrackedProduct} />);
  expect(screen.getByLabelText("Goal quantity scope")).toHaveValue("product");
  const props = JSON.parse(screen.getByTestId("planner").textContent!);
  expect(props.focusSku).toBe("NOICHIEN2L");
  expect(props.campaignIds).toBeUndefined();
});
