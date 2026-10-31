import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SourceDiagnostics } from "./SourceDiagnostics";
import type { Issue } from "./sources";

const issues: Issue[] = [
  { level: "error", code: "NEG_STOCK", row: 33, column: "Tồn thực tế", message: "Negative stock." },
  { level: "warning", code: "TIMEZONE_REVIEW", message: "Confirm timezone." },
  { level: "info", code: "PII_DROPPED", message: "Contact columns removed." },
];
function open(overrides: Partial<Parameters<typeof SourceDiagnostics>[0]> = {}) {
  const onViewRecord = vi.fn();
  render(<SourceDiagnostics issues={issues} query="" format="csv" rowCount={40}
    repositorySample={false} onViewRecord={onViewRecord} {...overrides} />);
  return onViewRecord;
}
describe("source diagnostics", () => {
  it("explains checks, separates severity and keeps informational notices out of errors", () => {
    open({ repositorySample: true });
    expect(screen.getByText(/not application errors/)).toBeInTheDocument();
    expect(screen.getByText(/intentional data issues/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Needs correction (1)" }));
    expect(screen.getByRole("heading", { name: "Negative inventory" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Contact columns removed" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Showing 1 of 3");
    fireEvent.click(screen.getByRole("button", { name: "Information (1)" }));
    expect(screen.getByText(/does not mean the upload failed/)).toBeInTheDocument();
  });
  it("offers the actual CSV record index without treating file-level checks as rows", () => {
    const view = open();
    fireEvent.click(screen.getByRole("button", { name: "View record 32" }));
    expect(view).toHaveBeenCalledWith(31);
    expect(screen.getAllByRole("button", { name: /View record/ })).toHaveLength(1);
    expect(screen.queryByText(/intentional data issues/)).not.toBeInTheDocument();
  });
  it("maps JSON record numbers without a CSV header offset", () => {
    const view = open({ format: "json", issues: [
      { level: "error", code: "INVALID_SKUS", row: 2, message: "Invalid product list." },
    ] });
    fireEvent.click(screen.getByRole("button", { name: "View record 2" }));
    expect(view).toHaveBeenCalledWith(1);
  });
  it("explains an empty search without claiming the file is clean", () => {
    open({ query: "nothing matches" });
    expect(screen.getByText(/No checks match/)).toBeInTheDocument();
    expect(screen.queryByText(/No issues found/)).not.toBeInTheDocument();
  });
  it("searches human-readable explanations", () => {
    open({ query: "negative inventory" });
    expect(screen.getByRole("heading", { name: "Negative inventory" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Showing 1 of 3");
  });
  it("does not confuse clean file checks with validated economic results", () => {
    open({ issues: [] });
    expect(screen.getByText(/does not certify forecast or profit accuracy/)).toBeInTheDocument();
  });
  it("groups repeated formats, counts distinct records, and keeps errors above information", () => {
    const view = open({ issues: [
      { level: "info", code: "MONEY_NORMALIZATION", row: 2, column: "giá vốn", message: "Preview only", value: "45k", normalizedVnd: 45000 },
      { level: "info", code: "MONEY_NORMALIZATION", row: 2, column: "phí đóng gói", message: "Preview only", value: "3k", normalizedVnd: 3000 },
      { level: "error", code: "MISSING_COST", row: 3, message: "Unknown cost" },
    ] });
    expect(screen.getAllByRole("article")).toHaveLength(2);
    expect(screen.getAllByRole("heading", { level: 4 })[0]).toHaveTextContent("Product cost is missing");
    const summary = screen.getByText("Inspect 2 checks · affected records: 1");
    const details = summary.closest("details")!;
    expect(details.open).toBe(false);
    fireEvent.click(summary);
    expect(details.open).toBe(true);
    expect(screen.getByText("45.000 ₫")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "View record 1" })[0]!);
    expect(view).toHaveBeenCalledWith(0);
  });
});
