import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { expect, it } from "vitest";
import { SourceWorkspace } from "./SourceWorkspace";
import { makeSource, type SourcePack } from "./sources";
it("uses all uploaded orders in HQ and searchable neighborhood records without demo clock filtering", async () => {
  const pack: SourcePack = { schema: "hackathon.sources.v1", files: [
    await makeSource("orders.csv", "Mã đơn hàng,SKU phân loại hàng,Số lượng,Ngày đặt hàng,Tên sản phẩm\nA,SKU1,2,2026-11-11 23:00,Cup\nB,SKU2,1,2026-11-12 00:00,Lamp", "upload"),
    await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nP1,Cup,2\nP2,Lamp,0", "upload"),
  ] };
  const view = render(<MemoryRouter><SourceWorkspace pack={pack} district="hq" /></MemoryRouter>);
  expect(screen.getByText("Order records").parentElement).toHaveTextContent("2");
  expect(screen.getByText("Products in stock").parentElement).toHaveTextContent("1");
  view.rerender(<MemoryRouter><SourceWorkspace pack={pack} district="residential" /></MemoryRouter>);
  fireEvent.change(screen.getByLabelText("Search orders"), { target: { value: "Cup" } });
  expect(screen.getByText("Cup")).toBeInTheDocument();
  expect(screen.queryByText("Lamp")).not.toBeInTheDocument();
});
