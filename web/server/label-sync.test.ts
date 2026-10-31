// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { syncDemandLabels } from "./label-sync";
import { buildLabelInput, sourceFingerprint, type LabelRun } from "../src/studio/product-labels";
import { makeSource, withoutDemandLabels, hashText, type SourcePack } from "../src/studio/sources";

const env = { DATA_BACKEND_URL: "https://backend.test/api/v1/", DATA_BACKEND_USER: "test", DATA_BACKEND_ROLE: "operator" };
afterEach(() => vi.unstubAllGlobals());
async function fixture() {
  const pack: SourcePack = { schema: "hackathon.sources.v1", files: [await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế,Demand label\nLOCAL-1,Áo len,3,Seasonal", "upload")] };
  const input = await buildLabelInput(pack, 4);
  const run: LabelRun = { id: "aee6b43a-8860-4526-899c-fb0238853070", context: { market: "Vietnam", asOf: "2026-10-30", evidence: [] },
    sourceRevision: 4, sourceFingerprint: input.sourceFingerprint, provider: "test", status: "completed",
    startedAt: "2026-10-30T10:00:00Z", completedAt: "2026-10-30T10:01:00Z", error: null,
    results: [{ productId: input.products[0]!.id, group: "Seasonal", reason: "Use in winter", confidence: 0.7 }] };
  const snapshot = { sources: { revision: 4, data: pack }, run };
  return { pack, snapshot, storage: { fetch: vi.fn(async () => Response.json(snapshot)) } };
}
it("sends only the saved label snapshot and canonical inventory hash, never browser labels", async () => {
  const f = await fixture();
  const fetch = vi.fn(async () => Response.json({ data: { version: 1, run_id: f.snapshot.run.id,
    accepted: [{ raw_code: "LOCAL-1", sku_id: "CANONICAL", entry_id: "saved" }], pending: [] } }));
  vi.stubGlobal("fetch", fetch);
  const response = await syncDemandLabels(new Request("https://app/api/product-labels/sync", { method: "POST", body: JSON.stringify({ label: "Trendy", sku_id: "WRONG" }) }), env, f.storage);
  expect(response.status).toBe(200);
  expect(await response.json()).toMatchObject({ status: "synced", synced: 1 });
  const [url, options] = fetch.mock.calls[0] as unknown as [URL, RequestInit];
  expect(url.pathname).toBe("/api/v1/demand-labels/sync");
  const payload = JSON.parse(String(options.body));
  expect(payload.labels).toEqual([{ raw_code: "LOCAL-1", name: "Áo len", label: "Seasonal", reason: "Use in winter", confidence: 0.7, manual: false }]);
  expect(payload.inventory_sha256).toBe(await hashText((await withoutDemandLabels(f.pack)).files[0]!.text));
  expect(payload.completed_at).toBe(f.snapshot.run.completedAt);
});
it("holds stale sources without writing to the backend", async () => {
  const f = await fixture(); f.snapshot.sources.revision++;
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  const response = await syncDemandLabels(new Request("https://app/api/product-labels/sync", { method: "POST" }), env, f.storage);
  expect(await response.json()).toMatchObject({ status: "stale" });
  expect(fetch).not.toHaveBeenCalled();
});

it("keeps manual provenance and refuses labels from a later replay hour", async () => {
  const f = await fixture();
  f.pack.files[0]!.replay = { date: "2026-10-30", hours: [10], lagHours: 0 };
  f.snapshot.run.sourceFingerprint = await sourceFingerprint(f.pack);
  f.snapshot.run.provider = "manual";
  f.snapshot.run.results[0]!.manual = true;
  f.snapshot.run.context!.eventHour = 10;
  Object.assign(f.snapshot, { hour: 9 });
  const fetch = vi.fn(async () => Response.json({ data: { version: 1, run_id: f.snapshot.run.id,
    accepted: [{ raw_code: "LOCAL-1", sku_id: "CANONICAL", entry_id: "saved" }], pending: [] } }));
  vi.stubGlobal("fetch", fetch);
  const configured = { ...env, OPENAI_API_KEY: "test-only", OPENAI_MODEL_DECISION: "test" };
  let result = await syncDemandLabels(new Request("https://app/api/product-labels/sync", { method: "POST" }), configured, f.storage);
  expect(await result.json()).toMatchObject({ status: "stale" });
  expect(fetch).not.toHaveBeenCalled();
  Object.assign(f.snapshot, { hour: 10 });
  result = await syncDemandLabels(new Request("https://app/api/product-labels/sync", { method: "POST" }), configured, f.storage);
  expect(await result.json()).toMatchObject({ status: "synced" });
  const [, options] = fetch.mock.calls[0] as unknown as [URL, RequestInit];
  const payload = JSON.parse(String(options.body));
  expect(payload.labels[0].manual).toBe(true);
  expect(payload.evidence_through).toBe("2026-10-30T04:00:00.000Z");
});
it("reports pending canonical mapping and retries by the same run identity", async () => {
  const f = await fixture();
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ data: { version: 1, run_id: f.snapshot.run.id, accepted: [], pending: [{ raw_code: "LOCAL-1", reason: "Review alias" }] } })));
  const result = await syncDemandLabels(new Request("https://app/api/product-labels/sync", { method: "POST" }), env, f.storage);
  expect(await result.json()).toMatchObject({ status: "pending", synced: 0, pending: [{ raw_code: "LOCAL-1" }] });
});
it("reads sync state without mutating and distinguishes an unavailable backend", async () => {
  const f = await fixture();
  const fetch = vi.fn(async () => Response.json({ data: { version: 1, labels: { CANONICAL: {
    run_id: f.snapshot.run.id, source_fingerprint: f.snapshot.run.sourceFingerprint, raw_code: "LOCAL-1",
  } } } })); vi.stubGlobal("fetch", fetch);
  const result = await syncDemandLabels(new Request("https://app/api/product-labels/sync"), env, f.storage);
  expect(await result.json()).toMatchObject({ status: "synced", synced: 1 });
  expect((fetch.mock.calls[0] as unknown as [URL, RequestInit])[1].method).toBe("GET");
  const disabled = await syncDemandLabels(new Request("https://app/api/product-labels/sync"), {}, f.storage);
  expect(await disabled.json()).toMatchObject({ status: "unconfigured" });
});
it("normalizes inventory serialization identically before and after adding a display label", async () => {
  const source = await makeSource("inventory.csv", 'Mã nội bộ,Tên hàng,Tồn thực tế\n"A","Cup",2\n', "upload");
  const labeled = await makeSource("inventory.csv", "Mã nội bộ,Tên hàng,Tồn thực tế,Demand label\r\nA,Cup,2,Normal", "upload");
  const plain = await withoutDemandLabels({ schema: "hackathon.sources.v1", files: [source] });
  const after = await withoutDemandLabels({ schema: "hackathon.sources.v1", files: [labeled] });
  expect(plain.files[0]!.hash).toBe(after.files[0]!.hash);
});
