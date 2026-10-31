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
beforeEach(() => { vi.clearAllMocks(); save.mockResolvedValue({ revision: 8 }); });

it("loads and saves the baseline with one click from an empty studio, without a folder picker", async () => {
  const file = await makeSource("ton_kho_google_sheet.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,2", "shop baseline");
  let resolve!: (value: unknown) => void;
  vi.mocked(requestJSON).mockReturnValue(new Promise(done => { resolve = done; }));
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
