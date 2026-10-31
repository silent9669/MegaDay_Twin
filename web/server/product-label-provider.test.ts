// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { getProductLabelProvider } from "./product-label-provider";
import { buildLabelInput } from "../src/studio/product-labels";
import { makeSource } from "../src/studio/sources";
afterEach(() => vi.unstubAllGlobals());
it("requires a separate decision model and keeps missing evidence unclassified", async () => {
  expect(getProductLabelProvider({ OPENAI_API_KEY: "test", OPENAI_MODEL: "chat" })).toBeNull();
  const input = await buildLabelInput({ schema: "hackathon.sources.v1", files: [await makeSource("inventory.csv", "Mã nội bộ,Tồn thực tế,Tên hàng\nA,2,Cup", "upload")] }, 1);
  const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ labels: [{ productId: input.products[0]!.id, group: null, reason: "Need dated product demand evidence", confidence: 0 }] }) }] }] }));
  vi.stubGlobal("fetch", fetch);
  const provider = getProductLabelProvider({ OPENAI_API_KEY: "test", OPENAI_MODEL_DECISION: "decision-test" })!;
  const result = await provider.classify({ ...input, context: { market: "Vietnam", asOf: "2026-10-31", evidence: [] } }, new AbortController().signal);
  expect(result).toMatchObject({ labels: [{ group: null }] });
  const body = JSON.parse(fetch.mock.calls[0]![1]!.body as string);
  expect(body.model).toBe("decision-test");
  expect(body.store).toBe(false);
  expect(body.instructions).toContain("11.11");
  expect(body.input).toContain("Vietnam");
});
it("batches products without mixing their evidence or accepting missing results", async () => {
  const csv = "Mã nội bộ,Tồn thực tế,Tên hàng\n" + Array.from({ length: 26 }, (_, i) => `P${i},2,Cup ${i}`).join("\n");
  const input = await buildLabelInput({ schema: "hackathon.sources.v1", files: [await makeSource("inventory.csv", csv, "upload")] }, 1);
  const calls: { products: { id: string; reference: string }[]; context: { evidence: { productCode: string }[] } }[] = [];
  vi.stubGlobal("fetch", vi.fn<typeof globalThis.fetch>(async (_url, init) => {
    const batch = JSON.parse(JSON.parse(init!.body as string).input).untrusted_evidence;
    calls.push(batch);
    return Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ labels: batch.products.map((p: { id: string }) => ({ productId: p.id, group: null, reason: "Needs product evidence", confidence: 0 })) }) }] }] });
  }));
  const provider = getProductLabelProvider({ OPENAI_API_KEY: "test", OPENAI_MODEL_DECISION: "decision" })!;
  await provider.classify({ ...input, context: { market: "Vietnam", asOf: "2026-10-31", evidence: [{ productCode: "P0", observedAt: "2026-10-30", source: "Report", observation: "Demand observations for P0" }] } }, new AbortController().signal);
  expect(calls).toHaveLength(2);
  expect(calls.every(c => c.context.evidence.every(e => c.products.some(p => p.reference === e.productCode)))).toBe(true);
  expect(calls.flatMap(c => c.products)).toHaveLength(26);
});
