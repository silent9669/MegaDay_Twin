import { z } from "zod";
import {
  inspect,
  SOURCE_DEFS,
  type SourceKind,
  type SourcePack,
} from "../../studio/sources";
import { sourceView, type StockRow } from "../../studio/source-view";
import type { Doc, Sandbox } from "../../studio/workspace-types";

const kinds = [
  "source",
  "stock-zero",
  "stock-unknown",
  "stock-recovered",
  "diagnostics",
  "diagnostics-resolved",
  "orders",
  "campaigns",
  "clock",
] as const;
export const changeReferenceSchema = z
  .object({
    kind: z.enum(kinds),
    sourceRevision: z.number().int().nonnegative(),
    sandboxRevision: z.number().int().nonnegative(),
    hour: z.number().int().min(0).max(24),
    source: z
      .enum(["inventory", "orders", "ads", "cost", "campaigns", "sale_program"])
      .optional(),
    entity: z.string().max(200).optional(),
    before: z.union([z.number().finite(), z.string().max(200), z.null()]),
    after: z.union([z.number().finite(), z.string().max(200), z.null()]),
  })
  .strict();
export type ChangeReference = z.infer<typeof changeReferenceSchema>;
export type ChangeNotice = {
  id: string;
  kind: ChangeReference["kind"];
  title: string;
  detail: string;
  before: ChangeReference["before"];
  after: ChangeReference["after"];
  important: boolean;
  simulation: boolean;
  reconnected: boolean;
  observedAt: string;
  href: string;
  reference: ChangeReference;
};
export type MonitorSnapshot = {
  sourceRevision: number;
  sandboxRevision: number;
  hour: number;
  stock: StockRow[];
  orders: number;
  campaigns: number;
  sources: { kind: SourceKind; hash: string; issues: string[] }[];
};
export function snapshot(
  sources: Doc<SourcePack>,
  sandbox: Doc<Sandbox>,
): MonitorSnapshot {
  const view = sourceView(sources.data, sandbox.data.hour);
  return {
    sourceRevision: sources.revision,
    sandboxRevision: sandbox.revision,
    hour: sandbox.data.hour,
    stock: view.stock,
    orders: view.orderRows,
    campaigns: view.campaignCount,
    sources: sources.data.files.map((file) => ({
      kind: file.kind,
      hash: file.hash,
      issues: inspect(file.name, file.text)
        .issues.filter((i) => i.level !== "info")
        .map((i) => `${i.code}:${i.row ?? ""}:${i.column ?? ""}`),
    })),
  };
}
export class WorkspaceMonitor {
  private previous: MonitorSnapshot | null = null;
  observe(next: MonitorSnapshot, reconnected = false): ChangeNotice[] {
    const previous = this.previous;
    if (
      previous &&
      (next.sourceRevision < previous.sourceRevision ||
        next.sandboxRevision < previous.sandboxRevision)
    )
      return [];
    if (
      previous &&
      next.sourceRevision === previous.sourceRevision &&
      next.sandboxRevision === previous.sandboxRevision
    )
      return [];
    this.previous = next;
    if (!previous) return [];
    const notices: ChangeNotice[] = [];
    const simulation = next.hour !== previous.hour;
    const add = (
      kind: ChangeReference["kind"],
      title: string,
      detail: string,
      before: ChangeReference["before"],
      after: ChangeReference["after"],
      source?: SourceKind,
      entity?: string,
      important = false,
    ) => {
      notices.push({
        id: `${next.sourceRevision}:${next.sandboxRevision}:${kind}:${source ?? ""}:${entity ?? ""}`,
        kind,
        title,
        detail,
        before,
        after,
        important,
        simulation,
        reconnected,
        observedAt: new Date().toISOString(),
        href:
          source === "inventory"
            ? `/city-demo?district=warehouse${entity ? `&stock=${encodeURIComponent(entity)}` : ""}`
            : `/mock-data${source ? `?source=${source}` : ""}`,
        reference: {
          kind,
          sourceRevision: next.sourceRevision,
          sandboxRevision: next.sandboxRevision,
          hour: next.hour,
          source,
          entity,
          before,
          after,
        },
      });
    };
    if (simulation)
      add(
        "clock",
        next.hour < previous.hour ? "Sandbox rewound" : "Sandbox advanced",
        `${previous.hour}:00 → ${next.hour}:00 · Simulation`,
        previous.hour,
        next.hour,
      );
    if (next.hour < previous.hour) return notices;
    for (const source of next.sources) {
      const old = previous.sources.find((s) => s.kind === source.kind);
      const name = SOURCE_DEFS.find((s) => s.kind === source.kind)!.name;
      if (!old || old.hash !== source.hash)
        add(
          "source",
          `${name} updated`,
          "A new source version is available.",
          old?.hash ?? null,
          source.hash,
          source.kind,
        );
      const newIssues = source.issues.filter(
        (i) => !old?.issues.includes(i),
      ).length;
      const resolved =
        old?.issues.filter((i) => !source.issues.includes(i)).length ?? 0;
      if (newIssues)
        add(
          "diagnostics",
          `${name} needs review`,
          `${newIssues} new source check${newIssues === 1 ? "" : "s"}.`,
          old?.issues.length ?? 0,
          source.issues.length,
          source.kind,
          undefined,
          true,
        );
      if (resolved)
        add(
          "diagnostics-resolved",
          `${name} checks resolved`,
          `${resolved} source check${resolved === 1 ? "" : "s"} resolved.`,
          old?.issues.length ?? 0,
          source.issues.length,
          source.kind,
        );
    }
    for (const old of previous.sources) {
      if (!next.sources.some((s) => s.kind === old.kind))
        add(
          "source",
          `${SOURCE_DEFS.find((s) => s.kind === old.kind)!.name} removed`,
          "This source is no longer in the workspace.",
          old.hash,
          null,
          old.kind,
        );
    }
    const oldStock = new Map(
      previous.stock.map((row) => [row.code, row.stock]),
    );
    for (const row of next.stock) {
      const before = oldStock.get(row.code);
      if (row.stock === before) continue;
      const title =
        row.stock === null
          ? "Stock is unknown"
          : row.stock === 0
            ? "Stock reached zero"
            : before === null || before === 0
              ? "Stock recovered"
              : "";
      if (!title) continue;
      const kind =
        row.stock === null
          ? "stock-unknown"
          : row.stock === 0
            ? "stock-zero"
            : "stock-recovered";
      add(
        kind,
        title,
        `${row.code}: ${before === undefined || before === null ? "unknown" : before} → ${row.stock ?? "unknown"}`,
        before ?? null,
        row.stock,
        "inventory",
        row.code,
        row.stock === null || row.stock === 0,
      );
    }
    if (next.orders !== previous.orders)
      add(
        "orders",
        "Order observations changed",
        `${previous.orders} → ${next.orders} source rows`,
        previous.orders,
        next.orders,
        "orders",
      );
    if (next.campaigns !== previous.campaigns)
      add(
        "campaigns",
        "Campaign structure changed",
        `${previous.campaigns} → ${next.campaigns} source rows`,
        previous.campaigns,
        next.campaigns,
        "campaigns",
      );
    return notices;
  }
}
