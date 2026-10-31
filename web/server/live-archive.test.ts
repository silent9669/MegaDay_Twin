// @vitest-environment node
// Opt-in against a local export; never commit or log source records.
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import type { DurableObjectState } from "@cloudflare/workers-types";
import { expect, it, vi } from "vitest";
import { analyzeArchive } from "./studio-import";
import { AccountWorkspace } from "./workspace";
import { inspect, type SourcePack } from "../src/studio/sources";
import { sourceView } from "../src/studio/source-view";
import type { LabelResponse } from "../src/studio/product-labels";

it.skipIf(!process.env.LIVE_ARCHIVE_PATH)("imports the live 11.11 archive and persists labels for all stocked products", async () => {
  const start = performance.now();
  const analysis = await analyzeArchive(new Uint8Array(readFileSync(process.env.LIVE_ARCHIVE_PATH!)), {}, false);
  expect(analysis.tables.map(table => table.rowCount).sort((a, b) => a - b)).toEqual([1176, 4156, 7176]);
  expect(analysis.skipped.some(file => file.file.endsWith("_replay.json"))).toBe(true);
  const pack: SourcePack = { schema: "hackathon.sources.v1", files: analysis.tables.map(table => table.source) };
  const feed = sourceView(pack, 1);
  expect(feed.orderRows).toBe(4156);
  expect(feed.stock.filter(row => (row.stock ?? 0) > 0)).toHaveLength(262);
  const database = new DatabaseSync(":memory:");
  const workspace = new AccountWorkspace({ storage: {
    sql: { exec: (query: string, ...bindings: (string | number)[]) => {
      const statement = database.prepare(query);
      if (query.startsWith("SELECT")) return { toArray: () => statement.all(...bindings) };
      statement.run(...bindings); return { toArray: () => [] };
    } },
    transactionSync: <T>(fn: () => T) => { database.exec("BEGIN"); try { const result = fn(); database.exec("COMMIT"); return result; } catch (error) { database.exec("ROLLBACK"); throw error; } },
  }, getWebSockets: () => [] } as unknown as DurableObjectState, { OPENAI_API_KEY: "test", OPENAI_MODEL_DECISION: "test" });
  try {
    const save = await workspace.fetch(new Request("https://test/api/workspace/sources", { method: "PUT", body: JSON.stringify({ revision: 0, data: pack }) }));
    expect(save.status).toBe(200);
    const savedAt = performance.now();
    const model = vi.fn<typeof fetch>(async (_url, init) => {
      const batch = JSON.parse(JSON.parse(init!.body as string).input).untrusted_evidence;
      return Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ labels: batch.products.map((p: { id: string }) => ({ productId: p.id, group: "Normal", reason: "Estimate from product use; needs demand evidence", confidence: 0.2 })) }) }] }] });
    });
    vi.stubGlobal("fetch", model);
    const result = await workspace.fetch(new Request("https://test/internal/product-labels", { method: "POST", body: JSON.stringify({ sourceRevision: 1, context: { market: "Vietnam", asOf: "2026-11-12", evidence: [] } }) }));
    expect(result.status).toBe(200);
    const body = await result.json() as LabelResponse;
    expect(body.run?.results).toHaveLength(262);
    expect(body.run?.status).toBe("completed");
    expect(body.run?.results.every(result => result.group !== null)).toBe(true);
    expect(model).toHaveBeenCalledTimes(6);
    const stored = await (await workspace.fetch(new Request("https://test/api/workspace/sources"))).json() as { data: SourcePack };
    const inventory = stored.data.files.find(file => file.kind === "inventory")!;
    expect(inspect(inventory.name, inventory.text).columns).toContain("Demand label");
    expect(database.prepare("SELECT COUNT(*) AS count FROM studio_rows").get()).toMatchObject({ count: 12508 });
    console.info(JSON.stringify({ localImportAndSaveMs: Math.round(savedAt - start), localMockedLabelAndSaveMs: Math.round(performance.now() - savedAt), rows: 12508, products: 262, modelCalls: 6 }));
  } finally { database.close(); vi.unstubAllGlobals(); }
});
