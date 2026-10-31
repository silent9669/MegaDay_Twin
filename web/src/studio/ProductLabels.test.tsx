import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import { ProductLabels } from "./ProductLabels";
import { requestJSON } from "./account";
import type { LabelResponse, LabelRun } from "./product-labels";

vi.mock("./account", () => ({ requestJSON: vi.fn() }));
const request = vi.mocked(requestJSON);
const fingerprint = "a".repeat(64);
function response(overrides: Partial<LabelResponse> = {}): LabelResponse {
  return {
    configured: true, provider: "Test provider", sourceRevision: 10,
    sourceFingerprint: fingerprint, stale: false, run: null,
    products: [
      { id: "orders:SKU1", name: "Bình nước", alternateNames: [], source: "orders", identity: "platform_sku", reference: "SKU1", rowCount: 12 },
      { id: "cost:binh", name: "binh nuoc", alternateNames: [], source: "cost", identity: "name_only", reference: "binh nuoc", rowCount: 1 },
    ],
    ...overrides,
  };
}
function complete(overrides: Partial<LabelRun> = {}): LabelRun {
  return {
    id: "00000000-0000-4000-8000-000000000001", provider: "Test provider",
    sourceRevision: 10, sourceFingerprint: fingerprint, status: "completed",
    startedAt: "2026-10-31T00:00:00Z", completedAt: "2026-10-31T00:00:01Z",
    results: [{ productId: "orders:SKU1", group: "on_dinh", reason: "Tool analysis explanation", confidence: 0.87 }],
    error: null, ...overrides,
  };
}
const props = { sourceRevision: 10, autoRunRevision: null as number | null, blocked: false };
const posts = () => request.mock.calls.filter(([, options]) => options?.method === "POST");
beforeEach(() => request.mockReset());

describe("external product label workflow", () => {
  it("shows unconfigured honestly and never calls the tool after an import", async () => {
    request.mockResolvedValue(response({ configured: false, provider: null }));
    render(<ProductLabels {...props} autoRunRevision={10} />);
    await screen.findByText("External labeling tool is not connected");
    fireEvent.click(screen.getByRole("button", { name: "Show products (2)" }));
    expect(screen.getAllByText("Chưa phân loại")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Run labeling" })).toBeDisabled();
    expect(screen.getByText(/Name only · matching needed/)).toBeInTheDocument();
    expect(posts()).toHaveLength(0);
  });
  it("does not label initial demo data automatically and shows persisted provider details", async () => {
    request.mockResolvedValue(response({ run: complete() }));
    render(<ProductLabels {...props} />);
    await screen.findByText("Labels received from the external tool");
    fireEvent.click(screen.getByRole("button", { name: "Show products (2)" }));
    expect(screen.getByText("Ổn định")).toBeInTheDocument();
    expect(screen.getByText("Tool confidence: 87%")).toBeInTheDocument();
    expect(screen.getByText("Source: Test provider")).toBeInTheDocument();
    expect(screen.getByText(/not yet applied to forecasts/)).toBeInTheDocument();
    expect(posts()).toHaveLength(0);
  });
  it("runs once only after the imported revision is saved and no longer blocked", async () => {
    let saved = response();
    request.mockImplementation(async (_url, options) => {
      if (options?.method === "POST") saved = response({ run: complete() });
      return saved;
    });
    const view = render(<ProductLabels {...props} blocked autoRunRevision={10} />);
    await screen.findByText("Waiting for source changes to be saved");
    expect(posts()).toHaveLength(0);
    view.rerender(<ProductLabels {...props} autoRunRevision={10} />);
    await screen.findByText("Labels received from the external tool");
    expect(posts()).toHaveLength(1);
    expect(JSON.parse(posts()[0]![1]!.body as string)).toEqual({ sourceRevision: 10 });
    view.rerender(<ProductLabels {...props} autoRunRevision={10} />);
    expect(posts()).toHaveLength(1);
  });
  it("does not auto-run a saved import token against a different source revision", async () => {
    request.mockResolvedValue(response({ sourceRevision: 11 }));
    render(<ProductLabels {...props} sourceRevision={11} autoRunRevision={10} />);
    await screen.findByText("Ready to request product labels");
    expect(posts()).toHaveLength(0);
  });
  it("preserves one eligible import attempt under StrictMode effect replay", async () => {
    let saved = response();
    request.mockImplementation(async (_url, options) => {
      if (options?.method === "POST") saved = response({ run: complete() });
      return saved;
    });
    render(<StrictMode><ProductLabels {...props} autoRunRevision={10} /></StrictMode>);
    await screen.findByText("Labels received from the external tool");
    expect(posts()).toHaveLength(1);
  });
  it("shows a failed attempt without an automatic retry loop and allows explicit retry", async () => {
    let saved = response();
    let attempts = 0;
    request.mockImplementation(async (_url, options) => {
      if (options?.method === "POST") {
        attempts++;
        if (attempts === 1) {
          saved = response({ run: complete({ status: "failed", results: [], error: "Provider unavailable" }) });
          throw Error("Provider unavailable");
        }
        saved = response({ run: complete() });
      }
      return saved;
    });
    render(<ProductLabels {...props} autoRunRevision={10} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Provider unavailable");
    expect(posts()).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole("button", { name: "Retry labeling" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Retry labeling" }));
    await screen.findByText("Labels received from the external tool");
    expect(posts()).toHaveLength(2);
  });
  it("hides stale results and supports search without merging equal names across sources", async () => {
    request.mockResolvedValue(response({ stale: true, run: complete() }));
    render(<ProductLabels {...props} />);
    await screen.findByText("Labels need updating for this import");
    fireEvent.click(screen.getByRole("button", { name: "Show products (2)" }));
    expect(screen.queryByText("Ổn định")).not.toBeInTheDocument();
    expect(screen.getAllByText("Chưa phân loại")).toHaveLength(2);
    fireEvent.change(screen.getByRole("textbox", { name: "Search products" }), { target: { value: "SKU1" } });
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    fireEvent.change(screen.getByRole("textbox", { name: "Search products" }), { target: { value: "nothing" } });
    expect(screen.getByText("No products match this search.")).toBeInTheDocument();
  });
  it("shows partial results including an explicit unclassified reason and zero confidence", async () => {
    request.mockResolvedValue(response({ run: complete({ status: "partial", results: [
      { productId: "orders:SKU1", group: null, reason: "Not enough evidence", confidence: 0 },
    ] }) }));
    render(<ProductLabels {...props} />);
    await screen.findByText("Some products still need a label");
    fireEvent.click(screen.getByRole("button", { name: "Show products (2)" }));
    expect(screen.getAllByText("Chưa phân loại")).toHaveLength(2);
    expect(screen.getByText("Not enough evidence")).toBeInTheDocument();
    expect(screen.getByText("Tool confidence: 0%")).toBeInTheDocument();
  });
  it("disables duplicate dispatch while a saved run is still running", async () => {
    request.mockResolvedValue(response({ run: complete({ status: "running", results: [], completedAt: null }) }));
    render(<ProductLabels {...props} autoRunRevision={10} />);
    await screen.findByText("Labeling products…");
    expect(screen.getByRole("button", { name: "Run labeling" })).toBeDisabled();
    expect(posts()).toHaveLength(0);
  });
  it("ignores a late successful response for a previous import", async () => {
    let saved = response();
    let finish!: (value: LabelResponse) => void;
    request.mockImplementation(async (_url, options) => {
      if (options?.method === "POST") return new Promise<LabelResponse>((resolve) => { finish = resolve; });
      return saved;
    });
    const view = render(<ProductLabels {...props} />);
    await screen.findByText("Ready to request product labels");
    fireEvent.click(screen.getByRole("button", { name: "Run labeling" }));
    await screen.findByText("Labeling products…");
    saved = response({ sourceRevision: 11 });
    view.rerender(<ProductLabels {...props} sourceRevision={11} />);
    await act(async () => { finish(response({ run: complete() })); });
    await screen.findByText("Ready to request product labels");
    fireEvent.click(screen.getByRole("button", { name: "Show products (2)" }));
    expect(screen.queryByText("Tool analysis explanation")).not.toBeInTheDocument();
    expect(screen.getAllByText("Chưa phân loại")).toHaveLength(2);
  });
});
