import { render, screen } from "@testing-library/react";
import { ProductEconomics } from "./ProductEconomics";
import { blankWorksheet } from "./types";
vi.mock("../api/queries", () => ({ useSku: () => ({ data: undefined }) }));
const product = { sku: "A", name: "Air fryer", mode: "workspace" as const };
it("shows the supplied illustration as an explicit assumption with the correct fee and contribution", () => {
  render(<ProductEconomics product={product} pack={null} worksheet={{ ...blankWorksheet, price: 600000, cost: 480000, feePercent: 12, packaging: 3000 }} />);
  expect(screen.getByRole("row", { name: /Platform fee/ })).toHaveTextContent("72,000 VND");
  expect(screen.getByRole("row", { name: /Contribution before ads/ })).toHaveTextContent("45,000 VND");
  expect(screen.getByRole("row", { name: /Unit cost/ })).toHaveTextContent("Comparison assumption");
});
it("uses SKU-linked facts and keeps unknown fees and contribution empty", () => {
  render(<ProductEconomics product={product} pack={{ schema: "hackathon.sources.v1", files: [{ name: "facts.csv", kind: "general", text: "sku,price_effective_vnd,unit_cost_vnd,packaging_cost_vnd\nA,600000,480000,3000", origin: "upload", hash: "facts" }] }} />);
  expect(screen.getByRole("row", { name: /Unit cost/ })).toHaveTextContent("480,000 VND");
  expect(screen.getByRole("row", { name: /Unit cost/ })).toHaveTextContent("facts.csv");
  expect(screen.getByRole("row", { name: /Platform fee/ })).toHaveTextContent("Missing input");
  expect(screen.getByRole("row", { name: /Contribution before ads/ })).toHaveTextContent("—");
});
