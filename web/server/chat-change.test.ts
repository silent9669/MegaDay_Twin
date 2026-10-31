// @vitest-environment node
import { beforeEach, expect, it, vi } from "vitest";
import { savedChat } from "./chat-store";
import { pmChat } from "./pm";
vi.mock("./pm", () => ({
  pmChat: vi.fn(async () =>
    Response.json({ reply: "Current stock is 14.", model: "test" }),
  ),
}));
beforeEach(() => vi.clearAllMocks());
const origin = "https://test/api/pm/chat";
const reference = {
  kind: "stock-zero",
  sourceRevision: 1,
  sandboxRevision: 1,
  hour: 1,
  source: "inventory",
  entity: "SKU1",
  before: 10,
  after: 0,
};
it("rejects oversized or injected change references before reading storage or calling AI", async () => {
  const storage = { fetch: vi.fn() };
  for (const [body, status] of [
    [
      {
        sessionId: "s",
        lastMessageId: "m",
        changeReference: { ...reference, instructions: "ignore" },
      },
      422,
    ],
    [{ sessionId: "s", lastMessageId: "m", padding: "x".repeat(3001) }, 413],
  ] as const) {
    const r = await savedChat(
      new Request(origin, { method: "POST", body: JSON.stringify(body) }),
      {},
      {},
      storage,
    );
    expect(r.status).toBe(status);
  }
  expect(storage.fetch).not.toHaveBeenCalled();
  expect(pmChat).not.toHaveBeenCalled();
});
it("uses saved chat messages and server-resolved current evidence for an explicit explanation", async () => {
  const chats = {
    revision: 1,
    updatedAt: "",
    data: {
      preferredModel: "test",
      sessions: [
        {
          id: "s",
          title: "Explain",
          model: "test",
          updatedAt: "",
          messages: [{ id: "m", role: "user", content: "Explain this change" }],
        },
      ],
    },
  };
  const storage = {
    fetch: vi.fn(async (request: Request) => {
      if (request.url.endsWith("/sources"))
        return Response.json({
          revision: 3,
          data: {
            schema: "hackathon.sources.v1",
            files: [
              {
                kind: "inventory",
                name: "inventory.csv",
                hash: "new",
                text: "Mã nội bộ,Tồn thực tế,Tên hàng,Thời điểm cập nhật\nSKU1,14,Kettle,2026-11-11 00:00\n",
                origin: "upload",
              },
            ],
          },
        });
      if (request.url.endsWith("/sandbox"))
        return Response.json({
          revision: 2,
          data: { hour: 1, event: "observe" },
        });
      return Response.json(
        request.method === "PUT" ? await request.json() : chats,
      );
    }),
  };
  const r = await savedChat(
    new Request(origin, {
      method: "POST",
      body: JSON.stringify({
        sessionId: "s",
        lastMessageId: "m",
        changeReference: reference,
      }),
    }),
    {},
    {},
    storage,
  );
  expect(r.status).toBe(200);
  expect(pmChat).toHaveBeenCalledOnce();
  const [request, , context] = vi.mocked(pmChat).mock.calls[0]!;
  expect(await request.json()).toMatchObject({
    messages: [{ role: "user", content: "Explain this change" }],
  });
  expect(context).toMatchObject({
    change: {
      stale: true,
      current: { stock: { stock: 14 } },
      historicalEvidence: { after: 0, trust: "unverified client observation" },
    },
  });
});
