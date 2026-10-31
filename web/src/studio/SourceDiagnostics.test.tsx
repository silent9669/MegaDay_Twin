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
});
