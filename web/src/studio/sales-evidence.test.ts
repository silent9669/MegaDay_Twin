import { expect, it } from "vitest";
import { makeSource } from "./sources";
import { buildLabelInput } from "./product-labels";
it("uses historical orders only for unique name + variant matches, with cancellations and future days excluded", async () => {
  const stock = await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-10,A,Cốc in hình meme - mẫu a,10\n2026-11-10,B,Cup - blue,1\n2026-11-10,C,Cup - blue,1", "baseline");
  const orders = await makeSource("orders.csv", "Mã đơn hàng,Ngày đặt hàng,SKU phân loại hàng,Tên sản phẩm,Tên phân loại hàng,Số lượng,Trạng thái đơn hàng\nO1,2026-11-01,MEMEA,Cốc in hình meme,Mẫu A,2,Complete\nO2,2026-11-10,MEMEA,Cốc in hình meme,Mẫu A,8,Complete\nO3,2026-11-10,MEMEA,Cốc in hình meme,Mẫu A,99,Đã hủy\nO4,2026-11-11,MEMEA,Cốc in hình meme,Mẫu A,999,Complete\nO5,2026-11-10,CUPBLUE,Cup,blue,9,Complete", "baseline");
  const input = await buildLabelInput({ schema: "hackathon.sources.v1", files: [stock, orders] }, 1);
  expect(input.context?.asOf).toBe("2026-11-10");
  expect(input.products[0]?.salesEvidence).toMatchObject({ dailyUnits: [["2026-11-01", 2], ["2026-11-10", 8]], excludedCancelled: 1 });
  expect(input.products.slice(1).every(product => !product.salesEvidence)).toBe(true);
});
