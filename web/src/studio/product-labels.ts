import { z } from "zod";
import { hashText, inspect, normalize, type SourceKind, type SourcePack } from "./sources";

export const DEMAND_LABELS = {
  moi: "Mới",
  ban_thua: "Bán thưa",
  mua_vu: "Mùa vụ",
  trend: "Trend",
  on_dinh: "Ổn định",
} as const;
export type DemandLabel = keyof typeof DEMAND_LABELS;
export const MAX_LABEL_PRODUCTS = 2000;

/** Source-scoped candidates, not confirmed canonical SKU matches. */
export type LabelProduct = {
  id: string;
  name: string;
  alternateNames: string[];
  source: SourceKind;
  identity: "platform_sku" | "internal_code" | "name_only";
  reference: string;
  rowCount: number;
};
export type LabelInput = {
  sourceRevision: number;
  sourceFingerprint: string;
  products: LabelProduct[];
};
export const productLabelSchema = z.object({
  productId: z.string().min(1).max(2000),
  group: z.enum(["moi", "ban_thua", "mua_vu", "trend", "on_dinh"]).nullable(),
  reason: z.string().trim().min(1).max(1000),
  confidence: z.number().min(0).max(1).optional(),
}).strict();
export type ProductLabel = z.infer<typeof productLabelSchema>;
export const labelProviderResponseSchema = z.object({
  labels: z.array(productLabelSchema).max(MAX_LABEL_PRODUCTS),
}).strict();
export const labelRunSchema = z.object({
  id: z.string().uuid(),
  sourceRevision: z.number().int().nonnegative(),
  sourceFingerprint: z.string().regex(/^[a-f0-9]{64}$/),
  provider: z.string().min(1).max(150),
  status: z.enum(["running", "completed", "partial", "failed"]),
  startedAt: z.string().datetime(),
  completedAt: z.string().datetime().nullable(),
  results: z.array(productLabelSchema).max(MAX_LABEL_PRODUCTS),
  error: z.string().max(1000).nullable(),
}).strict();
export type LabelRun = z.infer<typeof labelRunSchema>;
export const productLabelArchiveSchema = z.object({ run: labelRunSchema.nullable() }).strict();
export type ProductLabelArchive = z.infer<typeof productLabelArchiveSchema>;
export type LabelResponse = LabelInput & {
  configured: boolean;
  provider: string | null;
  run: LabelRun | null;
  stale: boolean;
};

export function validateLabelResponse(raw: unknown, input: LabelInput): ProductLabel[] {
  const { labels } = labelProviderResponseSchema.parse(raw);
  const expected = new Set(input.products.map((product) => product.id));
  const seen = new Set<string>();
  for (const label of labels) {
    if (!expected.has(label.productId)) throw Error("Provider returned an unknown product ID.");
    if (seen.has(label.productId)) throw Error("Provider returned duplicate product IDs.");
    seen.add(label.productId);
  }
  return labels;
}

export function extractLabelProducts(pack: SourcePack): LabelProduct[] {
  const products = new Map<string, LabelProduct>();
  for (const source of pack.files) {
    if (!["orders", "inventory", "cost"].includes(source.kind)) continue;
    const parsed = inspect(source.name, source.text);
    const field = (name: string) => parsed.columns.find((column) => normalize(column) === normalize(name));
    const nameColumn = field(source.kind === "orders" ? "Tên sản phẩm" : source.kind === "inventory" ? "Tên hàng" : "tên sp");
    const codeColumn = field(source.kind === "orders" ? "SKU phân loại hàng" : "Mã nội bộ");
    if (!nameColumn) continue;
    for (const row of parsed.rows) {
      const name = String(row[nameColumn] ?? "").trim();
      if (!name) continue;
      const code = codeColumn ? String(row[codeColumn] ?? "").trim() : "";
      const identity = code ? source.kind === "orders" ? "platform_sku" : "internal_code" : "name_only";
      const reference = code || name;
      if (name.length > 300 || reference.length > 300)
        throw Error("A product name or identifier exceeds 300 characters. Review the source before labeling.");
      // Same names across different source systems are not evidence of SKU identity.
      const id = JSON.stringify([source.kind, identity, reference]);
      const existing = products.get(id);
      if (existing) {
        existing.rowCount++;
        if (name !== existing.name && !existing.alternateNames.includes(name)) {
          if (existing.alternateNames.length >= 20)
            throw Error("One product identifier has more than 20 names. Resolve its identity before labeling.");
          existing.alternateNames.push(name);
        }
      } else {
        if (products.size >= MAX_LABEL_PRODUCTS)
          throw Error(`At most ${MAX_LABEL_PRODUCTS} product candidates can be labeled per workspace. Split the source set before labeling.`);
        products.set(id, { id, name, alternateNames: [], source: source.kind, identity, reference, rowCount: 1 });
      }
    }
  }
  return [...products.values()].sort((a, b) => a.id.localeCompare(b.id));
}

export async function sourceFingerprint(pack: SourcePack): Promise<string> {
  return hashText(JSON.stringify(pack.files.map((source) => [source.kind, source.hash]).sort(([a], [b]) => a!.localeCompare(b!))));
}
export async function buildLabelInput(pack: SourcePack, sourceRevision: number): Promise<LabelInput> {
  return { sourceRevision, sourceFingerprint: await sourceFingerprint(pack), products: extractLabelProducts(pack) };
}
