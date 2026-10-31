// @vitest-environment node
import { expect, it } from "vitest";
import { workspaceSummary } from "./workspace-summary";
import { makeSource } from "../src/studio/sources";

it("includes historical evening observations from GitHub even at playback hour one", async () => {
  const inventory = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế,Thời điểm cập nhật\nA,Mug,20,2026-11-10 23:00", "GitHub", "a".repeat(40));
  const orders = await makeSource("orders.csv", "Mã đơn hàng,SKU phân loại hàng,Số lượng,Ngày đặt hàng\n1,A,2,2026-11-09 18:00\n2,A,1,2026-11-10 22:00", "GitHub", "a".repeat(40));
  const result = workspaceSummary({ revision: 3, updatedAt: "now", data: { schema: "hackathon.sources.v1", files: [inventory, orders] } },
    { revision: 2, updatedAt: "now", data: { hour: 1, event: "observe" } }, "A");
  expect(result.stock[0]?.stock).toBe(20);
  expect(result.orders).toBe(2);
});

it("keeps exact selected stock and visible order counts without retaining other products", async () => {
  const inventory = await makeSource("inventory.csv", "Mã nội bộ,Tên hàng,Tồn thực tế,Ngày\nA,Mug,20,2026-11-10\nB,Other,99,2026-11-10\nA,Mug,-1,2026-11-11\nA,Mug,7,2026-11-12", "upload");
  inventory.replay = { date: "2026-11-11", hours: [-1, -1, 1, 23], lagHours: 0 };
  const orders = await makeSource("orders.csv", "Mã đơn hàng,SKU phân loại hàng,Số lượng,Ngày đặt hàng\n1,A,2,2026-11-10\n2,A,1,2026-11-11\n3,A,3,2026-11-12", "upload");
  orders.replay = { date: "2026-11-11", hours: [-1, 1, 23], lagHours: 1 };
  const result = workspaceSummary({ revision: 3, updatedAt: "now", data: { schema: "hackathon.sources.v1", files: [inventory, orders] } },
    { revision: 2, updatedAt: "now", data: { hour: 1, event: "observe" } }, "A");
  expect(result.stock).toEqual([{ code: "A", name: "Mug", stock: null, asOf: "2026-11-11" }]);
  expect(result.orders).toBe(1);
  expect(result.sources[0]).toMatchObject({ diagnosticsChecked: false, issueCount: null });
});
