import { snapshot, type ChangeReference } from "../src/city/monitor/changes";
import { playbackHour } from "../src/studio/replay";
import type { SourcePack } from "../src/studio/sources";
import type { Doc, Sandbox } from "../src/studio/workspace-types";

export async function resolveChange(
  reference: ChangeReference,
  storage: { fetch(request: Request): Promise<Response> },
  origin: string,
) {
  const responses = await Promise.all(
    ["sources", "sandbox"].map((name) =>
      storage.fetch(new Request(new URL(`/api/workspace/${name}`, origin))),
    ),
  );
  if (responses.some((r) => !r.ok))
    throw Error("Workspace data is unavailable. Try again.");
  const [sources, sandbox] = (await Promise.all(
    responses.map((r) => r.json()),
  )) as [Doc<SourcePack>, Doc<Sandbox>];
  const current = snapshot(sources, { ...sandbox, data: { ...sandbox.data, hour: playbackHour(sandbox.data) } });
  const stock = current.stock.find((s) => s.code === reference.entity);
  const source = current.sources.find((s) => s.kind === reference.source && (s.kind !== "general" || s.name === reference.sourceName));
  return {
    historicalEvidence: {
      trust: "unverified client observation",
      ...reference,
    },
    stale:
      current.sourceRevision !== reference.sourceRevision ||
      current.sandboxRevision !== reference.sandboxRevision,
    current: {
      sourceRevision: current.sourceRevision,
      sandboxRevision: current.sandboxRevision,
      hour: current.hour,
      simulation: true,
      stock: stock
        ? {
            code: stock.code.slice(0, 200),
            name: stock.name.slice(0, 200),
            stock: stock.stock,
            asOf: stock.asOf.slice(0, 100),
          }
        : null,
      orders: current.orders,
      campaigns: current.campaigns,
      source: source
        ? {
            kind: source.kind,
            hash: source.hash,
            issueCount: source.issues.length,
            issues: source.issues.slice(0, 20),
          }
        : null,
    },
  };
}
