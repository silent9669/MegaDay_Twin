import { fail } from "./auth";
import {
  labelContextSchema, buildLabelInput, validateLabelResponse, type LabelInput, type LabelResponse, type LabelRun,
} from "../src/studio/product-labels";
import type { Doc } from "../src/studio/workspace-types";
import type { SourcePack } from "../src/studio/sources";
import { LABEL_LEASE_MS, LABEL_TIMEOUT_MS, type ProductLabelProvider } from "./product-label-provider";

type Storage = { fetch(request: Request): Promise<Response> };
type Snapshot = { sources: Doc<SourcePack>; run: LabelRun | null };

/** Auth and same-origin protection are enforced by the Worker before this handler. */
export async function productLabels(
  request: Request,
  storage: Storage,
  provider: ProductLabelProvider | null,
): Promise<Response> {
  if (!["GET", "POST"].includes(request.method)) return fail(405, "Method not allowed.");
  const internal = (operation: string, body?: LabelRun) => storage.fetch(new Request(
    new URL(`/internal/product-labels/${operation}`, request.url),
    body ? { method: "POST", body: JSON.stringify(body) } : {},
  ));
  const snapshotResponse = await internal("snapshot");
  if (!snapshotResponse.ok) return snapshotResponse;
  const snapshot = await snapshotResponse.json() as Snapshot;
  let input: LabelInput;
  try { input = await buildLabelInput(snapshot.sources.data, snapshot.sources.revision); }
  catch (error) {
    return fail(422, error instanceof Error ? error.message : "Review product identities before labeling.");
  }
  const response = (run: LabelRun | null): Response => {
    const stale = !!run && (!run.context || run.sourceRevision !== input.sourceRevision ||
      run.sourceFingerprint !== input.sourceFingerprint || (!!provider && run.provider !== provider.id));
    const expired = run?.status === "running" && Date.parse(run.startedAt) + LABEL_LEASE_MS <= Date.now();
    const body: LabelResponse = {
      ...input, configured: !!provider, provider: provider?.id ?? null, stale,
      run: expired ? { ...run, status: "failed", error: "Labeling timed out. Retry to start a new run." } : run,
    };
    return Response.json(body, { headers: { "Cache-Control": "no-store" } });
  };
  if (request.method === "GET") return response(snapshot.run);
  let revision: number;
  try {
    const raw = await request.text();
    if (raw.length > 250000) return fail(413, "Request too large.");
    const body = JSON.parse(raw);
    if (!body || Object.keys(body).some((key) => !["sourceRevision", "context"].includes(key)) ||
        !Number.isSafeInteger(body.sourceRevision) || body.sourceRevision < 0)
      return fail(422, "A saved source revision is required.");
    revision = body.sourceRevision;
    input.context = labelContextSchema.parse(body.context);
    const codes = new Set(input.products.map(p => p.reference));
    if (input.context.evidence.some(e => !codes.has(e.productCode)))
      return fail(422, "Evidence must refer to an inventory product code.");
  } catch { return fail(422, "Provide a saved source revision, market, assessment date, and valid dated evidence."); }
  if (revision !== input.sourceRevision)
    return fail(409, "Sources changed. Save and label the latest sources.", "LABEL_SOURCES_CHANGED");
  if (!provider) return fail(503, "The labeling tool is not connected yet. Existing forecasting remains unchanged.", "LABEL_PROVIDER_UNCONFIGURED");
  if (!input.products.length) return fail(422, "No named products were found in these sources.");
  const run: LabelRun = {
    id: crypto.randomUUID(), context: input.context, sourceRevision: input.sourceRevision,
    sourceFingerprint: input.sourceFingerprint, provider: provider.id,
    status: "running", startedAt: new Date().toISOString(), completedAt: null,
    results: [], error: null,
  };
  const claimed = await internal("claim", run);
  if (!claimed.ok) return claimed;
  const claim = await claimed.json() as { acquired: boolean; run: LabelRun };
  if (!claim.acquired) return response(claim.run);
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => { controller.abort(); reject(Error("timeout")); }, LABEL_TIMEOUT_MS);
    });
    const raw = await Promise.race([provider.classify(input, controller.signal), timeout]);
    const results = validateLabelResponse(raw, input);
    run.results = results;
    run.status = results.filter((result) => result.group !== null).length === input.products.length ? "completed" : "partial";
  } catch {
    // Provider errors can contain credentials, URLs or prompt payloads. Never echo them.
    run.status = "failed";
    run.error = controller.signal.aborted
      ? "The labeling tool timed out. Your source data is saved; retry when the tool is available."
      : "The labeling tool failed or returned invalid labels. Your source data is saved; retry or contact the integration owner.";
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
  run.completedAt = new Date().toISOString();
  const finished = await internal("finish", run);
  if (!finished.ok) return finished;
  const saved = await finished.json() as { run: LabelRun };
  const latestResponse = await internal("snapshot");
  if (!latestResponse.ok) return latestResponse;
  const latest = await latestResponse.json() as Snapshot;
  input = await buildLabelInput(latest.sources.data, latest.sources.revision);
  return response(latest.run ?? saved.run);
}
