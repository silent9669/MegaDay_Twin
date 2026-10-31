import type { SkuDetail } from "../api/types";
import { readSource } from "../studio/read-source";
import { previewVnd } from "../studio/money-preview";
import { normalize, type SourcePack } from "../studio/sources";
import type { StockRow } from "../studio/source-view";
import { blankWorksheet, type Worksheet } from "./types";

export type WorksheetEvidence = Partial<Record<keyof Worksheet, { value: number; source: string }>>;
const skuColumns = new Set(["ma noi bo", "sku", "sku_id", "sku id", "ma sku", "sku phan loai hang", "seller sku", "merchant sku"]);
const columns = {
  price: ["gia uu dai", "selling price", "price_effective_vnd"],
  cost: ["gia von", "unit cost", "unit_cost_vnd"],
  packaging: ["phi dong goi", "packaging_cost_vnd"],
  feePercent: ["platform fee (%)", "fee_percent"],
} as const;
function valid(value: unknown, max: number): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= max;
}

/** Only exact SKU facts. Historical sales and shared campaign spend are not counterfactual forecasts. */
export function workspaceSuggestion(pack: SourcePack | null, sku: string, stock?: StockRow): WorksheetEvidence {
  const evidence: WorksheetEvidence = {};
  const inventory = pack?.files.find(file => file.kind === "inventory");
  if (inventory && stock?.code === sku && valid(stock.stock, 1e9)) {
    evidence.stock = { value: stock.stock, source: `${inventory.name} · ${sku} · ${stock.asOf || "date unavailable"}` };
  }
  for (const [key, aliases] of Object.entries(columns) as [keyof typeof columns, readonly string[]][]) {
    const candidates: { value: number; source: string }[] = [];
    for (const file of pack?.files ?? []) {
      if (key === "price" && !["orders", "general"].includes(file.kind)) continue;
      if ((key === "cost" || key === "packaging") && !["cost", "general"].includes(file.kind)) continue;
      const parsed = readSource(file);
      const codes = parsed.columns.filter(column => skuColumns.has(normalize(column)));
      const column = parsed.columns.find(column => aliases.includes(normalize(column)));
      if (!column || !codes.length) continue;
      parsed.rows.forEach((row, index) => {
        if (!codes.some(code => String(row[code] ?? "").trim() === sku)) return;
        const status = parsed.columns.find(c => normalize(c) === "trang thai don hang");
        if (status && /huy|cancel|hoan tra|hoan tien|tra hang|return/.test(normalize(String(row[status] ?? "")))) return;
        const raw = String(row[column] ?? "").trim();
        const value = !raw ? null : key === "feePercent" ? Number(raw) : previewVnd(raw);
        if (valid(value, key === "feePercent" ? 100 : 1e12))
          candidates.push({ value, source: `${file.name} · row ${index + 1} · ${column}` });
      });
    }
    // Conflicting prices/costs need review; never select an arbitrary row.
    if (candidates.length && new Set(candidates.map(c => c.value)).size === 1) {
      evidence[key] = { ...candidates[0]!, source: candidates[0]!.source + (candidates.length > 1 ? ` · ${candidates.length} agreeing records` : "") };
    }
  }
  return evidence;
}

export function modelSuggestion(item: Pick<SkuDetail, "sku" | "as_of" | "stock_available" | "price_effective_vnd" | "cost"> | undefined, snapshot: boolean): WorksheetEvidence {
  if (!item) return {};
  const source = `${snapshot ? "Model snapshot" : "Decision backend"} · ${item.sku} · ${item.as_of || "date unavailable"}`;
  const evidence: WorksheetEvidence = {};
  const values = { stock: item.stock_available, price: item.price_effective_vnd, cost: item.cost?.unit_cost_vnd, packaging: item.cost?.packaging_cost_vnd };
  for (const [key, value] of Object.entries(values) as [keyof typeof values, unknown][])
    if (valid(value, key === "stock" ? 1e9 : 1e12)) evidence[key] = { value, source };
  return evidence;
}

export function suggestedWorksheet(evidence: WorksheetEvidence): Worksheet {
  const value = { ...blankWorksheet };
  for (const [key, fact] of Object.entries(evidence)) value[key as keyof Worksheet] = fact.value;
  return value;
}

/** Fill missing facts without replacing saved assumptions or fields explicitly cleared in this session. */
export function autofillWorksheet(saved: Worksheet, evidence: WorksheetEvidence, cleared: Partial<Record<keyof Worksheet, boolean>> = {}): Worksheet {
  const value = { ...saved };
  for (const [key, fact] of Object.entries(evidence)) {
    const field = key as keyof Worksheet;
    if (value[field] === null && !cleared[field]) value[field] = fact.value;
  }
  return value;
}
