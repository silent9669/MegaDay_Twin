// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { getProductLabelProvider } from "./product-label-provider";
import { buildLabelInput } from "../src/studio/product-labels";
import { makeSource } from "../src/studio/sources";
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
it("requires a separate decision model and chooses a best-fit label without evidence", async () => {
  expect(getProductLabelProvider({ OPENAI_API_KEY: "test", OPENAI_MODEL: "chat" })).toBeNull();
  const input = await buildLabelInput({ schema: "hackathon.sources.v1", files: [await makeSource("inventory.csv", "Mã nội bộ,Tồn thực tế,Tên hàng\nA,2,Cup", "upload")] }, 1);
  const fetch = vi.fn<typeof globalThis.fetch>(async () => Response.json({ status: "completed", output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ labels: [{ productId: input.products[0]!.id, group: "Normal", reason: "Estimate: no dated demand evidence; Normal has the highest comparative support among the available choices.", confidence: 0.2 }] }) }] }] }));
  vi.stubGlobal("fetch", fetch);
  const provider = getProductLabelProvider({ OPENAI_API_KEY: "test", OPENAI_MODEL_DECISION: "decision-test" })!;
  const result = await provider.classify({ ...input, context: { market: "Vietnam", asOf: "2026-10-31", evidence: [] } }, new AbortController().signal);
  expect(result).toMatchObject({ labels: [{ group: "Normal" }] });
  const body = JSON.parse(fetch.mock.calls[0]![1]!.body as string);
  expect(body.model).toBe("decision-test");
  expect(body.store).toBe(false);
  expect(body.instructions).toContain("11.11");
  expect(body.input).toContain("Vietnam");
  expect(body.text.format.schema.properties.labels.items.properties.group).toEqual({ type: "string", enum: ["Trendy", "Seasonal", "Normal"] });
  expect(provider.id).toContain("demand-v3");
});
it("rejects a null model label instead of silently leaving products unclassified", async () => {
  const input = await buildLabelInput({ schema: "hackathon.sources.v1", files: [await makeSource("stock.csv", "Mã nội bộ,Tồn thực tế,Tên hàng\nA,2,Cup", "upload")] }, 1);
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ labels: [{ productId: input.products[0]!.id, group: null, reason: "No evidence", confidence: 0 }] }) }] }] })));
  await expect(getProductLabelProvider({ OPENAI_API_KEY: "test", OPENAI_MODEL_DECISION: "test" })!.classify({ ...input, context: { market: "Vietnam", asOf: "2026-10-31", evidence: [] } }, new AbortController().signal)).rejects.toThrow("best-fit label");
});
it("batches products without mixing their evidence or accepting missing results", async () => {
  const csv = "Mã nội bộ,Tồn thực tế,Tên hàng\n" + Array.from({ length: 51 }, (_, i) => `P${i},2,Cup ${i}`).join("\n");
  const input = await buildLabelInput({ schema: "hackathon.sources.v1", files: [await makeSource("inventory.csv", csv, "upload")] }, 1);
  const calls: { products: { id: string; reference: string }[]; context: { evidence: { productCode: string }[] } }[] = [];
  vi.stubGlobal("fetch", vi.fn<typeof globalThis.fetch>(async (_url, init) => {
    const batch = JSON.parse(JSON.parse(init!.body as string).input).untrusted_evidence;
    calls.push(batch);
    return Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ labels: batch.products.map((p: { id: string }) => ({ productId: p.id, group: "Normal", reason: "Estimate: needs product evidence.", confidence: 0.2 })) }) }] }] });
  }));
  const provider = getProductLabelProvider({ OPENAI_API_KEY: "test", OPENAI_MODEL_DECISION: "decision" })!;
  await provider.classify({ ...input, context: { market: "Vietnam", asOf: "2026-10-31", evidence: [{ productCode: "P0", observedAt: "2026-10-30", source: "Report", observation: "Demand observations for P0" }] } }, new AbortController().signal);
  expect(calls).toHaveLength(2);
  expect(calls.every(c => c.context.evidence.every(e => c.products.some(p => p.reference === e.productCode)))).toBe(true);
  expect(calls.flatMap(c => c.products)).toHaveLength(51);
});
it("retains successful batches when one model call fails", async () => {
  const csv = "Mã nội bộ,Tồn thực tế,Tên hàng\n" + Array.from({ length: 51 }, (_, i) => `P${i},2,Cup ${i}`).join("\n");
  const input = await buildLabelInput({ schema: "hackathon.sources.v1", files: [await makeSource("stock.csv", csv, "upload")] }, 1);
  let calls = 0;
  vi.stubGlobal("fetch", vi.fn<typeof globalThis.fetch>(async (_url, init) => {
    if (++calls === 1) return new Response("sensitive upstream text", { status: 429 });
    const batch = JSON.parse(JSON.parse(init!.body as string).input).untrusted_evidence;
    return Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ labels: batch.products.map((p: { id: string }) => ({ productId: p.id, group: "Normal", reason: "Estimate: needs evidence.", confidence: 0.2 })) }) }] }] });
  }));
  const result = await getProductLabelProvider({ OPENAI_API_KEY: "test", OPENAI_MODEL_DECISION: "test" })!.classify({ ...input, context: { market: "Vietnam", asOf: "2026-11-12", evidence: [] } }, new AbortController().signal);
  expect(result).toMatchObject({ labels: [expect.objectContaining({ reason: "Estimate: needs evidence." })] });
});

it.each([
  { title: "accepts a valid Luna batch taking longer than 30 seconds", delay: 31000, completes: true },
  { title: "still aborts a Luna batch that exceeds 60 seconds", delay: 61000, completes: false },
])("$title", async ({ delay, completes }) => {
  const input = await buildLabelInput({ schema: "hackathon.sources.v1", files: [await makeSource("stock.csv", "Mã nội bộ,Tồn thực tế,Tên hàng\nA,2,Cup", "upload")] }, 1);
  vi.useFakeTimers();
  // Node's native AbortSignal.timeout uses real time; model it with the fake clock.
  const timeout = vi.spyOn(AbortSignal, "timeout").mockImplementation(ms => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException("Timed out", "TimeoutError")), ms);
    return controller.signal;
  });
  vi.stubGlobal("fetch", vi.fn<typeof globalThis.fetch>((_url, init) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => resolve(Response.json({ output: [{ type: "message", content: [{ type: "output_text", text: JSON.stringify({ labels: [{ productId: input.products[0]!.id, group: "Normal", reason: "Estimate from product use.", confidence: 0.2 }] }) }] }] })), delay);
    init!.signal!.addEventListener("abort", () => { clearTimeout(timer); reject(init!.signal!.reason); }, { once: true });
  })));
  try {
    const result = getProductLabelProvider({ OPENAI_API_KEY: "test", OPENAI_MODEL_DECISION: "test" })!.classify({ ...input, context: { market: "Vietnam", asOf: "2026-11-12", evidence: [] } }, new AbortController().signal);
    const assertion = completes
      ? expect(result).resolves.toMatchObject({ labels: [{ group: "Normal" }] })
      : expect(result).rejects.toThrow("Timed out");
    await Promise.all([assertion, vi.advanceTimersByTimeAsync(delay)]);
  } finally { timeout.mockRestore(); }
});
