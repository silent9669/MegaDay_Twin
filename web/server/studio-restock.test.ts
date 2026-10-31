// @vitest-environment node
import { expect, it, vi } from "vitest";
import { makeSource } from "../src/studio/sources";
import { mergeReplaySources, visibleSources } from "../src/studio/replay";
import { sourceView } from "../src/studio/source-view";
import { sourceText } from "../src/studio/source-text";
import { restockInventory, syncRestock } from "./studio-restock";
import { suggestedWorksheet, workspaceSuggestion } from "../src/hq/worksheet-suggestions";
import { exampleWorksheet, worksheetResults } from "../src/hq/types";

it("adds ten units only to GD0003's latest baseline observation and changes stock-limited calculations", async () => {
  const inventory = await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-09,GD0003,Fryer,20\n2026-11-10,GD0003,Fryer,2\n2026-11-10,OTHER,Cup,9", "upload");
  const result = await restockInventory({ schema: "hackathon.sources.v1", files: [inventory] }, 1, "receipt-1");
  expect(result.csv).toContain("receipt-1");
  expect(result.change).toMatchObject({ before: 2, after: 12, code: "GD0003" });
  expect(sourceText(result.pack.files[0]!)).toContain("2026-11-09,GD0003,Fryer,20");
  expect(sourceText(result.pack.files[0]!)).toContain("2026-11-10,OTHER,Cup,9");
  const row = sourceView(result.pack, 1).stock.find(row => row.code === "GD0003")!;
  const facts = suggestedWorksheet(workspaceSuggestion(result.pack, "GD0003", row));
  const before = worksheetResults({ ...exampleWorksheet, stock: 2 })!;
  const after = worksheetResults({ ...exampleWorksheet, stock: facts.stock })!;
  expect(after[0]!.units - before[0]!.units).toBe(10);
  expect(after[0]!.afterAds - before[0]!.afterAds).toBe(450000);
});
it("keeps replay history and other products unchanged while carrying a receipt into future stock", async () => {
  const baseline = await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-10,GD0003,Fryer,83", "shop");
  const event = { ...await makeSource("stock.csv", "Thời điểm cập nhật,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-11 00:00,GD0003,Fryer,5\n2026-11-11 01:00,GD0003,Fryer,2\n2026-11-11 02:00,GD0003,Fryer,0", "event"), replay: { date: "2026-11-11", hours: [0, 1, 2], lagHours: 0 } };
  const pack = { schema: "hackathon.sources.v1" as const, files: await mergeReplaySources([baseline], [event]) };
  const result = await restockInventory(pack, 1, "receipt-2");
  expect(result.change).toMatchObject({ before: 2, after: 12 });
  expect(result.pack.files[0]!.replay).toEqual(pack.files[0]!.replay);
  expect(sourceView(visibleSources(result.pack, 0), 0).stock[0]!.stock).toBe(5);
  expect(sourceView(visibleSources(result.pack, 1), 1).stock[0]!.stock).toBe(12);
  expect(sourceView(visibleSources(result.pack, 2), 2).stock[0]!.stock).toBe(10);
  expect(result.csv).not.toContain("2026-11-11 02:00");
});
it("rejects missing and unknown stock without inventing units", async () => {
  await expect(restockInventory({ schema: "hackathon.sources.v1", files: [] }, 1, "receipt-3")).rejects.toThrow("Import inventory");
  const file = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nGD0003,Fryer,", "upload");
  await expect(restockInventory({ schema: "hackathon.sources.v1", files: [file] }, 1, "receipt-3")).rejects.toThrow("valid stock count");
});
it("sends the changed inventory observation to the existing private backend", async () => {
  const fetch = vi.fn(async (request: Request) => {
    expect(new URL(request.url).pathname).toBe("/api/v1/ingest/files");
    const files = (await request.formData()).getAll("files") as File[];
    expect(files).toHaveLength(1);
    expect(await files[0]!.text()).toBe("csv-data");
    return Response.json({ data: { batches: [{ status: "loaded", rows_held: 0 }] } });
  });
  expect(await syncRestock("csv-data", { DECISION_BACKEND: { fetch } })).toBe("synced");
  expect(await syncRestock("csv-data", {})).toBe("unavailable");
  fetch.mockImplementationOnce(async () => Response.json({ data: { batches: [{ status: "needs_sku_review", rows_held: 1 }] } }));
  await expect(syncRestock("csv-data", { DECISION_BACKEND: { fetch } })).rejects.toThrow("mapping needs review");
});
