import { z } from "zod";
import { plannerBackend, BackendFailure, type PlannerEnv } from "./sale-planner";
import { getProductLabelProvider } from "./product-label-provider";
import type { StudioModelEnv } from "./model-json";
import { labelRunSchema } from "../src/studio/product-labels";
import { snapshotInput } from "./product-labels";
import { sourceText } from "../src/studio/source-text";
import { hashText, withoutDemandLabels, type SourcePack } from "../src/studio/sources";

type Storage = { fetch(request: Request): Promise<Response> };
const snapshotSchema = z.object({ hour: z.number().min(0).max(24).optional(), sources: z.object({ revision: z.number().int(), data: z.unknown() }), run: labelRunSchema.nullable() });

/** Read the saved snapshot server-side: the browser cannot supply a SKU mapping or AI result. */
export async function syncDemandLabels(request: Request, env: PlannerEnv & StudioModelEnv, storage: Storage): Promise<Response> {
  if (!["GET", "POST"].includes(request.method)) return Response.json({ error: { message: "Method not allowed." } }, { status: 405 });
  const api = plannerBackend(env, request.signal);
  if (!api) return Response.json({ status: "unconfigured", message: "Labels are saved in Warehouse. Connect the calculation backend to synchronize them." });
  try {
    const read = async () => {
      const response = await storage.fetch(new Request(new URL("/internal/product-labels/snapshot", request.url)));
      if (!response.ok) throw new Error("Snapshot unavailable");
      return snapshotSchema.parse(await response.json());
    };
    const snapshot = await read();
    const pack = snapshot.sources.data as SourcePack;
    const input = await snapshotInput({ ...snapshot, sources: { ...snapshot.sources, data: pack } });
    const run = snapshot.run;
    const provider = getProductLabelProvider(env);
    if (!run?.context || !run.completedAt || !["completed", "partial"].includes(run.status) ||
      run.sourceRevision !== input.sourceRevision || run.sourceFingerprint !== input.sourceFingerprint ||
      (provider && run.provider !== provider.id && !run.results.every(result => result.manual)))
      return Response.json({ status: "stale", message: "Save the latest sources and label them before synchronizing." });
    const replayDates = [...new Set(pack.files.flatMap(file => file.replay ? [file.replay.date] : []))];
    if (replayDates.length && (replayDates.length !== 1 || run.context.eventHour === undefined || run.context.eventHour > (snapshot.hour ?? 24)))
      return Response.json({ status: "stale", message: "Review labels at the current replay hour before synchronizing; later observations cannot be used earlier." });
    const evidenceThrough = new Date(Date.parse(`${replayDates[0] ?? run.context.asOf}T00:00:00+07:00`) +
      (replayDates.length ? Math.min(24, run.context.eventHour! + 1) : 24) * 3600000).toISOString();
    const products = new Map(input.products.map(product => [product.id, product]));
    const labels = run.results.filter(result => result.group && products.has(result.productId)).map(result => ({
      raw_code: products.get(result.productId)!.reference, name: products.get(result.productId)!.name,
      label: result.group, reason: result.reason, confidence: result.confidence ?? null, manual: result.manual ?? false,
    }));
    if (!labels.length) return Response.json({ status: "empty", message: "No current labels to synchronize." });
    const inventory = (await withoutDemandLabels(pack)).files.find(file => file.kind === "inventory");
    if (!inventory) throw Error("Inventory missing");
    if (request.method === "GET") {
      const result = await api("demand-labels");
      const data = z.object({ version: z.literal(1), labels: z.record(z.string(), z.object({
        run_id: z.string(), raw_code: z.string(), source_fingerprint: z.string(),
      })) }).parse(result.data);
      const synced = new Set(Object.values(data.labels).filter(row => row.run_id === run.id && row.source_fingerprint === run.sourceFingerprint).map(row => row.raw_code));
      const count = labels.filter(label => synced.has(label.raw_code)).length;
      return Response.json({ status: count === labels.length ? "synced" : "pending", synced: count, total: labels.length,
        message: `${count}/${labels.length} labels synchronized with canonical backend SKUs.` });
    }
    const result = await api("demand-labels/sync", {
      run_id: run.id, source_revision: run.sourceRevision, source_fingerprint: run.sourceFingerprint,
      inventory_sha256: await hashText(sourceText(inventory)), provider: run.provider,
      market: run.context.market, assessed_on: run.context.asOf, completed_at: run.completedAt,
      evidence_through: evidenceThrough, labels,
    });
    const data = z.object({ version: z.literal(1), run_id: z.literal(run.id),
      accepted: z.array(z.object({ raw_code: z.string(), sku_id: z.string(), entry_id: z.string() })),
      pending: z.array(z.object({ raw_code: z.string(), reason: z.string() })),
    }).parse(result.data);
    const latest = await read();
    if (latest.sources.revision !== snapshot.sources.revision || latest.run?.id !== run.id)
      return Response.json({ status: "stale", message: "Sources changed during synchronization. Send the latest data and synchronize again." }, { status: 409 });
    return Response.json({ status: data.pending.length ? "pending" : "synced", synced: data.accepted.length, total: labels.length,
      pending: data.pending, message: `${data.accepted.length}/${labels.length} labels synchronized. ${data.pending.length ? "Confirm pending SKU mappings in the backend and retry." : "Forecasts will use the shared labels with data-quality safeguards."}` });
  } catch (error) {
    return Response.json({ error: { message: error instanceof BackendFailure ? error.message : "Could not confirm label synchronization. Saved labels are retained; retry safely." } },
      { status: error instanceof BackendFailure ? error.status : 502 });
  }
}
