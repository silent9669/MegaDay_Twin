import Papa from "papaparse";
import { inspect, makeSource, normalize, SOURCE_DEFS, type Source } from "../src/studio/sources";
import { previewVnd } from "../src/studio/money-preview";

const moneyColumns = ["giá vốn", "phí đóng gói", "Chi phí", "Tổng tiền hàng", "Giá gốc", "Giá ưu đãi"];
/** Apply only contract-defined, deterministic repairs. Never fill missing business facts. */
export async function repairSource(source: Source) {
  const parsed = inspect(source.name, source.text);
  const repairs: string[] = [];
  if (source.kind === "general" || parsed.payload !== undefined) return { source, repairs };
  const canonical = [...(SOURCE_DEFS.find(def => def.kind === source.kind)?.required ?? []),
    "Tên sản phẩm", "Thời điểm cập nhật", ...moneyColumns];
  const columns = parsed.columns.map(column => canonical.find(name => normalize(name) === normalize(column)) ?? column);
  if (new Set(columns).size !== columns.length) throw Error("Column names collide after normalization. Resolve them before AI repair.");
  const renamed = columns.filter((column, index) => column !== parsed.columns[index]).length;
  let trimmed = 0, amounts = 0;
  const rows = parsed.rows.map(row => Object.fromEntries(parsed.columns.map((column, index) => {
    let value = row[column];
    if (typeof value === "string") {
      if (value !== value.trim()) { value = value.trim(); trimmed++; }
      if (moneyColumns.includes(columns[index]!) && value && !/^\d+$/.test(value as string)) {
        const amount = previewVnd(value as string);
        if (amount !== null && amount >= 0) { value = String(amount); amounts++; }
      }
    }
    return [columns[index]!, value];
  })));
  if (renamed) repairs.push(`Standardized ${renamed} column names.`);
  if (trimmed) repairs.push(`Trimmed surrounding whitespace in ${trimmed} cells.`);
  if (amounts) repairs.push(`Normalized ${amounts} VND amounts using the existing money contract.`);
  if (!repairs.length) return { source, repairs };
  const repaired = await makeSource(source.name, Papa.unparse({ fields: columns, data: rows }), source.origin);
  return { source: { ...source, text: repaired.text, hash: repaired.hash }, repairs };
}
