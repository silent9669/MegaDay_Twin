// @vitest-environment node
import { DatabaseSync } from "node:sqlite";
import { createHmac } from "node:crypto";
import type { DurableObjectState } from "@cloudflare/workers-types";
import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "../worker";
import { AccountWorkspace } from "./workspace";
import { productLabels } from "./product-labels";
import { getProductLabelProvider, LABEL_LEASE_MS, LABEL_TIMEOUT_MS, type ProductLabelProvider } from "./product-label-provider";
import { buildLabelInput, type LabelInput, type LabelResponse, type LabelRun } from "../src/studio/product-labels";
import { makeSource, type SourcePack } from "../src/studio/sources";
import type { Doc } from "../src/studio/workspace-types";
import rawPack from "./repository-sources.json";

const origin = "https://example.test";
const databases: DatabaseSync[] = [];
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  databases.splice(0).forEach((database) => database.close());
});
const pack: SourcePack = {
  ...rawPack as SourcePack,
  files: (rawPack as SourcePack).files.map((source) => ({
    ...source,
    text: source.kind === "orders"
      ? "Mã đơn hàng,SKU phân loại hàng,Số lượng,Ngày đặt hàng,Tên sản phẩm\nO1,SKU1,1,2026-11-11 01:00,Product one"
      : source.kind === "inventory"
        ? "Mã nội bộ,Tồn thực tế,Tên hàng\nInternal1,5,Product one"
        : source.kind === "cost" ? "tên sp,giá vốn\nProduct one,45000" : source.text,
  })),
};
const request = (sourceRevision?: number) => new Request(origin + "/api/product-labels",
  sourceRevision === undefined ? {} : { method: "POST", body: JSON.stringify({ sourceRevision, context: { market: "Vietnam", asOf: "2026-10-31", evidence: [] } }) });
const labels = (input: LabelInput) => ({
  labels: input.products.map((product) => ({ productId: product.id, group: "Normal", reason: "Provider evidence", confidence: 0.8 })),
});
async function setup(env = {}) {
  const database = new DatabaseSync(":memory:");
  databases.push(database);
  const sent: string[] = [];
  const state = {
    storage: {
      sql: { exec: (query: string, ...bindings: (string | number)[]) => {
        const statement = database.prepare(query);
        if (query.startsWith("SELECT")) return { toArray: () => statement.all(...bindings) };
        statement.run(...bindings);
        return { toArray: () => [] };
      } },
      transactionSync: <T>(callback: () => T) => {
        database.exec("BEGIN");
        try { const result = callback(); database.exec("COMMIT"); return result; }
        catch (error) { database.exec("ROLLBACK"); throw error; }
      },
    },
    getWebSockets: () => [{
      deserializeAttachment: () => ({ expires: Date.now() + 3600000 }),
      send: (value: string) => sent.push(value), close: () => {},
    }],
  } as unknown as DurableObjectState;
  const workspace = new AccountWorkspace(state, env);
  const saveSources = (revision: number, data: SourcePack = pack) => workspace.fetch(new Request(origin + "/api/workspace/sources", {
    method: "PUT", body: JSON.stringify({ revision, data }),
  }));
  expect((await saveSources(0)).status).toBe(200);
  const sourceDoc = await (await workspace.fetch(new Request(origin + "/api/workspace/sources"))).json() as Doc<SourcePack>;
  const input = await buildLabelInput(sourceDoc.data, sourceDoc.revision);
  return { workspace, database, input, saveSources, sent, reconstruct: () => new AccountWorkspace(state) };
}

describe("product label workflow", () => {
  it("runs labeling in account storage with the same OpenAI secret binding as Milo", async () => {
    const { workspace } = await setup({ OPENAI_API_KEY: "test-secret", OPENAI_MODEL_DECISION: "decision-test" });
    const upstream = vi.fn<typeof fetch>(async (_url, init) => {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer test-secret");
      const body = JSON.parse(init!.body as string);
      expect(body.model).toBe("decision-test");
      const input = JSON.parse(body.input).untrusted_evidence;
      return Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify(labels(input)) }] }] });
    });
    vi.stubGlobal("fetch", upstream);
    const result = await workspace.fetch(new Request(origin + "/internal/product-labels", request(1)));
    expect(result.status).toBe(200);
    expect(await result.json()).toMatchObject({ run: { status: "completed" } });
    expect(upstream).toHaveBeenCalledTimes(1);
  });
  it("saves successful products and retries only missing products", async () => {
    const { workspace, saveSources } = await setup();
    const inventory = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,2\nB,Lamp,3", "upload");
    await saveSources(1, { schema: "hackathon.sources.v1", files: [inventory] });
    const classify = vi.fn(async (input: LabelInput) => ({ labels: labels(input).labels.slice(0, 1) }));
    const provider = { id: "test", classify };
    const first = await (await productLabels(request(2), workspace, provider)).json() as LabelResponse;
    expect(first.run).toMatchObject({ status: "partial", error: "Saved 1 of 2 products. Retry to label the remaining products." });
    const second = await (await productLabels(request(first.sourceRevision), workspace, provider)).json() as LabelResponse;
    expect(second.run).toMatchObject({ status: "completed", error: null });
    expect(second.run?.results).toHaveLength(2);
    expect(classify.mock.calls.map(([input]) => input.products.length)).toEqual([2, 1]);
  });

  it("stays honestly unconfigured and never writes fake labels", async () => {
    const { workspace } = await setup();
    expect(getProductLabelProvider({ OPENAI_API_KEY: "must-not-enable-without-decision-model" })).toBeNull();
    const response = await productLabels(request(), workspace, null);
    expect(await response.json()).toMatchObject({ configured: false, provider: null, sourceRevision: 1, run: null, stale: false });
    const disabled = await productLabels(request(1), workspace, null);
    expect(disabled.status).toBe(503);
    expect(await disabled.json()).toHaveProperty("error.code", "LABEL_PROVIDER_UNCONFIGURED");
  });
  it("persists valid labels across reconstruction and deduplicates completed requests", async () => {
    const { workspace, reconstruct, sent, database, saveSources } = await setup();
    const classify = vi.fn(async (input: LabelInput) => labels(input));
    const provider = { id: "test-adapter-v1", classify };
    const first = await productLabels(request(1), workspace, provider);
    expect(first.status).toBe(200);
    const result = await first.json() as LabelResponse;
    expect(result.run?.status).toBe("completed");
    expect(result.run?.results).toHaveLength(1);
    expect(result.run?.provider).toBe("test-adapter-v1");
    expect(result).toMatchObject({ sourceRevision: 2, stale: false });
    const sourceDoc = await (await workspace.fetch(new Request(origin + "/api/workspace/sources"))).json() as Doc<SourcePack>;
    expect(sourceDoc.data.files.find(f => f.kind === "inventory")?.text).toContain("Demand label");
    const row = database.prepare("SELECT record_json FROM studio_rows WHERE table_id = 'inventory'").get() as { record_json: string };
    expect(JSON.parse(row.record_json)).toMatchObject({ "Demand label": "Normal", "Tồn thực tế": "5" });
    const second = await productLabels(request(result.sourceRevision), reconstruct(), provider);
    expect((await second.json() as LabelResponse).run?.id).toBe(result.run?.id);
    expect(classify).toHaveBeenCalledTimes(1);
    expect(sent.some((message) => JSON.parse(message).name === "product-labels")).toBe(true);
    expect((await saveSources(2, sourceDoc.data)).status).toBe(200);
    const edited = await (await workspace.fetch(new Request(origin + "/api/workspace/sources"))).json() as Doc<SourcePack>;
    expect(edited.data.files.find(f => f.kind === "inventory")?.text).not.toContain("Demand label");
  });
  it("reuses partial results until evidence changes", async () => {
    const { workspace, input } = await setup();
    const provider = { id: "test", classify: vi.fn(async () => ({ labels: [
      { productId: input.products[0]!.id, group: null, reason: "Insufficient evidence" },
    ] })) };
    const response = await productLabels(request(1), workspace, provider);
    expect(await response.json()).toMatchObject({ run: { status: "partial", results: [{ group: null }] } });
    await productLabels(request(2), workspace, provider);
    expect(provider.classify).toHaveBeenCalledTimes(1);
    const changedEvidence = new Request(origin + "/api/product-labels", { method: "POST", body: JSON.stringify({ sourceRevision: 2,
      context: { market: "Vietnam", asOf: "2026-10-31", evidence: [{ productCode: "Internal1", observedAt: "2026-10-30", source: "Report", observation: "New product-specific evidence" }] } }) });
    expect((await productLabels(changedEvidence, workspace, provider)).status).toBe(200);
    expect(provider.classify).toHaveBeenCalledTimes(2);
  });
  it.each(["unknown-id", "duplicate-id", "unknown-group", "bad-confidence", "bad-envelope"])(
    "rejects invalid provider data (%s) without saving misleading labels", async (kind) => {
      const { workspace, input } = await setup();
      const first = labels(input).labels[0]!;
      const raw = kind === "unknown-id" ? { labels: [{ ...first, productId: "invented" }] }
        : kind === "duplicate-id" ? { labels: [first, first] }
          : kind === "unknown-group" ? { labels: [{ ...first, group: "electronics" }] }
            : kind === "bad-confidence" ? { labels: [{ ...first, confidence: 10 }] }
              : [first];
      const response = await productLabels(request(1), workspace, { id: "test", classify: async () => raw });
      expect(await response.json()).toMatchObject({ run: { status: "failed", results: [] } });
    },
  );
  it("reclassifies when the assessment changes and rejects future or unrelated evidence", async () => {
    const { workspace } = await setup();
    const provider = { id: "test", classify: vi.fn(async (input: LabelInput) => labels(input)) };
    await productLabels(request(1), workspace, provider);
    const post = (context: unknown) => new Request(origin + "/api/product-labels", {
      method: "POST", body: JSON.stringify({ sourceRevision: 2, context }),
    });
    const context = { market: "Thailand", asOf: "2026-10-31", evidence: [] };
    expect((await productLabels(post(context), workspace, provider)).status).toBe(200);
    expect(provider.classify).toHaveBeenCalledTimes(2);
    for (const evidence of [
      { productCode: "Internal1", observedAt: "2026-11-01", source: "Report", observation: "future" },
      { productCode: "Other", observedAt: "2026-10-30", source: "Report", observation: "unrelated" },
    ]) expect((await productLabels(post({ ...context, evidence: [evidence] }), workspace, provider)).status).toBe(422);
    expect(provider.classify).toHaveBeenCalledTimes(2);
  });
  it("deletes inventory records and labels together, rejecting stale restores", async () => {
    const { workspace, saveSources, database } = await setup();
    const provider = { id: "test", classify: async (input: LabelInput) => labels(input) };
    await productLabels(request(1), workspace, provider);
    expect((await saveSources(2, { schema: "hackathon.sources.v1", files: [] })).status).toBe(200);
    expect(await (await productLabels(request(), workspace, provider)).json()).toMatchObject({ products: [], run: null });
    expect(database.prepare("SELECT * FROM studio_rows").all()).toEqual([]);
    expect(database.prepare("SELECT * FROM chunks WHERE name = 'product-labels'").all()).toEqual([]);
    expect((await saveSources(2)).status).toBe(409);
  });
  it("redacts provider errors and lets a failed job retry", async () => {
    const { workspace } = await setup();
    const provider = { id: "test", classify: vi.fn(async (input: LabelInput) => labels(input)) };
    provider.classify.mockRejectedValueOnce(Error("secret-token=do-not-echo"));
    const failed = await productLabels(request(1), workspace, provider);
    const body = await failed.text();
    expect(body).toContain('"status":"failed"');
    expect(body).not.toContain("do-not-echo");
    expect(await (await productLabels(request(1), workspace, provider)).json()).toMatchObject({ run: { status: "completed" } });
  });
  it("dispatches one provider call when two devices start the same run", async () => {
    const { workspace, input } = await setup();
    let release!: (value: unknown) => void;
    let started!: () => void;
    const start = new Promise<void>((resolve) => { started = resolve; });
    const classify = vi.fn(() => { started(); return new Promise((resolve) => { release = resolve; }); });
    const provider = { id: "test", classify };
    const first = productLabels(request(1), workspace, provider);
    await start;
    const second = await productLabels(request(1), workspace, provider);
    expect(second.status).toBe(409);
    expect(classify).toHaveBeenCalledTimes(1);
    release(labels(input));
    expect((await first).status).toBe(200);
  });
  it("rejects late results atomically when sources change during the provider call", async () => {
    const { workspace, input, saveSources } = await setup();
    let release!: (value: unknown) => void;
    let started!: () => void;
    const start = new Promise<void>((resolve) => { started = resolve; });
    const provider = { id: "test", classify: () => { started(); return new Promise((resolve) => { release = resolve; }); } };
    const pending = productLabels(request(1), workspace, provider);
    await start;
    expect((await saveSources(1)).status).toBe(200);
    release(labels(input));
    expect((await pending).status).toBe(409);
    const latest = await (await productLabels(request(), workspace, provider)).json() as LabelResponse;
    expect(latest.sourceRevision).toBe(2);
    expect(latest.stale).toBe(true);
    expect(latest.run?.results).toEqual([]);
    expect((await productLabels(request(1), workspace, provider)).status).toBe(409);
  });
  it("times out even if an adapter ignores abort, and allows a later retry", async () => {
    const { workspace } = await setup();
    vi.useFakeTimers();
    let started!: () => void;
    const start = new Promise<void>((resolve) => { started = resolve; });
    let signal!: AbortSignal;
    const provider: ProductLabelProvider = { id: "test", classify: (_input, incomingSignal) => {
      signal = incomingSignal; started(); return new Promise(() => {});
    } };
    const pending = productLabels(request(1), workspace, provider);
    await start;
    await vi.advanceTimersByTimeAsync(LABEL_TIMEOUT_MS + 1);
    expect(signal.aborted).toBe(true);
    expect(await (await pending).json()).toMatchObject({ run: { status: "failed" } });
    provider.classify = async (input) => labels(input);
    expect(await (await productLabels(request(1), workspace, provider)).json()).toMatchObject({ run: { status: "completed" } });
  });
  it("rejects an expired run's finish after a new run acquires the lease", async () => {
    const { workspace, input } = await setup();
    const internal = (operation: string, run: LabelRun) => workspace.fetch(new Request(origin + `/internal/product-labels/${operation}`, {
      method: "POST", body: JSON.stringify(run),
    }));
    const old: LabelRun = { id: crypto.randomUUID(), sourceRevision: 1, sourceFingerprint: input.sourceFingerprint,
      provider: "test", status: "running", startedAt: new Date().toISOString(), completedAt: null, results: [], error: null };
    expect((await internal("claim", old)).status).toBe(200);
    vi.useFakeTimers();
    vi.setSystemTime(Date.now() + LABEL_LEASE_MS + 1);
    expect(await (await productLabels(request(), workspace, { id: "test", classify: async () => ({ labels: [] }) })).json())
      .toMatchObject({ run: { status: "failed" } });
    const newer = { ...old, id: crypto.randomUUID() };
    expect((await internal("claim", newer)).status).toBe(200);
    const staleFinish = await internal("finish", { ...old, status: "completed", completedAt: new Date().toISOString() });
    expect(staleFinish.status).toBe(409);
    const snapshot = await (await workspace.fetch(new Request(origin + "/internal/product-labels/snapshot"))).json() as { run: LabelRun };
    expect(snapshot.run.id).toBe(newer.id);
  });
});

describe("label route boundaries", () => {
  const secret = "workspace-test-secret-at-least-32-bytes";
  function cookie() {
    const payload = `admin.${Math.floor(Date.now() / 1000) + 3600}.nonce`;
    return `megaday_session=${payload}.${createHmac("sha256", secret).update(payload).digest("base64url")}`;
  }
  const base = {
    DB: { prepare: () => ({ first: async () => ({ ok: 1 }) }) },
    ASSETS: { fetch: async () => new Response("static app") },
    SESSION_SECRET: secret, ADMIN_PASSWORD_HASH: "configured",
  };
  it("serves the deployed shop baseline only after authentication", async () => {
    const assets = vi.fn<(request: Request) => Promise<Response>>(async () => Response.json({ schema: "hackathon.sources.v1", files: [] }));
    const env = { ...base, ASSETS: { fetch: assets } };
    expect((await worker.fetch(new Request(origin + "/api/studio/shop"), env)).status).toBe(401);
    expect((await worker.fetch(new Request(origin + "/shop-baseline.json"), env)).status).toBe(307);
    expect(assets).not.toHaveBeenCalled();
    const response = await worker.fetch(new Request(origin + "/api/studio/shop", { headers: { Cookie: cookie() } }), env);
    expect(response.status).toBe(200);
    expect(new URL(assets.mock.calls[0]![0].url).pathname).toBe("/shop-baseline.json");
  });
  it("keeps the Python multipart contract and excludes general tables and derived labels", async () => {
    const files = await Promise.all([
      makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế,Demand label\nA,Cup,17,Normal", "upload"),
      makeSource("custom.csv", "event,month\nTet,2", "upload"),
    ]);
    const upstream = vi.fn<typeof fetch>(async (_url, init) => {
      const form = init?.body as FormData;
      const forwarded = form.getAll("files") as File[];
      expect(forwarded).toHaveLength(1);
      expect(forwarded[0]!.name).toBe("ton_kho_google_sheet.csv");
      expect(await forwarded[0]!.text()).toBe("Mã nội bộ,Tên hàng,Tồn thực tế\r\nA,Cup,17");
      return Response.json({ accepted: true });
    });
    vi.stubGlobal("fetch", upstream);
    const env = { ...base, DATA_BACKEND_URL: "https://python.test/", DATA_BACKEND_USER: "operator", DATA_BACKEND_ROLE: "operator",
      WORKSPACE: { idFromName: () => "admin", get: () => ({ fetch: async () => Response.json({ data: { schema: "hackathon.sources.v1", files } }) }) } };
    const post = () => new Request(origin + "/api/data-backend/import", { method: "POST", headers: { Cookie: cookie(), Origin: origin } });
    expect((await worker.fetch(post(), env)).status).toBe(200);
    expect(upstream).toHaveBeenCalledTimes(1);
    files.shift();
    expect((await worker.fetch(post(), env)).status).toBe(422);
    expect(upstream).toHaveBeenCalledTimes(1);
  });
  it("requires auth and same-origin writes", async () => {
    expect((await worker.fetch(request(), base)).status).toBe(401);
    expect((await worker.fetch(new Request(origin + "/api/studio/import", { method: "POST" }), base)).status).toBe(401);
    expect((await worker.fetch(new Request(origin + "/api/studio/import", { method: "POST", headers: { Cookie: cookie(), Origin: "https://other.test" } }), base)).status).toBe(403);
    const response = await worker.fetch(new Request(origin + "/api/product-labels", {
      method: "POST", headers: { Cookie: cookie(), Origin: "https://other.test" }, body: "{}",
    }), base);
    expect(response.status).toBe(403);
  });
  it.each(["/api/product-labels", "/api/studio/import?review=1"])("streams %s to account storage without parsing data in the gateway", async path => {
    const storage = vi.fn(async (request: Request) => {
      expect(new URL(request.url).pathname).toBe(path.startsWith("/api/studio") ? "/internal/studio/import" : "/internal/product-labels");
      expect(new URL(request.url).search).toBe(path.includes("?") ? "?review=1" : "");
      expect(await request.text()).toBe("opaque payload");
      return new Response("streamed response", { status: 200 });
    });
    const response = await worker.fetch(new Request(origin + path, { method: "POST", body: "opaque payload", headers: { Cookie: cookie(), Origin: origin } }),
      { ...base, WORKSPACE: { idFromName: () => "admin", get: () => ({ fetch: storage }) } });
    expect(await response.text()).toBe("streamed response");
    expect(storage).toHaveBeenCalledTimes(1);
  });
  it("does not proxy forged label writes or internal path aliases", async () => {
    const fetch = vi.fn(async () => Response.json({ unexpected: true }));
    const env = { ...base, WORKSPACE: { idFromName: () => "admin", get: () => ({ fetch }) } };
    for (const path of ["/api/workspace/product-labels", "/api/workspace/internal/product-labels/finish",
      "/api/workspace/%70roduct-labels", "/api/workspace/../../internal/product-labels/claim", "/internal/product-labels/finish", "/internal/product-labels", "/internal/studio/import"]) {
      await worker.fetch(new Request(origin + path, { method: "PUT", headers: {
        Cookie: cookie(), Origin: origin, "X-Internal": "true", "X-Label-Write": "true",
      }, body: "{}" }), env);
    }
    expect(fetch).not.toHaveBeenCalled();
  });
  it("serves the authenticated unconfigured label workspace without exposing internal writes", async () => {
    const { workspace } = await setup();
    const response = await worker.fetch(new Request(origin + "/api/product-labels", { headers: { Cookie: cookie() } }), {
      ...base, WORKSPACE: { idFromName: () => "admin", get: () => workspace },
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ configured: false, run: null });
    expect((await workspace.fetch(new Request(origin + "/api/workspace/product-labels", { method: "PUT", body: "{}" }))).status).toBe(404);
  });
});

it("persists manual labels, rejects stale edits and preserves the override through AI labeling", async () => {
  const { workspace, input, saveSources } = await setup();
  const patch = (revision: number, group: string) => new Request(origin + "/api/product-labels", { method: "PATCH", body: JSON.stringify({ sourceRevision: revision, productId: input.products[0]!.id, group }) });
  expect((await productLabels(patch(1, "Trendy"), workspace, null)).status).toBe(200);
  expect((await productLabels(patch(1, "Normal"), workspace, null)).status).toBe(409);
  expect((await productLabels(patch(2, "Unknown"), workspace, null)).status).toBe(422);
  const provider: ProductLabelProvider = { id: "test", classify: async input => labels(input) };
  const result = await productLabels(request(2), workspace, provider);
  expect(result.status).toBe(200);
  const response = await result.json() as LabelResponse;
  expect(response.run?.results[0]).toMatchObject({ group: "Trendy", manual: true });
  const saved = await (await workspace.fetch(new Request(origin + "/api/workspace/sources"))).json() as Doc<SourcePack>;
  expect(saved.data.files.find(source => source.kind === "inventory")?.text).toContain("Trendy");
  expect((await saveSources(saved.revision, saved.data)).status).toBe(200);
  const imported = await (await workspace.fetch(new Request(origin + "/api/workspace/sources"))).json() as Doc<SourcePack>;
  expect(imported.data.files.find(source => source.kind === "inventory")?.text).toContain("Trendy");
});

it("starts event playback once at import and does not restart it when labels save", async () => {
  const { workspace, saveSources } = await setup();
  const stock = await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-11 00:00,A,Cup,9\n2026-11-11 23:00,A,Cup,1", "event");
  stock.replay = { date: "2026-11-11", hours: [0, 23], lagHours: 0 };
  expect((await saveSources(1, { schema: "hackathon.sources.v1", files: [stock] })).status).toBe(200);
  const clock = await (await workspace.fetch(new Request(origin + "/api/workspace/sandbox"))).json() as Doc<{ hour: number; startedAt: string }>;
  expect(clock.data.hour).toBe(0);
  expect(clock.data.startedAt).toBeTruthy();
  expect((await productLabels(request(2), workspace, { id: "test", classify: async input => labels(input) })).status).toBe(200);
  const after = await (await workspace.fetch(new Request(origin + "/api/workspace/sandbox"))).json();
  expect(after).toEqual(clock);
});
