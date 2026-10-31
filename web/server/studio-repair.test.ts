// @vitest-environment node
import { expect, it } from "vitest";
import { inspect, makeSource } from "../src/studio/sources";
import { repairSource } from "./studio-repair";

it("repairs known headers, whitespace and VND formatting without inventing missing values", async () => {
  const source = await makeSource("cost.csv", ' tên sp , GIÁ VỐN ,phí đóng gói\n Cup ,45k,3.000đ\nLamp,,USD5\nFaulty,-3k,', "upload");
  const fixed = await repairSource(source);
  expect(inspect(fixed.source.name, fixed.source.text).rows).toEqual([
    { "tên sp": "Cup", "giá vốn": "45000", "phí đóng gói": "3000" },
    { "tên sp": "Lamp", "giá vốn": "", "phí đóng gói": "USD5" },
    { "tên sp": "Faulty", "giá vốn": "-3k", "phí đóng gói": "" },
  ]);
  expect(fixed.repairs).toHaveLength(3);
  expect((await repairSource(fixed.source)).repairs).toEqual([]);
  expect(source.text).toContain("45k");
});
it("preserves unknown JSON data and does not guess timezones or stock", async () => {
  const json = await makeSource("notes.json", '[{"note":" keep spacing ","amount":"USD5"}]', "upload");
  expect((await repairSource(json)).source).toEqual(json);
  const stock = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế,Thời điểm cập nhật\nA,Cup,,2026-11-11 00:00\nB,Lamp,-2,2026-11-11 01:00", "upload");
  expect((await repairSource(stock)).source).toEqual(stock);
});
