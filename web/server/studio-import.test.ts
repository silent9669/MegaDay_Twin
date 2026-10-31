// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { strToU8, zipSync } from "fflate";
import { analyzeArchive, extractArchive } from "./studio-import";

afterEach(() => vi.unstubAllGlobals());
const archive = (files: Record<string, string>) => zipSync(Object.fromEntries(Object.entries(files).map(([k, v]) => [k, strToU8(v)])));
describe("backend archive investigation", () => {
  it("extracts data and reports excluded answer keys and unsupported files", () => {
    const result = extractArchive(archive({ "shop/stock.csv": "sku,name,stock\nA,Cup,2", "ground_truth/answers.csv": "secret", "readme.txt": "hello" }));
    expect(result.files.map(f => f.name)).toEqual(["shop/stock.csv"]);
    expect(result.skipped).toHaveLength(2);
  });
  it("rejects traversal and expansion bombs before producing tables", () => {
    expect(() => extractArchive(archive({ "../stock.csv": "a" }))).toThrow(/path/i);
    expect(() => extractArchive(archive({ "stock.csv": "a".repeat(21 * 1024 * 1024) }))).toThrow(/limit/i);
  });
  it("uses validated LLM mappings while preserving values and removing contact columns", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ tables: [{ file: "stock.csv", kind: "inventory", columns: [{ source: "sku", target: "Mã nội bộ" }, { source: "name", target: "Tên hàng" }, { source: "stock", target: "Tồn thực tế" }] }] }) }] }] }));
    vi.stubGlobal("fetch", fetch);
    const result = await analyzeArchive(archive({ "stock.csv": "sku,name,stock,email\nA,Cup,17,private@example.test" }), { OPENAI_API_KEY: "test", OPENAI_MODEL_IMPORT: "test-model" });
    expect(result.tables[0]?.source.text).toContain("A,Cup,17");
    expect(result.tables[0]?.source.kind).toBe("inventory");
    expect(result.tables[0]?.source.text).not.toContain("private");
    expect(fetch.mock.calls[0]?.[1]?.body).not.toContain("private");
    expect(result.tables[0]?.rowCount).toBe(1);
  });
  it("rejects invented mapping columns instead of fabricating values", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ tables: [{ file: "stock.csv", kind: "inventory", columns: [{ source: "invented", target: "Mã nội bộ" }] }] }) }] }] })));
    await expect(analyzeArchive(archive({ "stock.csv": "sku,name,stock\nA,Cup,17" }), { OPENAI_API_KEY: "test", OPENAI_MODEL_IMPORT: "test-model" })).rejects.toThrow(/mapping/i);
  });
  it("imports recognized contracts without a model and reports missing configuration", async () => {
    const result = await analyzeArchive(archive({ "inventory.csv": "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,17", "custom.csv": "sku,name,stock\nA,Cup,17" }), {});
    expect(result.tables).toHaveLength(1);
    expect(result.skipped[0]?.reason).toMatch(/model/i);
  });
});
