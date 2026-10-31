import { describe, expect, it } from "vitest";
import { modelSuggestion, suggestedWorksheet, workspaceSuggestion } from "./worksheet-suggestions";
import type { SourcePack } from "../studio/sources";
import { worksheetResults } from "./types";

const pack = (text: string): SourcePack => ({ schema: "hackathon.sources.v1", files: [
  { kind: "general", name: "sku-facts.csv", text, hash: text, origin: "upload" },
  { kind: "inventory", name: "inventory.csv", text: "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Mug,12", hash: "inventory", origin: "upload" },
] });
describe("product comparison suggestions", () => {
  it("uses exact SKU facts and localized money without inventing demand or shared spend", () => {
    const evidence = workspaceSuggestion(pack("sku,price_effective_vnd,unit_cost_vnd,packaging_cost_vnd,fee_percent\nA,600000,540000,3k,2\nB,10,5,1,0"), "A", { code: "A", name: "A", stock: 83, asOf: "2026-10-31" });
    expect(suggestedWorksheet(evidence)).toEqual({ stock: 83, price: 600000, cost: 540000, packaging: 3000, feePercent: 2, continueUnits: null, pauseUnits: null, spend: null });
    expect(evidence.cost?.source).toContain("sku-facts.csv · row 1");
    expect(worksheetResults(suggestedWorksheet(evidence))).toBeNull();
  });
  it("rejects conflicting, absent, invalid and name-only values; preserves explicit zero", () => {
    const evidence = workspaceSuggestion(pack("sku,price_effective_vnd,unit_cost_vnd,packaging_cost_vnd,fee_percent\nA,100,-1,,101\nA,200,-1,0,101"), "A");
    expect(evidence).toEqual({ packaging: { value: 0, source: expect.any(String) } });
    expect(workspaceSuggestion(pack("tên sp,giá vốn\nA,92000"), "A")).toEqual({});
    expect(workspaceSuggestion(pack("sku,unit_cost_vnd\nB,92000"), "A")).toEqual({});
  });
  it("accepts completed orders without confusing them with returns", () => {
    expect(workspaceSuggestion(pack("sku,price_effective_vnd,Trạng thái đơn hàng\nA,600000,Hoàn thành"), "A").price?.value).toBe(600000);
  });
  it("does not use cancelled orders or another product's stock", () => {
    expect(workspaceSuggestion(pack("sku,price_effective_vnd,Trạng thái đơn hàng\nA,600000,Đã hủy"), "A", { code: "B", name: "B", stock: 83, asOf: "today" })).toEqual({});
  });
  it("labels model snapshots and never derives fees or ad lift from contribution", () => {
    const evidence = modelSuggestion({ sku: "A", as_of: "2026-11-11", stock_available: 0, price_effective_vnd: 100, cost: { unit_cost_vnd: 30, packaging_cost_vnd: 0 } }, true);
    expect(evidence.stock).toEqual({ value: 0, source: "Model snapshot · A · 2026-11-11" });
    expect(evidence.feePercent).toBeUndefined();
    expect(evidence.continueUnits).toBeUndefined();
  });
});
