// @vitest-environment node
import { expect, it } from "vitest";
import { applyModelCorrections } from "./studio-corrections";
import { inspect, makeSource } from "../src/studio/sources";

it("applies a model cell correction while retaining the other rows and columns", async () => {
  const source = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cupp,2\nB,Lamp,3", "upload");
  const result = await applyModelCorrections(source, [{ row: 0, column: "Tên hàng", before: "Cupp", after: "Cup", reason: "Corrected a product-name typo from supplied evidence." }]);
  expect(inspect(result.source.name, result.source.text).rows).toEqual([
    { "Mã nội bộ": "A", "Tên hàng": "Cup", "Tồn thực tế": "2" },
    { "Mã nội bộ": "B", "Tên hàng": "Lamp", "Tồn thực tế": "3" },
  ]);
  expect(result.repairs[0]).toContain("row 1");
  expect(source.text).toContain("Cupp");
});
it("rejects stale or invalid corrections instead of silently changing a different cell", async () => {
  const source = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,2", "upload");
  const correction = { row: 0, column: "Tên hàng", before: "Wrong", after: "Cup", reason: "Fix" };
  await expect(applyModelCorrections(source, [correction])).rejects.toThrow(/original/);
  await expect(applyModelCorrections(source, [{ ...correction, row: 2 }])).rejects.toThrow(/cell/);
  await expect(applyModelCorrections(source, [{ ...correction, before: "Cup", after: "" }])).rejects.toThrow(/clear a cell/);
  await expect(applyModelCorrections(source, [{ ...correction, column: "Mã nội bộ", before: "A", after: "B" }])).rejects.toThrow(/identity/);
  await expect(applyModelCorrections(source, [{ ...correction, column: "Tồn thực tế", before: "2", after: "-2" }])).rejects.toThrow(/new data errors/);
});
it("preserves structured JSON and refuses scalar edits to nested values", async () => {
  const source = await makeSource("notes.json", '[{"name":"Cupp","tags":["A"]}]', "upload");
  const result = await applyModelCorrections(source, [{ row: 0, column: "name", before: "Cupp", after: "Cup", reason: "Typo" }]);
  expect(JSON.parse(result.source.text)).toEqual([{ name: "Cup", tags: ["A"] }]);
  await expect(applyModelCorrections(source, [{ row: 0, column: "tags", before: "A", after: "B", reason: "Change" }])).rejects.toThrow(/scalar/);
});
