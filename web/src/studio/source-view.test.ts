import { expect, it } from "vitest";
import { makeSource, type SourcePack } from "./sources";
import { sourceView } from "./source-view";
it("shows latest uploaded inventory regardless of demo clock, while retaining demo playback", async () => {
  const source = await makeSource("inventory.csv", "Mã nội bộ,Tên hàng,Tồn thực tế,Thời điểm cập nhật\nA,Cup,17,2026-10-31 18:00\nA,Cup,9,2026-10-31 20:00\n,Missing code,100,2026-10-31 20:00", "upload");
  const pack: SourcePack = { schema: "hackathon.sources.v1", files: [source] };
  expect(sourceView(pack, 1).stock).toEqual([{ code: "A", name: "Cup", stock: 9, asOf: "2026-10-31 20:00" }]);
  expect(sourceView({ ...pack, files: [{ ...source, commit: "a".repeat(40) }] }, 1).stock).toEqual([]);
});
it("reads recognized header variants and trims internal codes consistently", async () => {
  const source = await makeSource("inventory.csv", "MA NOI BO,TEN HANG,TON THUC TE\n A ,Cup,17", "upload");
  expect(sourceView({ schema: "hackathon.sources.v1", files: [source] }, 0).stock)
    .toEqual([{ code: "A", name: "Cup", stock: 17, asOf: "" }]);
});
