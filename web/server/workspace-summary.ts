import { scanSource } from "./analysis-tools";
import { normalize, type SourcePack } from "../src/studio/sources";
import { playbackHour } from "../src/studio/replay";
import type { StockRow } from "../src/studio/source-view";
import type { Doc, Sandbox } from "../src/studio/workspace-types";

/** Bounded server context: never retain parsed order/advertising tables. */
export function workspaceSummary(sources: Doc<SourcePack>, sandbox: Doc<Sandbox>, sku?: string) {
  const hour = playbackHour(sandbox.data);
  let orders = 0, campaigns = 0;
  let stock: StockRow | null = null;
  for (const file of sources.data.files) {
    if (!["inventory", "orders", "campaigns"].includes(file.kind)) continue;
    if (file.kind === "inventory" && !sku) continue;
    let columns: Record<string, string> | undefined;
    scanSource(file, (row, index) => {
      if (file.replay && file.replay.hours[index]! >= 0 && file.replay.hours[index]! > hour - file.replay.lagHours) return;
      if (file.kind === "orders") {
        orders++;
      } else if (file.kind === "campaigns") campaigns++;
      else {
        columns ??= Object.fromEntries(Object.keys(row).map(key => [normalize(key), key]));
        const value = (key: string) => row[columns![normalize(key)] ?? key];
        const code = String(value("Mã nội bộ") ?? "").trim();
        if (code !== sku) return;
        const asOf = String(value("Thời điểm cập nhật") || value("Ngày") || "");
        if (stock && stock.asOf > asOf) return;
        const raw = String(value("Tồn thực tế") ?? "").trim(), amount = raw ? Number(raw) : NaN;
        stock = { code, name: String(value("Tên hàng") ?? code), asOf,
          stock: Number.isFinite(amount) && amount >= 0 ? amount : null };
      }
    });
  }
  return { sourceRevision: sources.revision, sandboxRevision: sandbox.revision, hour,
    stock: stock ? [stock as StockRow] : [], orders, campaigns,
    sources: sources.data.files.map(file => ({ kind: file.kind, name: file.name, hash: file.hash,
      issueCount: null, issues: [] as string[], diagnosticsChecked: false as const })),
  };
}
