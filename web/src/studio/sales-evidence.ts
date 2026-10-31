import { readSource } from "./read-source";
import { normalize, type SourcePack } from "./sources";
import type { LabelProduct } from "./product-labels";

/** Exact, unique name + variant association only; never guess an internal-to-platform SKU crosswalk. */
export function attachSalesEvidence(pack: SourcePack, products: LabelProduct[]) {
  const inventory = pack.files.find(file => file.kind === "inventory");
  const orders = pack.files.find(file => file.kind === "orders");
  if (!inventory || !orders) return;
  const identities = new Map<string, Set<string>>();
  for (const row of readSource(inventory).rows) {
    const name = normalize(String(row["Tên hàng"] ?? ""));
    const codes = identities.get(name) ?? new Set<string>();
    codes.add(String(row["Mã nội bộ"] ?? "").trim()); identities.set(name, codes);
  }
  const names = new Map(products.filter(p => identities.get(normalize(p.name))?.size === 1).map(p => [normalize(p.name), p]));
  const daily = new Map<string, Map<string, number>>();
  const skus = new Map<string, Set<string>>();
  const cancelled = new Map<string, number>();
  const latest = readSource(inventory).rows.reduce((last, row) => {
    const date = String(row["Thời điểm cập nhật"] || row["Ngày"] || "").slice(0, 10);
    return date > last ? date : last;
  }, "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(latest) || !Number.isFinite(Date.parse(latest))) return;
  const seen = new Set<string>();
  for (const row of readSource(orders).rows) {
    const name = String(row["Tên sản phẩm"] ?? "").trim(), variant = String(row["Tên phân loại hàng"] ?? "").trim();
    const product = names.get(normalize(variant ? `${name} - ${variant}` : name));
    if (!product) continue;
    const date = String(row["Ngày đặt hàng"] ?? "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || (latest && date > latest)) continue;
    const identity = JSON.stringify([row["Mã đơn hàng"], row["SKU phân loại hàng"], date, row["Số lượng"], row["Trạng thái đơn hàng"]]);
    if (seen.has(identity)) continue; seen.add(identity);
    const refs = skus.get(product.id) ?? new Set<string>();
    refs.add(String(row["SKU phân loại hàng"] ?? "")); skus.set(product.id, refs);
    if (/huy|cancel/.test(normalize(String(row["Trạng thái đơn hàng"] ?? "")))) {
      cancelled.set(product.id, (cancelled.get(product.id) ?? 0) + 1); continue;
    }
    const units = Number(row["Số lượng"]);
    if (!Number.isFinite(units) || units <= 0) continue;
    const days = daily.get(product.id) ?? new Map<string, number>();
    days.set(date, (days.get(date) ?? 0) + units); daily.set(product.id, days);
  }
  for (const product of products) {
    const days = daily.get(product.id);
    if (!days?.size || skus.get(product.id)?.size !== 1) continue;
    const entries = [...days.entries()].sort(([a], [b]) => a.localeCompare(b));
    const cutoff = new Date(Date.parse(latest + "T00:00:00Z") - 27 * 86400000).toISOString().slice(0, 10);
    const earlierWeeks = new Map<string, number>();
    for (const [date, units] of entries.filter(([date]) => date < cutoff)) {
      const day = new Date(date + "T00:00:00Z");
      day.setUTCDate(day.getUTCDate() - (day.getUTCDay() + 6) % 7);
      const week = day.toISOString().slice(0, 10);
      earlierWeeks.set(week, (earlierWeeks.get(week) ?? 0) + units);
    }
    product.salesEvidence = { match: "Unique exact product name + variant (not a confirmed SKU crosswalk)", firstDate: entries[0]![0], lastDate: entries.at(-1)![0], dailyUnits: entries.filter(([date]) => date >= cutoff), earlierWeeks: [...earlierWeeks], excludedCancelled: cancelled.get(product.id) ?? 0 };
  }
}
