import { expect, it } from "vitest";
import { makeSource } from "./sources";
import { sourceView } from "./source-view";

it("keeps all historical observations regardless of repository provenance or playback hour", async () => {
  const orders = await makeSource("orders.csv", "Mã đơn hàng,Ngày đặt hàng,SKU phân loại hàng,Số lượng\nA,2026-11-09 18:00,GD0003,1\nB,2026-11-10 22:00,GD0003,1", "GitHub", "a".repeat(40));
  const inventory = await makeSource("stock.csv", "Thời điểm cập nhật,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-10 23:00,GD0003,Fryer,83", "GitHub", "a".repeat(40));
  const view = sourceView({ schema: "hackathon.sources.v1", files: [orders, inventory] }, 1);
  expect(view.orderRows).toBe(2);
  expect(view.stock[0]?.stock).toBe(83);
});

it("still excludes future replay rows using explicit playback metadata", async () => {
  const source = await makeSource("stock.csv", "Thời điểm cập nhật,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-11 01:00,GD0003,Fryer,80\n2026-11-11 10:00,GD0003,Fryer,3", "event");
  source.replay = { date: "2026-11-11", hours: [1, 10], lagHours: 0 };
  const pack = { schema: "hackathon.sources.v1" as const, files: [source] };
  expect(sourceView(pack, 1).stock[0]?.stock).toBe(80);
});

it("shows the latest uploaded observation and omits rows without a product code", async () => {
  const source = await makeSource("inventory.csv", "Mã nội bộ,Tên hàng,Tồn thực tế,Thời điểm cập nhật\nA,Cup,17,2026-10-31 18:00\nA,Cup,9,2026-10-31 20:00\n,Missing code,100,2026-10-31 20:00", "upload");
  expect(sourceView({ schema: "hackathon.sources.v1", files: [source] }, 1).stock)
    .toEqual([{ code: "A", name: "Cup", stock: 9, asOf: "2026-10-31 20:00" }]);
});

it("reads recognized header variants and trims internal codes consistently", async () => {
  const source = await makeSource("inventory.csv", "MA NOI BO,TEN HANG,TON THUC TE\n A ,Cup,17", "upload");
  expect(sourceView({ schema: "hackathon.sources.v1", files: [source] }, 0).stock)
    .toEqual([{ code: "A", name: "Cup", stock: 17, asOf: "" }]);
});
