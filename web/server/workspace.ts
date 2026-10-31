import { savedChat } from "./chat-store";
import type { PlannerEnv } from "./sale-planner";
import { hqSchema, defaultHQ } from "../src/hq/types";
import type {
  DurableObjectState,
  WebSocket as WorkerSocket,
} from "@cloudflare/workers-types";
import {
  defaultSettings,
  defaultSandbox,
  settingsSchema,
  sandboxSchema,
  chatArchiveSchema,
  defaultChatArchive,
} from "../src/studio/workspace-types";
import Papa from "papaparse";
import { validatePack, MAX_BYTES, inspect, sourceKey, hashText, normalize, DEMAND_COLUMN, withoutDemandLabels } from "../src/studio/sources";
import type { Source, SourcePack } from "../src/studio/sources";
import {
  labelRunSchema, sourceFingerprint, type LabelRun,
} from "../src/studio/product-labels";
import { getProductLabelProvider, LABEL_LEASE_MS } from "./product-label-provider";
import { productLabels } from "./product-labels";
import { studioImport } from "./studio-import";
import type { StudioModelEnv } from "./model-json";
import { fail } from "./auth";
import { workspaceQuotaFailure } from "./workspace-errors";
import repositoryPack from "./repository-sources.json";
function isUntouchedDemo(source: Source) {
  if (!source.commit) return false;
  const demo = repositoryPack.files.find(file => file.name === source.name);
  if (!demo) return false;
  if (source.hash === demo.hash) return true;
  if (source.kind !== "inventory") return false;
  const parsed = inspect(source.name, source.text);
  return parsed.columns.includes(DEMAND_COLUMN) &&
    Papa.unparse({ fields: parsed.columns.filter(column => column !== DEMAND_COLUMN), data: parsed.rows }) === demo.text;
}
declare const WebSocketPair: { new (): { 0: WorkerSocket; 1: WorkerSocket } };
export class AccountWorkspace {
  private ctx: DurableObjectState;
  constructor(ctx: DurableObjectState, private env: StudioModelEnv & PlannerEnv = {}) {
    this.ctx = ctx;
    const existing = new Set(ctx.storage.sql.exec<{ name: string }>("SELECT name FROM sqlite_master WHERE type = 'table'").toArray().map(table => table.name));
    // Avoid redundant schema writes; Cloudflare may still reject reads after quota exhaustion.
    if (!existing.has("documents")) ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS documents (name TEXT PRIMARY KEY, revision INTEGER NOT NULL, updated_at TEXT NOT NULL)",
    );
    if (!existing.has("chunks")) ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS chunks (name TEXT, position INTEGER, content TEXT NOT NULL, PRIMARY KEY(name, position))",
    );
    if (!existing.has("studio_tables")) ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS studio_tables (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, columns_json TEXT NOT NULL, source_revision INTEGER NOT NULL)");
    if (!existing.has("studio_rows")) ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS studio_rows (table_id TEXT NOT NULL, position INTEGER NOT NULL, record_json TEXT NOT NULL, PRIMARY KEY(table_id, position))");
    // One-time cleanup of exact, untouched repository defaults. Uploaded/edited tables survive.
    if (!this.read("empty-studio-v1")) this.ctx.storage.transactionSync(() => {
      const sources = this.read("sources");
      if (sources) {
        const pack = sources.data as SourcePack;
        const files = pack.files.filter(source => !isUntouchedDemo(source));
        if (files.length !== pack.files.length) {
          this.writeDocument("sources", { ...pack, files }, sources.revision);
          this.removeDocument("product-labels");
        }
      }
      this.writeDocument("empty-studio-v1", { done: true }, 0);
    });
  }
  private read(name: string) {
    const meta = this.ctx.storage.sql
      .exec<{ revision: number; updated_at: string }>(
        "SELECT revision, updated_at FROM documents WHERE name = ?",
        name,
      )
      .toArray()[0];
    if (!meta) return null;
    const chunks = this.ctx.storage.sql
      .exec<{ content: string }>(
        "SELECT content FROM chunks WHERE name = ? ORDER BY position",
        name,
      )
      .toArray();
    return {
      revision: meta.revision,
      updatedAt: meta.updated_at,
      data: JSON.parse(chunks.map((c) => c.content).join("")),
    };
  }
  private writeDocument(name: string, data: unknown, expected: number) {
    const old = this.ctx.storage.sql
      .exec<{ revision: number }>(
        "SELECT revision FROM documents WHERE name = ?",
        name,
      )
      .toArray()[0];
    if ((old?.revision ?? 0) !== expected) return null;
    const previous = name === "sources" ? this.read(name)?.data as SourcePack | undefined : undefined;
    const revision = expected + 1,
      updatedAt = new Date().toISOString(),
      text = JSON.stringify(data);
    this.ctx.storage.sql.exec("DELETE FROM chunks WHERE name = ?", name);
    for (let i = 0; i < text.length; i += 100000)
      this.ctx.storage.sql.exec(
        "INSERT INTO chunks VALUES (?, ?, ?)",
        name,
        i / 100000,
        text.slice(i, i + 100000),
      );
    this.ctx.storage.sql.exec(
      "INSERT INTO documents VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET revision = excluded.revision, updated_at = excluded.updated_at",
      name,
      revision,
      updatedAt,
    );
    if (name === "sources") {
      this.materialize(data as SourcePack, revision, previous);
      if (!(data as SourcePack).files.some(source => source.kind === "inventory")) this.removeDocument("product-labels");
    }
    return { revision, updatedAt, data };
  }
  private write(name: string, data: unknown, expected: number) {
    return this.ctx.storage.transactionSync(() => this.writeDocument(name, data, expected));
  }
  private removeDocument(name: string) {
    this.ctx.storage.sql.exec("DELETE FROM chunks WHERE name = ?", name);
    this.ctx.storage.sql.exec("DELETE FROM documents WHERE name = ?", name);
  }
  private materialize(pack: SourcePack, revision: number, previous?: SourcePack) {
    const catalog = this.ctx.storage.sql.exec<{ id: string }>("SELECT id FROM studio_tables").toArray();
    const keys = new Set(pack.files.map(sourceKey));
    for (const { id } of catalog) if (!keys.has(id)) {
      this.ctx.storage.sql.exec("DELETE FROM studio_rows WHERE table_id = ?", id);
      this.ctx.storage.sql.exec("DELETE FROM studio_tables WHERE id = ?", id);
    }
    for (const source of pack.files) {
      const id = sourceKey(source);
      if (catalog.some(table => table.id === id) && previous?.files.some(old => sourceKey(old) === id && old.hash === source.hash)) continue;
      const parsed = inspect(source.name, source.text);
      const previousRows = new Map(this.ctx.storage.sql.exec<{ position: number; record_json: string }>("SELECT position, record_json FROM studio_rows WHERE table_id = ?", id).toArray().map(row => [row.position, row.record_json]));
      if ([...previousRows.keys()].some(position => position >= parsed.rows.length))
        this.ctx.storage.sql.exec("DELETE FROM studio_rows WHERE table_id = ? AND position >= ?", id, parsed.rows.length);
      this.ctx.storage.sql.exec("INSERT INTO studio_tables VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind, columns_json=excluded.columns_json, source_revision=excluded.source_revision", id, source.name, source.kind, JSON.stringify(parsed.columns), revision);
      parsed.rows.forEach((row, index) => {
        const record = JSON.stringify(row);
        if (previousRows.get(index) === record) return;
        this.ctx.storage.sql.exec("INSERT INTO studio_rows VALUES (?, ?, ?) ON CONFLICT(table_id, position) DO UPDATE SET record_json = excluded.record_json", id, index, record);
      });
    }
  }
  private notify(name: string, revision: number) {
    for (const socket of this.ctx.getWebSockets()) {
      try {
        if ((socket.deserializeAttachment() as { expires: number }).expires <= Date.now())
          socket.close(1008, "Sign in again");
        else socket.send(JSON.stringify({ name, revision }));
      } catch {
        socket.close(1011, "Reconnect");
      }
    }
  }
  /** Internal binding calls only. Worker must never proxy browser requests here. */
  private async productLabels(request: Request, operation: string): Promise<Response> {
    if ((operation === "snapshot" ? "GET" : "POST") !== request.method)
      return fail(405, "Method not allowed.");
    if (!this.read("sources")) {
      const initialized = await this.fetch(new Request(new URL("/api/workspace/sources", request.url)));
      if (!initialized.ok) return initialized;
    }
    const sources = this.read("sources")!;
    const archive = this.read("product-labels");
    if (operation === "snapshot")
      return Response.json({ sources, run: archive?.data.run ?? null });
    let candidate: LabelRun;
    try {
      const raw = await request.text();
      if (new TextEncoder().encode(raw).length > MAX_BYTES) return fail(413, "Label result too large.");
      candidate = labelRunSchema.parse(JSON.parse(raw));
    } catch {
      return fail(422, "Invalid labeling run.");
    }
    const fingerprint = await sourceFingerprint(sources.data as SourcePack);
    // Validate before claiming; finish rechecks again after asynchronous hashing.
    if (this.read("sources")?.revision !== sources.revision ||
        candidate.sourceRevision !== sources.revision || candidate.sourceFingerprint !== fingerprint)
      return fail(409, "Sources changed. Save and label the latest sources.", "LABEL_SOURCES_CHANGED");
    const current = this.read("product-labels");
    const previous = current?.data.run as LabelRun | undefined;
    if (operation === "claim") {
      if (candidate.status !== "running" || candidate.results.length || candidate.error || candidate.completedAt)
        return fail(422, "Invalid labeling claim.");
      if (previous?.sourceRevision === sources.revision && previous.sourceFingerprint === fingerprint) {
        if (previous.status === "running" && Date.parse(previous.startedAt) + LABEL_LEASE_MS > Date.now())
          return fail(409, "Labeling is already running. Refresh its status shortly.", "LABEL_RUN_ACTIVE");
        if (["completed", "partial"].includes(previous.status) && !previous.error && previous.provider === candidate.provider && JSON.stringify(previous.context) === JSON.stringify(candidate.context))
          return Response.json({ acquired: false, run: previous });
      }
      // Start time comes from this object, not from the caller's clock.
      candidate.startedAt = new Date().toISOString();
    } else {
      if (!previous || previous.id !== candidate.id || previous.status !== "running" ||
          previous.provider !== candidate.provider || previous.sourceRevision !== candidate.sourceRevision ||
          previous.sourceFingerprint !== candidate.sourceFingerprint || JSON.stringify(previous.context) !== JSON.stringify(candidate.context) || candidate.status === "running")
        return fail(409, "A newer labeling run superseded this result.", "LABEL_RUN_SUPERSEDED");
      candidate.startedAt = previous.startedAt;
      candidate.completedAt = new Date().toISOString();
    }
    let labeledPack: SourcePack | null = null;
    if (operation === "finish" && ["completed", "partial"].includes(candidate.status)) {
      const labels = new Map(candidate.results.map(r => [r.productId, r.group]));
      labeledPack = { ...sources.data, files: await Promise.all((sources.data as SourcePack).files.map(async source => {
        if (source.kind !== "inventory") return source;
        const parsed = inspect(source.name, source.text);
        const code = parsed.columns.find(c => normalize(c) === normalize("Mã nội bộ"))!;
        const columns = [...parsed.columns.filter(c => c !== DEMAND_COLUMN), DEMAND_COLUMN];
        const rows = parsed.rows.map(row => ({ ...row, [DEMAND_COLUMN]: labels.get(JSON.stringify(["inventory", "internal_code", String(row[code] ?? "").trim()])) ?? "" }));
        const text = Papa.unparse({ fields: columns, data: rows });
        return { ...source, text, hash: await hashText(text) };
      })) };
      candidate.sourceFingerprint = await sourceFingerprint(labeledPack!);
      candidate.sourceRevision = sources.revision + 1;
    }
    // Hashing yields: recheck both revisions before the atomic source/run commit.
    if (this.read("sources")?.revision !== sources.revision || this.read("product-labels")?.revision !== current?.revision)
      return fail(409, "Workspace changed while labeling. Retry with the latest sources.", "LABEL_SOURCES_CHANGED");
    const saved = this.ctx.storage.transactionSync(() => {
      if (labeledPack) this.writeDocument("sources", labeledPack, sources.revision);
      return this.writeDocument("product-labels", { run: candidate }, current?.revision ?? 0);
    });
    if (!saved) return fail(409, "Labeling status changed. Refresh and try again.");
    if (labeledPack) this.notify("sources", candidate.sourceRevision);
    this.notify("product-labels", saved.revision);
    return Response.json({ acquired: true, run: candidate });
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url),
      name = url.pathname.split("/").pop() ?? "";
    // Parse archives and inventory here, not in the gateway's 10 ms CPU budget.
    if (url.pathname === "/internal/milo/chat") return savedChat(request,this.env,{decisionToolsConnected:!!this.env.DATA_BACKEND_URL},this);
    if (url.pathname === "/internal/studio/import") return studioImport(request, this.env);
    if (url.pathname === "/internal/product-labels") return productLabels(request, this, getProductLabelProvider(this.env));
    const labelOperation = /^\/internal\/product-labels\/(snapshot|claim|finish)$/.exec(url.pathname)?.[1];
    if (labelOperation) return this.productLabels(request, labelOperation);
    if (name === "events") {
      if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket")
        return fail(426, "WebSocket upgrade required.");
      if (this.ctx.getWebSockets().length >= 20)
        return fail(429, "Too many workspace connections.");
      const pair = new WebSocketPair();
      pair[1].serializeAttachment({
        expires: Math.min(
          Number(request.headers.get("X-Session-Expires") ?? 0),
          Date.now() + 12 * 3600000,
        ),
      });
      this.ctx.acceptWebSocket(pair[1]);
      return new Response(null, {
        status: 101,
        webSocket: pair[0],
      } as ResponseInit);
    }
    if (!["sources", "settings", "sandbox", "chats", "hq"].includes(name))
      return fail(404, "Workspace document not found.");
    if (request.method === "GET") {
      let document = this.read(name);
      if (!document) {
        const data =
          name === "sources"
            ? { schema: "hackathon.sources.v1", files: [] }
            : name === "hq"
              ? defaultHQ
              : name === "settings"
              ? defaultSettings
              : name === "chats"
                ? defaultChatArchive
                : defaultSandbox;
        document = this.write(name, data, 0) ?? this.read(name);
      }
      return Response.json(document, {
        headers: { "Cache-Control": "no-store" },
      });
    }
    if (request.method !== "PUT") return fail(405, "Method not allowed.");
    if (Number(request.headers.get("content-length") ?? 0) > MAX_BYTES + 100000)
      return fail(413, "Workspace exceeds 20 MiB.");
    try {
      const raw = await request.text();
      if (new TextEncoder().encode(raw).length > MAX_BYTES + 100000)
        return fail(413, "Workspace exceeds 20 MiB.");
      const body = JSON.parse(raw);
      if (!Number.isSafeInteger(body.revision) || body.revision < 0)
        return fail(422, "A valid revision is required.");
      const data =
        name === "sources"
          ? await withoutDemandLabels(await validatePack(body.data))
          : name === "hq"
            ? hqSchema.parse(body.data)
            : name === "settings"
            ? settingsSchema.parse(body.data)
            : name === "chats"
              ? chatArchiveSchema.parse(body.data)
              : sandboxSchema.parse(body.data);
      const saved = this.write(name, data, body.revision);
      if (!saved)
        return fail(
          409,
          "Another device saved a newer version. Your draft has been kept.",
          "REVISION_CONFLICT",
        );
      this.notify(name, saved.revision);
      return Response.json(saved, { headers: { "Cache-Control": "no-store" } });
    } catch (error) {
      const quotaFailure = workspaceQuotaFailure(error);
      if (quotaFailure) return quotaFailure;
      return fail(
        422,
        error instanceof Error && error.name !== "ZodError"
          ? error.message.slice(0, 1000)
          : "Invalid workspace data.",
      );
    }
  }
  webSocketMessage(socket: WorkerSocket) {
    if (
      (socket.deserializeAttachment() as { expires: number }).expires <=
      Date.now()
    )
      socket.close(1008, "Sign in again");
    else socket.send("pong");
  }
  webSocketClose(socket: WorkerSocket, code: number) {
    // 1006 is reserved and cannot be sent in a close frame.
    socket.close(code === 1006 ? 1000 : code, "");
  }
  webSocketError(socket: WorkerSocket) {
    socket.close(1011, "Reconnect");
  }
}
