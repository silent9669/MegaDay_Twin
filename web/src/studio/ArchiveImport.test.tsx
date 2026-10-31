import { createRef } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ArchiveImport, type ArchiveImportHandle } from "./ArchiveImport";
import { requestJSON } from "./account";
import { makeSource } from "./sources";
vi.mock("./account", () => ({ requestJSON: vi.fn() }));
beforeEach(() => vi.mocked(requestJSON).mockReset());
it("extracts and auto-loads a ZIP without a second click, guarding duplicate drops", async () => {
  const source = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,2", "upload");
  vi.mocked(requestJSON).mockResolvedValue({ tables: [{ file: "stock.csv", source, rowCount: 1, method: "recognized", columns: [] }], skipped: [], model: null });
  const onLoad = vi.fn(async () => {}), ref = createRef<ArchiveImportHandle>();
  render(<ArchiveImport ref={ref} sources={[]} blocked={false} onLoad={onLoad} />);
  const file = new File(["zip"], "stock.zip");
  fireEvent.change(screen.getByLabelText("Upload ZIP archive"), { target: { files: [file] } });
  ref.current?.importFile(file);
  await waitFor(() => expect(onLoad).toHaveBeenCalledWith([source], false));
  expect(requestJSON).toHaveBeenCalledTimes(1);
  expect(requestJSON).toHaveBeenCalledWith("/api/studio/import", expect.anything());
  expect(screen.getByRole("status")).toHaveTextContent("1 tables loaded");
});
it("rejects non-ZIP drops", async () => {
  const ref = createRef<ArchiveImportHandle>(), onLoad = vi.fn();
  render(<ArchiveImport ref={ref} sources={[]} blocked={false} onLoad={onLoad} />);
  fireEvent.change(screen.getByLabelText("Upload ZIP archive"), { target: { files: [new File(["x"], "stock.csv")] } });
  await screen.findByRole("alert");
  expect(requestJSON).not.toHaveBeenCalled();
});
it("reviews current saved tables and preserves general-table identity", async () => {
  const source = await makeSource("one%2Fevents.csv", "event,month\nTet,2", "upload");
  vi.mocked(requestJSON).mockResolvedValue({ tables: [{ file: source.name, source: { ...source, name: "one%252Fevents.csv" }, rowCount: 1, method: "general", columns: [], review: [] }], skipped: [], model: "test" });
  const onLoad = vi.fn(async () => {});
  render(<ArchiveImport sources={[source]} blocked={false} onLoad={onLoad} />);
  fireEvent.click(screen.getByRole("button", { name: "Review & fix with AI" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("No repairs needed"));
  expect(onLoad).not.toHaveBeenCalled();
  expect(requestJSON).toHaveBeenCalledWith("/api/studio/import?review=1", expect.objectContaining({ body: expect.any(File) }));
});
it("automatically saves repaired tables and exposes remaining issues", async () => {
  const source = await makeSource("cost.csv", "tên sp,giá vốn\nCup,45k", "upload");
  const fixed = await makeSource("cost.csv", "tên sp,giá vốn\nCup,45000", "upload");
  vi.mocked(requestJSON).mockResolvedValue({ tables: [{ file: source.name, source: fixed, rowCount: 1, method: "recognized", columns: [], repairs: ["Normalized 1 VND amount."], remainingIssues: ["Missing timezone"] }], skipped: [], model: "test" });
  const onLoad = vi.fn(async () => {});
  render(<ArchiveImport sources={[source]} blocked={false} onLoad={onLoad} />);
  fireEvent.click(screen.getByRole("button", { name: "Review & fix with AI" }));
  await waitFor(() => expect(onLoad).toHaveBeenCalledWith([fixed], true));
  expect(screen.getByRole("status")).toHaveTextContent("AI fixes saved");
  expect(screen.getByText(/Needs review: Missing timezone/)).toBeInTheDocument();
});
it("keeps all saved tables when review skips a table or fails", async () => {
  const source = await makeSource("notes.csv", "event,month\nTet,2", "upload");
  vi.mocked(requestJSON).mockResolvedValue({ tables: [], skipped: [{ file: source.name, reason: "Invalid" }], model: "test" });
  const onLoad = vi.fn();
  render(<ArchiveImport sources={[source]} blocked={false} onLoad={onLoad} />);
  fireEvent.click(screen.getByRole("button", { name: "Review & fix with AI" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Your tables are unchanged");
  expect(onLoad).not.toHaveBeenCalled();
  vi.mocked(requestJSON).mockRejectedValueOnce(Error("AI unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Review & fix with AI" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("AI unavailable"));
  expect(onLoad).not.toHaveBeenCalled();
});

it("does not describe unresolved repairs as no changes needed", async () => {
  const source = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,-2", "upload");
  vi.mocked(requestJSON).mockResolvedValue({ tables: [{ file: source.name, source, rowCount: 1, method: "recognized", columns: [], remainingIssues: ["Confirm negative stock"] }], skipped: [], model: "test" });
  const onLoad = vi.fn(async () => {});
  render(<ArchiveImport sources={[source]} blocked={false} onLoad={onLoad} />);
  fireEvent.click(screen.getByRole("button", { name: "Review & fix with AI" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("1 issue needs input"));
  expect(screen.getByRole("status")).not.toHaveTextContent("No repairs needed");
  expect(onLoad).not.toHaveBeenCalled();
});

it("saves AI cell patches directly through the existing table save flow", async () => {
  const source = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cupp,2", "upload");
  const fixed = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,2", "upload");
  vi.mocked(requestJSON).mockResolvedValue({ tables: [{ file: source.name, source: fixed, rowCount: 1, method: "recognized", columns: [], repairs: ["AI corrected row 1, Tên hàng: Typo"], remainingIssues: [], review: ["Product name contained a typo."] }], skipped: [], model: "test" });
  const onLoad = vi.fn(async () => {});
  render(<ArchiveImport sources={[source]} blocked={false} onLoad={onLoad} />);
  fireEvent.click(screen.getByRole("button", { name: "Review & fix with AI" }));
  await waitFor(() => expect(onLoad).toHaveBeenCalledWith([fixed], true));
  expect(screen.getByRole("status")).toHaveTextContent("AI fixes saved");
  expect(screen.getByText(/AI corrected row 1/)).toBeInTheDocument();
  expect(screen.getByRole("status")).not.toHaveTextContent("needs input");
  expect(screen.getByText("AI notes: Product name contained a typo.")).toBeInTheDocument();
});

it("keeps unresolved model observations visible without claiming repairs were made", async () => {
  const source = await makeSource("notes.csv", "name,value\nCup,1", "upload");
  vi.mocked(requestJSON).mockResolvedValue({ tables: [{ file: source.name, source, rowCount: 1, method: "general", columns: [], repairs: [], remainingIssues: [], review: ["Confirm what value measures."] }], skipped: [], model: "test" });
  const onLoad = vi.fn(async () => {});
  render(<ArchiveImport sources={[source]} blocked={false} onLoad={onLoad} />);
  fireEvent.click(screen.getByRole("button", { name: "Review & fix with AI" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Review AI notes"));
  expect(onLoad).not.toHaveBeenCalled();
});
