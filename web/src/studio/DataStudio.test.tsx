import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";
import { DataStudio } from "./DataStudio";
import { requestJSON, useAccountDocument } from "./account";
import { makeSource, type SourcePack } from "./sources";

vi.mock("./account", () => ({ requestJSON: vi.fn(), useAccountDocument: vi.fn() }));
vi.mock("./SandboxControl", () => ({ SandboxControl: () => null }));
const save = vi.fn();
const setup = (files: SourcePack["files"] = []) => {
  vi.mocked(useAccountDocument).mockReturnValue({
    document: { revision: 7, updatedAt: "2026-11-10T00:00:00Z", data: { schema: "hackathon.sources.v1", files } },
    save, reload: vi.fn(), error: "",
  });
  return render(<MemoryRouter><DataStudio /></MemoryRouter>);
};
beforeEach(() => {
  vi.resetAllMocks();
  save.mockResolvedValue({ revision: 8 });
  vi.mocked(requestJSON).mockImplementation(async path => path === "/api/auth/session" ? { authenticated: true, account: "admin" } : undefined);
});
it.each(["judges", null])("hides restock for account %s", async account => {
  vi.mocked(requestJSON).mockResolvedValue({ authenticated: !!account, account });
  setup();
  await waitFor(() => expect(requestJSON).toHaveBeenCalledWith("/api/auth/session"));
  expect(screen.queryByRole("button", { name: /restock/i })).not.toBeInTheDocument();
});
it.each(["admin", "admin1", "admin2", "admin3", "admin4"])("shows restock for %s", async account => {
  vi.mocked(requestJSON).mockResolvedValue({ authenticated: true, account });
  setup();
  expect(await screen.findByRole("button", { name: "Restock GD0003 +10" })).toBeInTheDocument();
});
it("keeps restock hidden while session is loading or unavailable", async () => {
  let reject!: (reason: Error) => void;
  vi.mocked(requestJSON).mockReturnValue(new Promise((_resolve, fail) => { reject = fail; }));
  setup();
  expect(screen.queryByRole("button", { name: /restock/i })).not.toBeInTheDocument();
  reject(Error("Session unavailable"));
  await waitFor(() => expect(requestJSON).toHaveBeenCalledOnce());
  expect(screen.queryByRole("button", { name: /restock/i })).not.toBeInTheDocument();
});
it("triggers GD0003 restock and reuses its operation ID when backend sync needs retry", async () => {
  const file = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nGD0003,Fryer,2", "upload");
  setup([file]);
  vi.mocked(requestJSON).mockResolvedValueOnce({ change: { before: 2, after: 12 }, backend: "pending" })
    .mockResolvedValueOnce({ change: { before: 2, after: 12 }, backend: "synced" });
  fireEvent.click(await screen.findByRole("button", { name: "Restock GD0003 +10" }));
  await screen.findByText(/GD0003 restocked: 2 → 12/);
  const first = JSON.parse(String(vi.mocked(requestJSON).mock.calls[1]![1]!.body));
  expect(first.revision).toBe(7);
  fireEvent.click(screen.getByRole("button", { name: "Retry restock sync" }));
  await screen.findByText(/Ready to analyze again/);
  const second = JSON.parse(String(vi.mocked(requestJSON).mock.calls[2]![1]!.body));
  expect(second.operationId).toBe(first.operationId);
  expect(save).not.toHaveBeenCalled();
});

it("loads and saves the baseline with one click from an empty studio, without a folder picker", async () => {
  const file = await makeSource("ton_kho_google_sheet.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,2", "shop baseline");
  let resolve!: (value: unknown) => void;
  vi.mocked(requestJSON).mockResolvedValueOnce({ authenticated: true, account: "admin" }).mockReturnValue(new Promise(done => { resolve = done; }));
  const view = setup();
  fireEvent.click(screen.getByRole("button", { name: "Import shop folder" }));
  expect(screen.getByRole("button", { name: "Importing shop…" })).toBeDisabled();
  expect(view.container.querySelector("input[webkitdirectory]")).toBeNull();
  resolve({ schema: "hackathon.sources.v1", files: [file] });
  await waitFor(() => expect(save).toHaveBeenCalledWith({ schema: "hackathon.sources.v1", files: [file] }, 7, "shop"));
  expect(requestJSON).toHaveBeenCalledWith("/api/studio/shop");
  expect(await screen.findByText("1 shop tables loaded.")).toBeInTheDocument();
});

it("imports the shop through a scoped save so the server preserves event tables", async () => {
  const baseline = await makeSource("ton_kho_google_sheet.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,9", "shop baseline");
  const general = await makeSource("notes.csv", "note,value\nKeep,1", "upload");
  setup([{ ...baseline, replay: { date: "2026-11-11", hours: [0], lagHours: 0 } }, general]);
  vi.mocked(requestJSON).mockResolvedValue({ schema: "hackathon.sources.v1", files: [baseline] });
  fireEvent.click(screen.getByRole("button", { name: "Import shop folder" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith({ schema: "hackathon.sources.v1", files: [baseline] }, 7, "shop"));
});

it.each(["shop", "event"] as const)("deletes only the requested %s data with its revision", async scope => {
  const baseline = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,2\nA,Cup,1", "upload");
  setup([{ ...baseline, replay: { date: "2026-11-11", hours: [-1, 0], lagHours: 0 } }]);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(requestJSON).mockResolvedValue({ revision: 8, data: { schema: "hackathon.sources.v1", files: [] } });
  expect(screen.queryByRole("button", { name: "Delete all data" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: `Delete ${scope} data` }));
  await waitFor(() => expect(requestJSON).toHaveBeenCalledWith("/api/workspace/sources", expect.objectContaining({ method: "DELETE", body: JSON.stringify({ scope, revision: 7 }) })));
  expect(save).not.toHaveBeenCalled();
  confirm.mockRestore();
});

it("keeps the current data when loading fails and allows retry", async () => {
  setup();
  vi.mocked(requestJSON).mockRejectedValueOnce(Error("Shop data unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Import shop folder" }));
  expect(await screen.findByText("Shop data unavailable")).toBeInTheDocument();
  expect(save).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Import shop folder" })).toBeEnabled();
});
