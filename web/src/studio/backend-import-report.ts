import { z } from "zod";

const count = z.number().int().nonnegative();
const reportSchema = z.object({ data: z.object({
  batches: z.array(z.object({
    file_name: z.string(),
    status: z.enum(["loaded", "skipped", "needs_sku_review", "failed", "unsupported"]),
    batch_status: z.string().nullable(),
    rows_held: count,
    issues: z.record(z.string(), count),
  })).min(1),
  pending_aliases: count,
}) });

/** HTTP success only confirms receipt; each file has its own ingestion outcome. */
export function backendImportReport(value: unknown) {
  const parsed = reportSchema.safeParse(value);
  if (!parsed.success) throw Error("Cannot confirm the import result. Check the downloaded report before retrying.");
  const { batches, pending_aliases: pending } = parsed.data.data;
  const loaded = batches.filter(batch => batch.status === "loaded").length;
  const skipped = batches.filter(batch => batch.status === "skipped").length;
  const held = batches.reduce((sum, batch) => sum + batch.rows_held, 0);
  const failed = batches.filter(batch => ["failed", "unsupported"].includes(batch.status) || batch.batch_status === "failed");
  const review = batches.some(batch => batch.status === "needs_sku_review" || batch.batch_status === "ok_with_issues" || Object.values(batch.issues).some(n => n > 0));
  const needsAttention = !!(failed.length || held || pending || review);
  const blocked = !!(failed.length || held || pending || batches.some(batch =>
    batch.status === "needs_sku_review" || (batch.issues.ROW_INVALID ?? 0) > 0 || (batch.issues.MISSING_COLUMNS ?? 0) > 0));
  const details = [
    `${loaded} file(s) loaded; ${skipped} already imported.`,
    failed.length ? `Failed or unsupported: ${failed.map(batch => batch.file_name).join(", ")}.` : "",
    held ? `${held} row(s) held for SKU mapping.` : "",
    pending ? `${pending} SKU mapping(s) need confirmation.` : "",
    review ? "Some files have data issues; review the downloaded report." : "",
  ].filter(Boolean).join(" ");
  return { canSyncLabels: !blocked,
    message: `${needsAttention ? "Import needs attention." : "Import confirmed."} ${details}${blocked ? " Review failed files and SKU mappings before synchronizing labels." : ""}` };
}
