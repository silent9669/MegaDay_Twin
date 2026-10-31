import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProductLabels } from "./ProductLabels";
import { requestJSON } from "./account";
import type { LabelResponse } from "./product-labels";
vi.mock("./account", () => ({ requestJSON: vi.fn() }));
const request = vi.mocked(requestJSON);
const context = { market: "Vietnam", asOf: "2026-10-31", evidence: [] };
const response: LabelResponse = {
  configured: true, provider: "test", sourceRevision: 1, sourceFingerprint: "a".repeat(64), stale: false, run: null,
  products: [{ id: "A", name: "Cup", alternateNames: [], source: "inventory", identity: "internal_code", reference: "A", rowCount: 1 }],
};
beforeEach(() => request.mockReset());
describe("warehouse demand labels", () => {
  it("reveals a sold-out source explicitly selected from a Milo notification", async () => {
    request.mockResolvedValue(response);
    render(<ProductLabels sourceRevision={1} blocked={false} selectedCode="B" inventory={[
      { code: "A", name: "Cup", stock: 2, asOf: "" },
      { code: "B", name: "Sold out", stock: 0, asOf: "" },
    ]} />);
    expect(screen.getByText("Sold out").closest("tr")).toHaveClass("stock-highlight");
    expect(screen.getByText("0")).toBeInTheDocument();
    await waitFor(() => expect(request).toHaveBeenCalled());
  });
  it("shows stocked products with search and label action, without settings", async () => {
    request.mockResolvedValue(response);
    render(<ProductLabels sourceRevision={1} blocked={false} inventory={[
      { code: "A", name: "Cup", stock: 2, asOf: "" },
      { code: "B", name: "Sold out", stock: 0, asOf: "" },
      { code: "C", name: "Unknown", stock: null, asOf: "" },
    ]} />);
    await screen.findByText("Cup");
    expect(screen.queryByText("Sold out")).not.toBeInTheDocument();
    expect(screen.queryByText("Unknown")).not.toBeInTheDocument();
    expect(screen.queryByText("Label settings")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Search products")).toBeInTheDocument();
    expect(screen.queryByLabelText("Market")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Refresh product labels" })).not.toBeInTheDocument();
  });
  it("reveals and highlights a Milo source selection after searching and paging", async () => {
    request.mockResolvedValue(response);
    const inventory = Array.from({ length: 10 }, (_, index) => ({ code: `SKU-${index}`, name: `Product ${index}`, stock: index, asOf: "2026-10-31" }));
    const view = render(<ProductLabels sourceRevision={1} blocked={false} inventory={inventory} selectedCode="SKU-9" />);
    await waitFor(() => expect(screen.getByLabelText("Search products")).toHaveValue("SKU-9"));
    expect(screen.getByText("SKU-9").closest("tr")).toHaveClass("stock-highlight");
    fireEvent.change(screen.getByLabelText("Search products"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    view.rerender(<ProductLabels sourceRevision={1} blocked={false} inventory={inventory} selectedCode="SKU-1" />);
    await waitFor(() => expect(screen.getByLabelText("Search products")).toHaveValue("SKU-1"));
    expect(screen.getByText("SKU-1").closest("tr")).toHaveClass("stock-highlight");
    expect(screen.queryByRole("navigation", { name: "Product label pages" })).not.toBeInTheDocument();
    expect(request.mock.calls.every(([, options]) => options?.method !== "POST")).toBe(true);
  });
  it("requires an explicit button click and sends assessment context", async () => {
    request.mockResolvedValue(response);
    render(<ProductLabels sourceRevision={1} blocked={false} />);
    await screen.findByText("Cup");
    expect(request.mock.calls.every(([, options]) => options?.method !== "POST")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Label data" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/api/product-labels", expect.objectContaining({ method: "POST", body: JSON.stringify({ sourceRevision: 1, context: { ...context, asOf: new Date().toISOString().slice(0, 10) } }) })));
  });
  it("keeps unknown products unclassified and explains missing evidence", async () => {
    request.mockResolvedValue({ ...response, run: { id: "run", provider: "test", context, sourceRevision: 1, sourceFingerprint: response.sourceFingerprint, status: "partial", startedAt: "2026-10-31T00:00:00Z", completedAt: "2026-10-31T00:01:00Z", error: null, results: [{ productId: "A", group: null, reason: "Need dated demand evidence" }] } });
    render(<ProductLabels sourceRevision={1} blocked={false} />);
    await screen.findByText("Need dated demand evidence");
    expect(screen.getByText("Unclassified")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Label data" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/api/product-labels", expect.objectContaining({ method: "POST", body: JSON.stringify({ sourceRevision: 1, context }) })));
  });
  it("blocks unavailable models and unsaved sources", async () => {
    request.mockResolvedValue({ ...response, configured: false });
    render(<ProductLabels sourceRevision={1} blocked />);
    await screen.findByText("Decision model is not connected.");
    expect(screen.getByRole("button", { name: "Label data" })).toBeDisabled();
  });
});
it("does not display late labels after a newer source revision arrives", async () => {
  let resolve!: (value: LabelResponse) => void;
  const pending = new Promise<LabelResponse>(r => { resolve = r; });
  request.mockImplementation(async () => pending);
  const view = render(<ProductLabels sourceRevision={1} blocked={false} />);
  await waitFor(() => expect(request).toHaveBeenCalled());
  view.rerender(<ProductLabels sourceRevision={2} blocked={false} />);
  resolve(response);
  await screen.findByText("Labels need updating for these sources or assessment details.");
  expect(screen.queryByText("Cup")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Label data" })).toBeDisabled();
});

it("filters demand groups and saves a manual dropdown change", async () => {
  request.mockResolvedValue(response);
  render(<ProductLabels sourceRevision={1} blocked={false} inventory={[
    { code: "A", name: "Cup", stock: 2, asOf: "", demand: "Trendy" },
    { code: "B", name: "Lamp", stock: 1, asOf: "", demand: "Seasonal" },
  ]} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Label data" })).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Filter demand labels"), { target: { value: "Trendy" } });
  expect(screen.queryByText("Lamp")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Demand label for Cup"), { target: { value: "Normal" } });
  await waitFor(() => expect(request).toHaveBeenCalledWith("/api/product-labels", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ sourceRevision: 1, productId: '["inventory","internal_code","A"]', group: "Normal" }) })));
});
