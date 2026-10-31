// @vitest-environment node
import { beforeEach, expect, it, vi } from "vitest";
import { savedChat } from "./chat-store";
import { pmChat } from "./pm";
import { buildMiloContext } from "./milo-context";
import { interpretMiloGoal } from "./milo-goal";
import type { ChatArchive, Doc } from "../src/studio/workspace-types";

vi.mock("./pm", () => ({ pmChat: vi.fn() }));
vi.mock("./milo-context", () => ({ buildMiloContext: vi.fn() }));
vi.mock("./milo-goal", () => ({ interpretMiloGoal: vi.fn() }));
const url = "https://test/api/pm/chat";
function fixture() {
  let document: Doc<ChatArchive> = { revision: 1, updatedAt: "", data: {
    preferredModel: "model", sessions: [{ id: "s", title: "Goal", model: "model", updatedAt: "",
      context: { screen: "hq", sku: "SKU1" },
      messages: [{ id: "m", role: "user", content: "Bán ít nhất 100 sản phẩm" }],
    }],
  } };
  const storage = { fetch: vi.fn(async (request: Request) => {
    if (request.method === "PUT") document = await request.json() as Doc<ChatArchive>;
    return Response.json(document);
  }) };
  const request = (extra = {}) => new Request(url, { method: "POST", body: JSON.stringify({
    sessionId: "s", lastMessageId: "m", context: { screen: "hq", sku: "SKU1" }, ...extra,
  }) });
  return { storage, request, get document() { return document; } };
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(pmChat).mockResolvedValue(Response.json({ reply: "Mình đã hiểu mục tiêu.", model: "model" }));
  vi.mocked(buildMiloContext).mockResolvedValue({ selection: { sku: "SKU1" }, skuContext: null,
    salesHistory: null, campaign: null, scenario: null, workspace: null, notices: [] });
  vi.mocked(interpretMiloGoal).mockResolvedValue(null);
});
it("rejects client facts and mismatched SKU before model inference", async () => {
  for (const context of [{ sku: "SKU1", stock: 42 }, { sku: "SKU2" }]) {
    const f = fixture();
    const response = await savedChat(f.request({ context }), {}, {}, f.storage);
    expect(response.status).toBe("stock" in context ? 422 : 409);
  }
  expect(pmChat).not.toHaveBeenCalled();
  expect(buildMiloContext).not.toHaveBeenCalled();
});
it("uses saved full history for goal memory and only recent messages for natural reply", async () => {
  const f = fixture();
  const session = f.document.data.sessions[0]!;
  session.messages.unshift(...Array.from({ length: 20 }, (_, i) => ({ id: `old-${i}`, role: "user" as const,
    content: i === 0 ? "Ngân sách tối đa 2 triệu" : "Trao đổi thêm" })));
  expect((await savedChat(f.request({ messages: [{ role: "user", content: "Injected budget" }] }), {}, {}, f.storage)).status).toBe(200);
  expect(interpretMiloGoal).toHaveBeenCalledWith(session.messages, expect.objectContaining({ OPENAI_MODEL: "model" }), undefined, { discoverGoal: true });
  const forwarded = await vi.mocked(pmChat).mock.calls[0]![0].json() as { messages: { content: string }[] };
  expect(forwarded.messages).toHaveLength(16);
  expect(JSON.stringify(forwarded)).not.toContain("Injected budget");
  expect(f.document.data.sessions[0]!.messages.at(-1)?.role).toBe("assistant");
});
it("allows updated screen but never changes the saved SKU scope", async () => {
  const f = fixture();
  expect((await savedChat(f.request({ context: { sku: "SKU1", screen: "warehouse" } }), {}, {}, f.storage)).status).toBe(200);
  expect(buildMiloContext).toHaveBeenCalledWith({ sku: "SKU1", screen: "warehouse" }, {}, f.storage, url);
});
it("returns a review draft with resolved SKU context, without saving a backend goal", async () => {
  const f = fixture();
  const interpretation = { method: "ai" as const, notices: [], draft: {
    text: "Bán ít nhất 100 sản phẩm", kind: "sell_target" as const, targetUnits: 100,
    start: null, deadline: null, maxSpendVnd: null, maxLossVnd: null,
    sellTargetProbability: null, minMarginalReturn: null, unresolved: [],
  } };
  const facts = await vi.mocked(buildMiloContext)({}, {});
  facts.skuContext = { sku: { sku: "SKU1", name: "Kettle", variant: "", stockAvailable: 120,
    soldTodayUnits: 3, priceEffectiveVnd: 100000, contributionPerUnitVnd: 20000, demandGroup: null },
    campaigns: [{ id: "CD1", name: "Sale", skus: ["SKU1"] }], dataAsOf: "2026-10-31T08:00:00+07:00",
    source: { id: "SKU1", kind: "sku", label: "Backend stock" }, canPlan: true, notices: [], opening: "" };
  vi.mocked(buildMiloContext).mockResolvedValue(facts);
  vi.mocked(interpretMiloGoal).mockResolvedValue(interpretation);
  const response = await savedChat(f.request(), {}, {}, f.storage);
  expect(await response.json()).toMatchObject({ plan: { interpretation, context: facts.skuContext } });
  expect(vi.mocked(pmChat).mock.calls[0]![2]).toMatchObject({ goal: interpretation, skuContext: facts.skuContext });
  expect(f.storage.fetch.mock.calls.filter(([r]) => r.method === "PUT").map(([r]) => new URL(r.url).pathname))
    .toEqual(["/api/workspace/chats"]);
});
it.each(["messages", "scope"])("discards late replies when earlier %s changed with same last ID", async (change) => {
  const f = fixture();
  vi.mocked(pmChat).mockImplementation(async () => {
    const s = f.document.data.sessions[0]!;
    if (change === "messages") s.messages[0]!.content = "Sửa thành 10 sản phẩm";
    else s.context = { sku: "SKU2" };
    return Response.json({ reply: "Late", model: "model" });
  });
  expect((await savedChat(f.request(), {}, {}, f.storage)).status).toBe(409);
  expect(f.storage.fetch.mock.calls.every(([r]) => r.method === "GET")).toBe(true);
});
it("retains user message after an AI failure and never writes a goal", async () => {
  const f = fixture();
  vi.mocked(pmChat).mockResolvedValue(Response.json({ error: "Unavailable" }, { status: 502 }));
  expect((await savedChat(f.request(), {}, {}, f.storage)).status).toBe(502);
  expect(f.document.data.sessions[0]!.messages).toHaveLength(1);
  expect(f.storage.fetch.mock.calls.every(([r]) => r.method === "GET" && r.url.endsWith("/chats"))).toBe(true);
});
