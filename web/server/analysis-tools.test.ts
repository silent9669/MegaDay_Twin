// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { workspaceReader } from "./analysis-tools";
import { pmChat } from "./pm";
import { imageAttachmentSchema } from "../src/city/chat-evidence";
const snapshot = {
  revision: 4,
  updatedAt: "2026-10-31T00:00:00Z",
  data: {
    schema: "hackathon.sources.v1",
    files: [
      {
        name: "stock.csv",
        kind: "general",
        hash: "hash",
        origin: "upload",
        text:
          "sku,stock\n" +
          Array.from({ length: 40 }, (_, i) => `A${i},${i}`).join("\n"),
      },
    ],
  },
};
describe("Milo evidence", () => {
  it("filters replay rows without renumbering original source citations", async () => {
    const source = { ...snapshot.data.files[0]!, replay: { date: "2026-11-11", lagHours: 1, hours: Array.from({ length: 40 }, (_, i) => i % 2 ? 5 : -1) } };
    const storage = { fetch: vi.fn(async (request: Request) => Response.json(request.url.endsWith("/sources")
      ? { ...snapshot, data: { ...snapshot.data, files: [source] } }
      : { revision: 1, data: { hour: 2, startedAt: null } })) };
    const read = workspaceReader(storage, "https://test");
    const catalog = JSON.parse(await read("workspace_catalog", "{}"));
    expect(catalog.files[0].records).toBe(20);
    const page = JSON.parse(await read("read_source_rows", JSON.stringify({ file: "stock.csv", query: "", offset: 0 })));
    expect(page.rows).toHaveLength(20);
    expect(page.rows[1]).toEqual({ record: 3, value: { sku: "A2", stock: "2" } });
    expect(page.totalRecords).toBe(20);
  });
  it("streams quoted CSV cells and caps oversized rows while keeping pagination", async () => {
    const text = 'sku,note\nA,"line one\nline two"\nB,' + "x".repeat(21000) + '\nC,end';
    const storage = { fetch: async () => Response.json({ ...snapshot, data: { ...snapshot.data, files: [{ ...snapshot.data.files[0], text }] } }) };
    const read = workspaceReader(storage, "https://test");
    const first = JSON.parse(await read("read_source_rows", JSON.stringify({ file: "stock.csv", query: "", offset: 0 })));
    expect(first.rows[0].value.note).toBe("line one\nline two");
    expect(first.matchingRecords).toBe(3);
    expect(first.nextOffset).toBe(1);
    const second = JSON.parse(await read("read_source_rows", JSON.stringify({ file: "stock.csv", query: "", offset: 1 })));
    expect(second.rows[0]).toMatchObject({ record: 2, truncated: true });
    expect(second.nextOffset).toBe(2);
  });
  it("reads all uploaded records through pagination, with stable revision and citations", async () => {
    const storage = {
      fetch: vi.fn(async (request: Request) =>
        request.url.endsWith("/sources")
          ? Response.json(snapshot)
          : Response.json({ revision: 0, data: {} }),
      ),
    };
    const read = workspaceReader(storage, "https://test");
    const catalog = JSON.parse(await read("workspace_catalog", "{}"));
    expect(catalog.files[0]).toMatchObject({
      name: "stock.csv",
      records: 40,
      columns: ["sku", "stock"],
    });
    const first = JSON.parse(
      await read(
        "read_source_rows",
        JSON.stringify({ file: "stock.csv", query: "", offset: 0 }),
      ),
    );
    const last = JSON.parse(
      await read(
        "read_source_rows",
        JSON.stringify({
          file: "stock.csv",
          query: "",
          offset: first.nextOffset,
        }),
      ),
    );
    expect(first.rows).toHaveLength(30);
    expect(last.rows).toHaveLength(10);
    expect(last.rows[9]).toEqual({
      record: 40,
      value: { sku: "A39", stock: "39" },
    });
    expect(last.nextOffset).toBeNull();
    expect(last.revision).toBe(4);
    const result = JSON.parse(
      await read(
        "read_source_rows",
        JSON.stringify({ file: "stock.csv", query: "A35", offset: 0 }),
      ),
    );
    expect(result.rows[0].record).toBe(36);
    expect(JSON.parse(await read("delete_source", "{}")).error).toBeDefined();
    expect(
      storage.fetch.mock.calls.filter(([r]) => r.url.endsWith("/sources")),
    ).toHaveLength(1);
  });
  it("rejects remote URLs, SVG and mismatched image signatures", () => {
    for (const dataUrl of [
      "https://test/image.png",
      "data:image/svg+xml;base64,PHN2Zz4=",
      "data:image/png;base64,YmFk",
    ])
      expect(
        imageAttachmentSchema.safeParse({ name: "image", dataUrl }).success,
      ).toBe(false);
  });
  it("passes saved image/context to Responses and completes a read-only tool loop", async () => {
    const calls: Record<string, unknown>[] = [];
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/models"))
        return Response.json({ data: [{ id: "gpt-4.1", created: 1 }] });
      calls.push(JSON.parse(String(init?.body)));
      return Response.json({
        output:
          calls.length === 1
            ? [
                {
                  type: "function_call",
                  name: "workspace_catalog",
                  arguments: "{}",
                  call_id: "call_1",
                },
              ]
            : [
                {
                  type: "message",
                  content: [
                    {
                      type: "output_text",
                      text: "stock.csv record 1, revision 4: 0 units.",
                    },
                  ],
                },
              ],
      });
    });
    vi.stubGlobal("fetch", fetcher);
    try {
      const read = vi.fn(async () => JSON.stringify(snapshot));
      const image = "data:image/png;base64,iVBORw0KGgo=";
      const response = await pmChat(
        new Request("https://test/chat", {
          method: "POST",
          body: JSON.stringify({
            model: "gpt-4.1",
            messages: [
              {
                role: "user",
                content: "Explain",
                analysis: {
                  title: "Test",
                  source: "user-assumptions",
                  data: '{"stock":0}',
                },
                attachments: [{ name: "chart.png", dataUrl: image }],
              },
            ],
          }),
        }),
        { OPENAI_API_KEY: "test" },
        {},
        read,
      );
      expect(response.status).toBe(200);
      expect(read).toHaveBeenCalledWith("workspace_catalog", "{}");
      expect(JSON.stringify(calls[0]?.input)).toContain("input_image");
      expect(JSON.stringify(calls[0]?.input)).toContain("user-assumptions");
      expect(JSON.stringify(calls[1]?.input)).toContain("function_call_output");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
