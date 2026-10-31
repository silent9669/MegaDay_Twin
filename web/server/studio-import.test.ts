// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { strToU8, zipSync } from "fflate";
import { analyzeArchive, extractArchive, studioImport } from "./studio-import";

afterEach(() => vi.unstubAllGlobals());
const archive = (files: Record<string, string>) => zipSync(Object.fromEntries(Object.entries(files).map(([k, v]) => [k, strToU8(v)])));
it("does not claim AI review succeeded when the model connection is missing", async () => {
  const result = await studioImport(new Request("https://app.test/api/studio/import?review=1", { method: "POST", body: "zip" }), {});
  expect(result.status).toBe(503);
  expect(await result.text()).toContain("AI review is not configured");
});
describe("backend archive investigation", () => {
  it("reviews recognized tables in one call using bounded samples, and exposes review issues", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ corrections: [], tables: [], reviews: [{ file: "stock.csv", issues: ["Check negative stock"] }] }) }] }] }));
    vi.stubGlobal("fetch", fetch);
    const csv = "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,-1\nB,Lamp,2\nC,NotInSample,3";
    const result = await analyzeArchive(archive({ "stock.csv": csv }), { OPENAI_API_KEY: "test", OPENAI_MODEL_IMPORT: "test" });
    expect(result.tables[0]?.review).toEqual(["Check negative stock"]);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0]?.[1]?.body).not.toContain("NotInSample");
    expect(result.tables[0]?.source.text).toContain("NotInSample");
  });
  it("keeps timezone-free ad clocks unchanged and out of remaining repairs", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ corrections: [], tables: [], reviews: [{ file: "ads.csv", issues: [] }] }) }] }] }));
    vi.stubGlobal("fetch", fetch);
    const csv = "Mã chiến dịch,Chi phí,Lượt click,Thời gian\nA,100,1,2026-11-11 01:00";
    const result = await analyzeArchive(archive({ "ads.csv": csv }), { OPENAI_API_KEY: "test", OPENAI_MODEL_IMPORT: "test" });
    expect(result.tables[0]?.source.text).toContain("2026-11-11 01:00");
    expect(result.tables[0]?.repairs).toEqual([]);
    expect(result.tables[0]?.remainingIssues).toEqual([]);
    const body = JSON.parse(fetch.mock.calls[0]![1]!.body as string);
    expect(JSON.parse(body.input).untrusted_evidence.reviewTables[0].checks).toContainEqual({ code: "TIMEZONE_REVIEW", level: "info", column: "Thời gian" });
  });
  it("rejects a missing review rather than claiming the table was checked", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: '{"tables":[],"reviews":[],"corrections":[]}' }] }] })));
    await expect(analyzeArchive(archive({ "stock.csv": "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,1" }), { OPENAI_API_KEY: "test", OPENAI_MODEL_IMPORT: "test" })).rejects.toThrow("Incomplete model table review");
  });
  it("retains multiple general CSV/JSON tables with all record fields after model abstention", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ corrections: [], tables: [], reviews: ["one/events.csv", "one%2Fevents.csv", "notes.json"].map(file => ({ file, issues: [] })) }) }] }] })));
    const result = await analyzeArchive(archive({
      "one/events.csv": "event,month\nTet,2",
      "one%2Fevents.csv": "event,month\nSummer,6",
      "notes.json": '[{"item":"A","email":"private@example.test"},{"item":"B","extra":2}]',
    }), { OPENAI_API_KEY: "test", OPENAI_MODEL_IMPORT: "test" });
    expect(result.tables.map(t => t.source.kind)).toEqual(["general", "general", "general"]);
    expect(new Set(result.tables.map(t => t.source.name)).size).toBe(3);
    expect(result.tables[2]?.columns.map(c => c.target)).toEqual(["item", "extra"]);
    expect(result.tables[2]?.source.text).not.toContain("private");
    expect(result.skipped).toEqual([]);
  });
  it("extracts data and reports excluded answer keys and unsupported files", () => {
    const result = extractArchive(archive({ "shop/stock.csv": "sku,name,stock\nA,Cup,2", "ground_truth/answers.csv": "secret", "readme.txt": "hello" }));
    expect(result.files.map(f => f.name)).toEqual(["shop/stock.csv"]);
    expect(result.skipped).toHaveLength(2);
  });
  it("rejects traversal and expansion bombs before producing tables", () => {
    expect(() => extractArchive(archive({ "../stock.csv": "a" }))).toThrow(/path/i);
    expect(() => extractArchive(archive({ "stock.csv": "a".repeat(33 * 1024 * 1024) }))).toThrow(/limit/i);
  });
  it("uses validated LLM mappings while preserving values and removing contact columns", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ corrections: [], reviews: [{ file: "stock.csv", issues: [] }], tables: [{ file: "stock.csv", kind: "inventory", columns: [{ source: "sku", target: "Mã nội bộ" }, { source: "name", target: "Tên hàng" }, { source: "stock", target: "Tồn thực tế" }] }] }) }] }] }));
    vi.stubGlobal("fetch", fetch);
    const result = await analyzeArchive(archive({ "stock.csv": "sku,name,stock,email,location\nA,Cup,17,private@example.test,Hanoi" }), { OPENAI_API_KEY: "test", OPENAI_MODEL_IMPORT: "test-model" });
    expect(result.tables[0]?.source.text).toContain("A,Cup,17");
    expect(result.tables[0]?.source.kind).toBe("inventory");
    expect(result.tables[0]?.source.text).toContain("location");
    expect(result.tables[0]?.source.text).toContain("Hanoi");
    expect(result.tables[0]?.source.text).not.toContain("private");
    expect(fetch.mock.calls[0]?.[1]?.body).not.toContain("private");
    expect(result.tables[0]?.rowCount).toBe(1);
  });
  it("rejects invented mapping columns instead of fabricating values", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ corrections: [], reviews: [{ file: "stock.csv", issues: [] }], tables: [{ file: "stock.csv", kind: "inventory", columns: [{ source: "invented", target: "Mã nội bộ" }] }] }) }] }] })));
    await expect(analyzeArchive(archive({ "stock.csv": "sku,name,stock\nA,Cup,17" }), { OPENAI_API_KEY: "test", OPENAI_MODEL_IMPORT: "test-model" })).rejects.toThrow(/mapping/i);
  });
  it("imports recognized contracts without a model and retains general tables without model configuration", async () => {
    const result = await analyzeArchive(archive({ "inventory.csv": "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,17", "custom.csv": "sku,name,stock\nA,Cup,17" }), {});
    expect(result.tables).toHaveLength(2);
    expect(result.tables[1]?.source.kind).toBe("general");
    expect(result.skipped).toEqual([]);
  });
});

it("fast-imports recognized tables even when the AI connection fails", async () => {
  const fetch = vi.fn(async () => { throw Error("offline"); });
  vi.stubGlobal("fetch", fetch);
  const result = await analyzeArchive(archive({ "live_11_11/stock.csv": "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,1" }), { OPENAI_API_KEY: "test", OPENAI_MODEL_IMPORT: "test" }, false);
  expect(result.tables).toHaveLength(1);
  expect(result.skipped).toHaveLength(0);
  expect(fetch).not.toHaveBeenCalled();
});

it("applies explicit AI cell corrections to the returned table before saving", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({
    tables: [], reviews: [{ file: "stock.csv", issues: [] }],
    corrections: [{ file: "stock.csv", edits: [{ row: 0, column: "Tên hàng", before: "Cupp", after: "Cup", reason: "Same product code uses Cup in the other supplied row." }] }],
  }) }] }] })));
  const result = await analyzeArchive(archive({ "stock.csv": "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cupp,2\nA,Cup,3" }), { OPENAI_API_KEY: "test", OPENAI_MODEL_IMPORT: "test" });
  expect(result.tables[0]?.source.text).toContain("A,Cup,2");
  expect(result.tables[0]?.source.text).not.toContain("Cupp");
  expect(result.tables[0]?.rowCount).toBe(2);
  expect(result.tables[0]?.repairs?.[0]).toContain("AI corrected row 1");
});
it("rejects AI patches for rows outside the reviewed samples", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({
    tables: [], reviews: [{ file: "notes.csv", issues: [] }],
    corrections: [{ file: "notes.csv", edits: [{ row: 2, column: "name", before: "Cupp", after: "Cup", reason: "Typo" }] }],
  }) }] }] })));
  await expect(analyzeArchive(archive({ "notes.csv": "name,value\nA,1\nB,2\nCupp,3" }), { OPENAI_API_KEY: "test", OPENAI_MODEL_IMPORT: "test" })).rejects.toThrow(/outside the reviewed samples/);
});


it("rejects malformed replay metadata instead of exposing all event rows", async () => {
  await expect(analyzeArchive(archive({ "stock.csv": "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,1", "_replay.json": '{"rows":[]}' }), {}, false)).rejects.toThrow();
});
