import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { ScenarioWorksheet } from "./ScenarioWorksheet";
import { exampleWorksheet, type Worksheet } from "./types";

vi.mock("../api/queries", () => ({ useSku: () => ({ data: undefined, isPending: false, isFetching: false, isError: false }) }));
function WorksheetHarness() {
  const [value, setValue] = useState<Worksheet>(exampleWorksheet);
  return <ScenarioWorksheet product={{ sku: "A", name: "Mug", mode: "workspace" }} saved={value} onChange={setValue} onSave={vi.fn()} busy={false}
    revision={19} pack={{ schema: "hackathon.sources.v1", files: [{ kind: "inventory", name: "stock.csv", text: "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Mug,12", hash: "x", origin: "upload" }] }} stock={{ code: "A", name: "Mug", stock: 12, asOf: "2026-10-31" }} />;
}
it("replaces unrelated assumptions with SKU facts and allows undo", () => {
  render(<WorksheetHarness />);
  fireEvent.click(screen.getByRole("button", { name: "Load best suggestion" }));
  expect(screen.getByLabelText(/^Available units/)).toHaveValue(12);
  expect(screen.getByLabelText(/^Selling price/)).toHaveValue(null);
  expect(screen.getByLabelText(/^Demand with ads/)).toHaveValue(null);
  expect(screen.getByRole("button", { name: "Save assumptions" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Undo load" }));
  expect(screen.getByLabelText(/^Selling price/)).toHaveValue(600000);
  expect(screen.getByRole("button", { name: "Save assumptions" })).toBeEnabled();
});
it("explains actual inputs, source disagreements, results and break-even without claiming a forecast", () => {
  render(<WorksheetHarness />);
  fireEvent.click(screen.getByText("How this is calculated"));
  const calculation = screen.getByText("How this is calculated").closest("details")!;
  expect(calculation).toHaveTextContent("Current source: 12; worksheet overridden");
  expect(within(calculation).getByRole("heading", { name: "Pause ads has the higher estimated result" })).toBeInTheDocument();
  expect(calculation).toHaveTextContent("-300,000 VND");
  expect(calculation).toHaveTextContent("27 whole units");
  expect(calculation).toHaveTextContent("Available stock cannot cover that threshold.");
  expect(calculation).toHaveTextContent("600,000 − 540,000 − 3,000 − 12,000 = 45,000 VND");
});
