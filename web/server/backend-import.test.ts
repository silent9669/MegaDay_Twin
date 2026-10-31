// @vitest-environment node
import { expect, it } from "vitest";
import { backendImport } from "./backend-import";
import { makeSource } from "../src/studio/sources";
import { mergeReplaySources } from "../src/studio/replay";

it("streams shop CSV with correct quoting and excludes replay and derived demand labels", async () => {
  const file = await makeSource("stock.csv", 'Mã nội bộ,Tên hàng,Tồn thực tế,Demand label\nA,"Cup, large",20,Trendy\nA,Cup,5,Normal', "upload");
  file.replay = { date: "2026-11-11", hours: [-1, 4], lagHours: 0 };
  const form = backendImport([file]);
  const data = await new Response(form.body, { headers: { "Content-Type": form.contentType } }).formData();
  const text = await (data.get("files") as File).text();
  expect(text).toContain('A,"Cup, large",20');
  expect(text).not.toContain('A,Cup,5');
  expect(text).not.toContain('Demand label');
  expect((data.get("files") as File).name).toBe("ton_kho_google_sheet.csv");
});

it("does not make daily baseline inventory look like invalid hourly observations after merging replay", async () => {
  const shop = await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-10,GD0003,Fryer,83", "shop");
  const event = { ...await makeSource("stock.csv", "Thời điểm cập nhật,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-11 10:00,GD0003,Fryer,10", "event"),
    replay: { date: "2026-11-11", hours: [10], lagHours: 0 } };
  const upload = backendImport(await mergeReplaySources([shop], [event]));
  const parts = await new Response(upload.body, { headers: { "Content-Type": upload.contentType } }).formData();
  const text = await (parts.get("files") as File).text();
  expect(text).toContain("2026-11-10");
  expect(text).not.toContain("2026-11-11 10:00");
  expect(text).not.toContain("Thời điểm cập nhật");
});
