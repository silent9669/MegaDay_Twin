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
it("routes replay ZIPs from Data Studio to staging without loading future tables", async () => {
  vi.mocked(requestJSON).mockRejectedValueOnce(Object.assign(Error("Replay demo"), { code: "REPLAY_ARCHIVE" }))
    .mockResolvedValueOnce({ uploaded: true, day: "2026-11-11", adsLagHours: 2 });
  const onLoad = vi.fn();
  render(<ArchiveImport sources={[]} blocked={false} onLoad={onLoad} />);
  const file = new File(["zip"], "live_11_11.zip");
  fireEvent.change(screen.getByLabelText("Upload ZIP archive"), { target: { files: [file] } });
  expect(await screen.findByRole("link", { name: /Mở CD002 để chạy replay/ })).toHaveAttribute("href", "/campaign-hq#replay");
  expect(requestJSON).toHaveBeenLastCalledWith("/api/v1/live/replay-archive", expect.objectContaining({ body: file }));
  expect(onLoad).not.toHaveBeenCalled();
});
it("preserves existing Studio tables when staging replay fails", async () => {
  vi.mocked(requestJSON).mockRejectedValueOnce(Object.assign(Error("Replay demo"), { code: "REPLAY_ARCHIVE" }))
    .mockRejectedValueOnce(Error("Dừng replay trước khi thay ZIP."));
  const onLoad = vi.fn();
  render(<ArchiveImport sources={[]} blocked={false} onLoad={onLoad} />);
  fireEvent.change(screen.getByLabelText("Upload ZIP archive"), { target: { files: [new File(["zip"], "live_11_11.zip")] } });
  expect(await screen.findByRole("alert")).toHaveTextContent("Dừng replay");
  expect(onLoad).not.toHaveBeenCalled();
});
it("reviews current saved tables and preserves general-table identity", async () => {
  const source = await makeSource("one%2Fevents.csv", "event,month\nTet,2", "upload");
  vi.mocked(requestJSON).mockResolvedValue({ tables: [{ file: source.name, source: { ...source, name: "one%252Fevents.csv" }, rowCount: 1, method: "general", columns: [], review: [] }], skipped: [], model: "test" });
  const onLoad = vi.fn(async () => {});
  render(<ArchiveImport sources={[source]} blocked={false} onLoad={onLoad} />);
  fireEvent.click(screen.getByRole("button", { name: "Review & fix with AI" }));
  await waitFor(() => expect(onLoad).toHaveBeenCalledWith([source], true));
  expect(requestJSON).toHaveBeenCalledWith("/api/studio/import?review=1", expect.objectContaining({ body: expect.any(File) }));
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
