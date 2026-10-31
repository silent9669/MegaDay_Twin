import { render, screen } from "@testing-library/react";
import { ProductDetails } from "./ProductDetails";
import { useProductIdentity } from "./useProductIdentity";
vi.mock("./useProductIdentity", () => ({ useProductIdentity: vi.fn() }));
vi.mock("./ProductEconomics", () => ({ ProductEconomics: () => null }));
vi.mock("../planner/SaleGoalPlanner", () => ({ SaleGoalPlanner: (props: unknown) => <output data-testid="scenario-planner">{JSON.stringify(props)}</output> }));
it("places the one-click scenario planner inside SKU Details using the confirmed inventory mapping", () => {
  vi.mocked(useProductIdentity).mockReturnValue({ sku: "NOICHIEN2L", campaignIds: ["CD002"], loading: false, error: "", retry: vi.fn() });
  render(<ProductDetails product={{ sku: "GD0003", name: "Fryer", mode: "workspace" }} pack={null} revision={1} />);
  expect(JSON.parse(screen.getByTestId("scenario-planner").textContent!)).toEqual({ embedded: true, focusSku: "NOICHIEN2L", campaignIds: ["CD002"], suggestAndPreview: true });
});
it("does not offer a scenario for an unresolved shop code", () => {
  vi.mocked(useProductIdentity).mockReturnValue({ sku: undefined, campaignIds: undefined, loading: true, error: "", retry: vi.fn() });
  render(<ProductDetails product={{ sku: "GD0003", name: "Fryer", mode: "workspace" }} pack={null} revision={1} />);
  expect(screen.getByRole("button", { name: "Suggest" })).toBeDisabled();
  expect(screen.queryByTestId("scenario-planner")).not.toBeInTheDocument();
});
