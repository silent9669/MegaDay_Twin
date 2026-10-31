// @vitest-environment node
import { DatabaseSync } from "node:sqlite";
import type { DurableObjectState } from "@cloudflare/workers-types";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AccountWorkspace } from "../../server/workspace";
import { savedChat } from "../../server/chat-store";
import Papa from "papaparse";
import rawPack from "../../server/repository-sources.json";
import { hashText, makeSource } from "./sources";
import { defaultSettings } from "./workspace-types";
import { mergeReplaySources } from "./replay";
import { sourceText } from "./source-text";
function setup(env: ConstructorParameters<typeof AccountWorkspace>[1] = {}) {
  const database = new DatabaseSync(":memory:");
  const sent: string[] = [];
  let writesBlocked = false;
  const state = {
    storage: {
      sql: {
        exec: (query: string, ...bindings: (string | number)[]) => {
          if (writesBlocked && !query.startsWith("SELECT")) throw Error("Exceeded allowed rows written in Durable Objects free tier.");
          const result = database.prepare(query);
          if (query.startsWith("SELECT"))
            return { toArray: () => result.all(...bindings) };
          result.run(...bindings);
          return { toArray: () => [] };
        },
      },
      transactionSync: <T>(callback: () => T) => {
        database.exec("BEGIN");
        try {
          const result = callback();
          database.exec("COMMIT");
          return result;
        } catch (e) {
          database.exec("ROLLBACK");
          throw e;
        }
      },
    },
    getWebSockets: () => [
      {
        send: (message: string) => sent.push(message),
        deserializeAttachment: () => ({ expires: Date.now() + 10000 }),
      },
    ],
  };
  return {
    blockWrites: () => { writesBlocked = true; },
    reconstruct: () =>
      new AccountWorkspace(state as unknown as DurableObjectState, env),
    workspace: new AccountWorkspace(state as unknown as DurableObjectState, env),
    database,
    sent,
  };
}
const req = (name: string, data?: unknown, revision = 1) =>
  new Request(
    `https://workspace.test/api/workspace/${name}`,
    data ? { method: "PUT", body: JSON.stringify({ data, revision }) } : {},
  );
afterEach(() => vi.unstubAllGlobals());
describe("persistent account documents", () => {
  it("persists GD0003 restock once and retries backend sync without adding stock twice", async () => {
    const upstream = vi.fn(async () => new Response("unavailable", { status: 503 }));
    const { workspace, reconstruct, database } = setup({ DECISION_BACKEND: { fetch: upstream } });
    const file = await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-10,GD0003,Fryer,2", "shop");
    await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [file] }, 0));
    const operationId = crypto.randomUUID();
    const restock = (revision = 1, id = operationId) => new Request("https://workspace.test/internal/studio/restock", {
      method: "POST", body: JSON.stringify({ revision, operationId: id }),
    });
    expect((await (await workspace.fetch(restock())).json())).toMatchObject({ revision: 2, backend: "pending", change: { before: 2, after: 12 } });
    upstream.mockImplementation(async () => Response.json({ data: { batches: [{ status: "loaded", rows_held: 0 }] } }));
    expect((await (await reconstruct().fetch(restock())).json())).toMatchObject({ revision: 2, backend: "synced", change: { before: 2, after: 12 } });
    await workspace.fetch(restock());
    expect(upstream).toHaveBeenCalledTimes(2);
    const saved = await (await reconstruct().fetch(req("sources"))).json();
    expect(saved.revision).toBe(2);
    expect(sourceText(saved.data.files[0])).toContain("GD0003,Fryer,12");
    expect((await workspace.fetch(restock(1, crypto.randomUUID()))).status).toBe(409);
    database.close();
  });
  it("keeps a static shop snapshot through event edits, reloads and separate deletions", async () => {
    const { workspace, reconstruct, database } = setup();
    const baseline = await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-10,A,Cup,10", "shop");
    const event = { ...await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-11,A,Cup,6", "event"), replay: { date: "2026-11-11", hours: [0], lagHours: 0 } };
    const save = (files: typeof baseline[], revision: number) => workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files }, revision));
    const remove = (scope: string, revision: number) => reconstruct().fetch(new Request("https://workspace.test/api/workspace/sources", { method: "DELETE", body: JSON.stringify({ scope, revision }) }));
    expect((await save([baseline], 0)).status).toBe(200);
    const mixed = await mergeReplaySources([baseline], [event]);
    expect((await save(mixed, 1)).status).toBe(200);
    const snapshot = database.prepare("SELECT content FROM chunks WHERE name = 'shop-sources' ORDER BY position").all().map(row => row.content).join("");
    expect(JSON.parse(snapshot).files[0].hash).toBe(baseline.hash);
    expect((await remove("event", 1)).status).toBe(409);
    const restored = await (await remove("event", 2)).json();
    expect(restored.data.files[0].hash).toBe(baseline.hash);
    expect(restored.data.files[0].replay).toBeUndefined();
    expect((await (await reconstruct().fetch(req("sandbox"))).json()).data.startedAt).toBeUndefined();
    expect((await save(mixed, 3)).status).toBe(200);
    const eventOnly = await (await remove("shop", 4)).json();
    expect(eventOnly.data.files[0].replay.hours).toEqual([0]);
    expect(sourceText(eventOnly.data.files[0])).not.toContain("2026-11-10");
    expect((await (await reconstruct().fetch(req("sandbox"))).json()).data.startedAt).toBeDefined();
    expect((await (await remove("event", 5)).json()).data.files).toEqual([]);
    expect((await (await reconstruct().fetch(req("sources"))).json()).data.files).toEqual([]);
    database.close();
  });
  it("imports shop data alongside an event without dropping event rows or restarting playback", async () => {
    const { workspace, database } = setup();
    const baseline = await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-10,A,Cup,10", "shop");
    const event = { ...await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-11,A,Cup,6", "event"), replay: { date: "2026-11-11", hours: [0], lagHours: 0 } };
    await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [event] }, 0));
    const clock = await (await workspace.fetch(req("sandbox"))).json();
    const response = await workspace.fetch(new Request("https://workspace.test/api/workspace/sources", {
      method: "PUT", body: JSON.stringify({ revision: 1, scope: "shop", data: { schema: "hackathon.sources.v1", files: [baseline] } }),
    }));
    expect(response.status).toBe(200);
    expect((await response.json()).data.files[0].replay.hours).toEqual([-1, 0]);
    expect(await (await workspace.fetch(req("sandbox"))).json()).toEqual(clock);
    const removed = await workspace.fetch(new Request("https://workspace.test/api/workspace/sources", { method: "DELETE", body: JSON.stringify({ scope: "event", revision: 2 }) }));
    expect((await removed.json()).data.files[0].hash).toBe(baseline.hash);
    database.close();
  });
  it("migrates an existing combined workspace once and keeps its shop snapshot through event edits", async () => {
    const { workspace, reconstruct, database } = setup();
    const baseline = await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-10,A,Cup,10", "shop");
    const event = { ...await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-11,A,Cup,6", "event"), replay: { date: "2026-11-11", hours: [0], lagHours: 0 } };
    const files = await mergeReplaySources([baseline], [event]);
    await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files }, 0));
    database.prepare("DELETE FROM chunks WHERE name = 'shop-sources'").run();
    database.prepare("DELETE FROM documents WHERE name = 'shop-sources'").run();
    await reconstruct().fetch(req("sources"));
    database.exec("CREATE TRIGGER protect_shop BEFORE DELETE ON chunks WHEN OLD.name = 'shop-sources' BEGIN SELECT RAISE(ABORT, 'shop snapshot rewritten'); END");
    await reconstruct().fetch(req("sources"));
    const edited = await makeSource("stock.csv", sourceText(files[0]!).replace(",6", ",3"), "edited");
    expect((await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [{ ...edited, replay: files[0]!.replay }] }, 1))).status).toBe(200);
    database.exec("DROP TRIGGER protect_shop");
    const removed = await workspace.fetch(new Request("https://workspace.test/api/workspace/sources", { method: "DELETE", body: JSON.stringify({ scope: "event", revision: 2 }) }));
    expect(sourceText((await removed.json()).data.files[0])).toContain("2026-11-10,A,Cup,10");
    database.close();
  });
  it("rolls scoped deletion back if updating the playback clock fails", async () => {
    const { workspace, database } = setup();
    const baseline = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,10", "shop");
    const event = { ...baseline, replay: { date: "2026-11-11", hours: [0], lagHours: 0 } };
    await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [baseline] }, 0));
    await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: await mergeReplaySources([baseline], [event]) }, 1));
    const before = await (await workspace.fetch(req("sources"))).json();
    database.exec("CREATE TRIGGER reject_clock BEFORE DELETE ON chunks WHEN OLD.name = 'sandbox' BEGIN SELECT RAISE(ABORT, 'clock failure'); END");
    const removed = await workspace.fetch(new Request("https://workspace.test/api/workspace/sources", { method: "DELETE", body: JSON.stringify({ scope: "event", revision: 2 }) }));
    expect(removed.status).toBe(422);
    expect(await (await workspace.fetch(req("sources"))).json()).toEqual(before);
    expect((await (await workspace.fetch(req("sandbox"))).json()).data.startedAt).toBeDefined();
    database.close();
  });
  it("stops event playback atomically when baseline tables replace the replay", async () => {
    const { workspace, reconstruct, database } = setup();
    const file = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nA,Cup,2", "upload");
    const event = { ...file, replay: { date: "2026-11-11", hours: [0], lagHours: 0 } };
    expect((await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [event] }, 0))).status).toBe(200);
    expect(await (await workspace.fetch(req("sandbox"))).json()).toMatchObject({ data: { startedAt: expect.any(String) } });
    expect((await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [file] }, 0))).status).toBe(409);
    expect(await (await workspace.fetch(req("sandbox"))).json()).toMatchObject({ data: { startedAt: expect.any(String) } });
    expect((await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [file] }, 1))).status).toBe(200);
    const restored = reconstruct();
    expect((await (await restored.fetch(req("sandbox"))).json()).data).toEqual({ hour: 1, event: "observe" });
    expect((await (await restored.fetch(req("sources"))).json()).data.files).toEqual([JSON.parse(JSON.stringify(file))]);
    database.close();
  });
  it("reads saved sources without writes and preserves data after a rejected save", async () => {
    const { workspace, database, reconstruct, blockWrites } = setup();
    const file = await makeSource("saved.csv", "item,value\nA,1", "upload");
    const saved = await (await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [file] }, 0))).json();
    blockWrites();
    const restored = reconstruct();
    expect(await (await restored.fetch(req("sources"))).json()).toEqual(saved);
    const rejected = await restored.fetch(req("sources", { schema: "hackathon.sources.v1", files: [] }, 1));
    expect(rejected.status).toBe(503);
    expect(await rejected.json()).toMatchObject({ error: { code: "WORKSPACE_WRITE_QUOTA" } });
    expect(await (await restored.fetch(req("sources"))).json()).toEqual(saved);
    database.close();
  });
  it("preserves unchanged rows when editing and shortening a table", async () => {
    const { workspace, database } = setup();
    const save = async (text: string, revision: number) => workspace.fetch(req("sources", {
      schema: "hackathon.sources.v1", files: [await makeSource("saved.csv", text, "upload")],
    }, revision));
    expect((await save("item,value\nA,1\nB,2\nC,3", 0)).status).toBe(200);
    for (const operation of ["DELETE", "UPDATE"])
      database.exec(`CREATE TRIGGER protect_${operation} BEFORE ${operation} ON studio_rows WHEN OLD.position = 0 BEGIN SELECT RAISE(ABORT, 'unchanged row rewritten'); END`);
    expect((await save("item,value\nA,1\nB,4\nC,3", 1)).status).toBe(200);
    expect((await save("item,value\nA,1\nB,4", 2)).status).toBe(200);
    expect(database.prepare("SELECT record_json FROM studio_rows ORDER BY position").all().map(row => JSON.parse(row.record_json as string))).toEqual([{ item: "A", value: "1" }, { item: "B", value: "4" }]);
    expect((await save("item,value\nA,1\nB,4\nD,5", 3)).status).toBe(200);
    expect(database.prepare("SELECT * FROM studio_rows").all()).toHaveLength(3);
    database.close();
  });
  it("clears untouched legacy demo tables once while retaining uploads", async () => {
    const { workspace, database, reconstruct } = setup();
    const upload = await makeSource("custom.csv", "item,value\nKeep,1", "upload");
    await workspace.fetch(req("sources", { ...rawPack, files: [...rawPack.files, upload] }, 0));
    database.prepare("DELETE FROM documents WHERE name = ?").run("empty-studio-v1");
    database.prepare("DELETE FROM chunks WHERE name = ?").run("empty-studio-v1");
    const migrated = reconstruct();
    const doc = await (await migrated.fetch(req("sources"))).json();
    expect(doc.data.files.map((f: { name: string }) => f.name)).toEqual(["custom.csv"]);
    expect(doc.revision).toBe(2);
    expect(database.prepare("SELECT id FROM studio_tables").all()).toHaveLength(1);
    expect(await (await reconstruct().fetch(req("sources"))).json()).toEqual(doc);
    database.close();
  });
  it("clears demo inventory with only a derived label column but preserves edited inventory", async () => {
    const { workspace, database, reconstruct } = setup();
    await workspace.fetch(req("sources"));
    const demo = rawPack.files.find(file => file.kind === "inventory")!;
    const rows = Papa.parse<Record<string, string>>(demo.text, { header: true }).data;
    const text = Papa.unparse(rows.map(row => ({ ...row, "Demand label": "Normal" })));
    const files = [{ ...demo, text, hash: await hashText(text) }];
    database.prepare("DELETE FROM chunks WHERE name = ?").run("sources");
    database.prepare("INSERT INTO chunks VALUES (?, ?, ?)").run("sources", 0, JSON.stringify({ ...rawPack, files }));
    for (const table of ["documents", "chunks"]) database.prepare(`DELETE FROM ${table} WHERE name = ?`).run("empty-studio-v1");
    expect(await (await reconstruct().fetch(req("sources"))).json()).toMatchObject({ data: { files: [] } });
    const edited = await makeSource(demo.name, demo.text, "edited in workspace");
    expect((await workspace.fetch(req("sources", { ...rawPack, files: [edited] }, 2))).status).toBe(200);
    expect(await (await reconstruct().fetch(req("sources"))).json()).toMatchObject({ data: { files: [{ origin: "edited in workspace" }] } });
    database.close();
  });
  it("saves only changed table rows and deletes removed tables with their rows", async () => {
    const { workspace, database, reconstruct } = setup();
    const one = await makeSource("one.csv", "item,value\nA,1", "upload");
    const two = await makeSource("two.csv", "item,value\nB,2", "upload");
    await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [one, two] }, 0));
    database.exec("CREATE TRIGGER protect_unchanged BEFORE DELETE ON studio_rows WHEN OLD.table_id = 'general:one.csv' BEGIN SELECT RAISE(ABORT, 'unchanged table rewritten'); END");
    const changed = await makeSource("two.csv", "item,value\nB,3", "upload");
    expect((await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [one, changed] }, 1))).status).toBe(200);
    expect((await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [one] }, 2))).status).toBe(200);
    expect(database.prepare("SELECT table_id FROM studio_rows").all()).toEqual([{ table_id: "general:one.csv" }]);
    database.exec("DROP TRIGGER protect_unchanged");
    expect((await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [] }, 3))).status).toBe(200);
    expect(await (await reconstruct().fetch(req("sources"))).json()).toMatchObject({ data: { files: [] } });
    expect(database.prepare("SELECT * FROM studio_rows").all()).toEqual([]);
    database.close();
  });
  it("starts empty, stores general and inventory rows, and keeps them across reconstruction", async () => {
    const { workspace, reconstruct, database } = setup();
    expect(await (await workspace.fetch(req("sources"))).json()).toMatchObject({ revision: 1, data: { files: [] } });
    const files = await Promise.all([
      makeSource("notes.csv", "item,value\nA,1", "upload"),
      makeSource("events.json", '[{"event":"Tet"},{"event":"Summer","month":6}]', "upload"),
      makeSource("inventory.csv", "Mã nội bộ,Tên hàng,Tồn thực tế,Demand label\nA,Cup,7,Trendy", "upload"),
    ]);
    const response = await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files }));
    expect(response.status).toBe(200);
    const stored = await response.json();
    expect(stored.data.files[2].text).not.toContain("Demand label");
    expect(database.prepare("SELECT * FROM studio_tables").all()).toHaveLength(3);
    expect(database.prepare("SELECT * FROM studio_rows").all()).toHaveLength(4);
    expect(await (await reconstruct().fetch(req("sources"))).json()).toEqual(stored);
    expect((await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [] }))).status).toBe(409);
    expect(database.prepare("SELECT * FROM studio_rows").all()).toHaveLength(4);
    database.close();
  });
  it("rolls source documents and rows back together if materialization fails", async () => {
    const { workspace, database } = setup();
    await workspace.fetch(req("sources"));
    database.exec("CREATE TRIGGER reject_rows BEFORE INSERT ON studio_rows BEGIN SELECT RAISE(ABORT, 'test failure'); END");
    const file = await makeSource("test.csv", "item,value\nA,1", "upload");
    expect((await workspace.fetch(req("sources", { schema: "hackathon.sources.v1", files: [file] }))).status).toBe(422);
    expect(await (await workspace.fetch(req("sources"))).json()).toMatchObject({ revision: 1, data: { files: [] } });
    expect(database.prepare("SELECT * FROM studio_tables").all()).toHaveLength(0);
    database.close();
  });

  it("persists settings across object reconstruction and sends revision updates", async () => {
    const { workspace, database, sent } = setup();
    const initial = await workspace.fetch(req("settings"));
    expect(await initial.json()).toMatchObject({
      revision: 1,
      data: defaultSettings,
    });
    const saved = await workspace.fetch(
      req("settings", { ...defaultSettings, signals: false }),
    );
    expect(saved.status).toBe(200);
    expect(await saved.json()).toMatchObject({
      revision: 2,
      data: { signals: false },
    });
    expect(JSON.parse(sent[0]!)).toEqual({ name: "settings", revision: 2 });
    expect(
      database
        .prepare("SELECT revision FROM documents WHERE name = ?")
        .get("settings"),
    ).toMatchObject({ revision: 2 });
    database.close();
  });
  it("rejects stale concurrent edits without overwriting the accepted document", async () => {
    const { workspace, database } = setup();
    await workspace.fetch(req("settings"));
    await workspace.fetch(
      req("settings", { ...defaultSettings, signals: false }),
    );
    const conflict = await workspace.fetch(
      req("settings", { ...defaultSettings, quality: "high" }),
    );
    expect(conflict.status).toBe(409);
    expect(await conflict.json()).toHaveProperty(
      "error.code",
      "REVISION_CONFLICT",
    );
    expect(await (await workspace.fetch(req("settings"))).json()).toMatchObject(
      { revision: 2, data: { signals: false, quality: "balanced" } },
    );
    database.close();
  });
  it("keeps chat history and model preference across device reads and rejects stale writes", async () => {
    const { workspace, reconstruct, database, sent } = setup();
    expect(await (await workspace.fetch(req("chats"))).json()).toMatchObject({
      revision: 1,
      data: { preferredModel: "", sessions: [] },
    });
    const archive = {
      preferredModel: "gpt-6.1-sol",
      sessions: [
        {
          id: crypto.randomUUID(),
          title: "Review stock",
          model: "gpt-6.1-sol",
          updatedAt: new Date().toISOString(),
          messages: [
            {
              id: crypto.randomUUID(),
              role: "user",
              content: "Review stock",
            },
          ],
        },
      ],
    };
    expect((await workspace.fetch(req("chats", archive))).status).toBe(200);
    expect(
      await (await reconstruct().fetch(req("chats"))).json(),
    ).toMatchObject({ revision: 2, data: archive });
    expect(
      (
        await workspace.fetch(
          req("chats", { preferredModel: "other", sessions: [] }),
        )
      ).status,
    ).toBe(409);
    expect(await (await workspace.fetch(req("chats"))).json()).toMatchObject({
      data: archive,
    });
    expect(JSON.parse(sent[0]!)).toEqual({ name: "chats", revision: 2 });
    database.close();
  });
  it("rejects invalid and duplicate chat sessions before saving", async () => {
    const { workspace, database } = setup();
    expect(
      (
        await workspace.fetch(
          req("chats", { preferredModel: "x", sessions: [{ id: "bad" }] }, 0),
        )
      ).status,
    ).toBe(422);
    database.close();
  });
  it("saves the assistant reply on the server and refuses stale retries", async () => {
    const { workspace, database } = setup();
    const message = {
      id: crypto.randomUUID(),
      role: "user",
      content: "Review stock",
    };
    const chat = {
      id: crypto.randomUUID(),
      title: "Review stock",
      model: "gpt-6.1-sol",
      updatedAt: new Date().toISOString(),
      messages: [message],
    };
    await workspace.fetch(
      req("chats", { preferredModel: chat.model, sessions: [chat] }, 0),
    );
    const upstream = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ data: [{ id: chat.model, created: 1 }] }),
      )
      .mockResolvedValueOnce(
        Response.json({
          output: [
            {
              type: "message",
              content: [
                { type: "output_text", text: "Check the source timestamps." },
              ],
            },
          ],
        }),
      );
    vi.stubGlobal("fetch", upstream);
    const request = () =>
      new Request("https://workspace.test/api/pm/chat", {
        method: "POST",
        body: JSON.stringify({
          sessionId: chat.id,
          lastMessageId: message.id,
          messages: "ignored client history",
        }),
      });
    const result = await savedChat(
      request(),
      { OPENAI_API_KEY: "test" },
      {},
      workspace,
    );
    expect(result.status).toBe(200);
    const stored = (await (await workspace.fetch(req("chats"))).json()) as {
      data: { sessions: { messages: unknown[] }[] };
    };
    expect(stored.data.sessions[0]!.messages).toEqual([
      message,
      expect.objectContaining({
        role: "assistant",
        content: "Check the source timestamps.",
      }),
    ]);
    expect(
      (await savedChat(request(), { OPENAI_API_KEY: "test" }, {}, workspace))
        .status,
    ).toBe(409);
    expect(upstream).toHaveBeenCalledTimes(2);
    database.close();
  });
  it("keeps the saved user message when OpenAI is unavailable", async () => {
    const { workspace, database } = setup();
    const message = {
      id: crypto.randomUUID(),
      role: "user",
      content: "Review stock",
    };
    const chat = {
      id: crypto.randomUUID(),
      title: "Review stock",
      model: "gpt-6.1-sol",
      updatedAt: new Date().toISOString(),
      messages: [message],
    };
    await workspace.fetch(
      req("chats", { preferredModel: chat.model, sessions: [chat] }, 0),
    );
    const response = await savedChat(
      new Request("https://workspace.test/api/pm/chat", {
        method: "POST",
        body: JSON.stringify({ sessionId: chat.id, lastMessageId: message.id }),
      }),
      {},
      {},
      workspace,
    );
    expect(response.status).toBe(503);
    expect(await (await workspace.fetch(req("chats"))).json()).toMatchObject({
      data: { sessions: [{ messages: [message] }] },
    });
    database.close();
  });
  it("validates clock, document name, revision and settings before writing", async () => {
    const { workspace, database } = setup();
    expect(
      (await workspace.fetch(req("sandbox", { hour: 25, event: "observe" }, 0)))
        .status,
    ).toBe(422);
    expect(
      (await workspace.fetch(req("sandbox", { hour: 1, event: "observe" }, -1)))
        .status,
    ).toBe(422);
    expect((await workspace.fetch(req("other"))).status).toBe(404);
    expect(
      (await workspace.fetch(req("settings", { signals: "yes" }, 0))).status,
    ).toBe(422);
    database.close();
  });
  it("stores and reads sources using chunks below SQLite row limits", async () => {
    const { workspace, database } = setup();
    const text =
      "Mã đơn hàng,SKU phân loại hàng,Số lượng,Ngày đặt hàng\n" +
      Array.from(
        { length: 5000 },
        (_, i) => `O${i},SKU1,1,2026-11-11 01:00`,
      ).join("\n");
    const { default: pack } =
      await import("../../server/repository-sources.json");
    const response = await workspace.fetch(
      req(
        "sources",
        {
          ...pack,
          files: pack.files.map((f, i) => (i === 0 ? { ...f, text } : f)),
        },
        0,
      ),
    );
    expect(response.status).toBe(200);
    expect(
      database
        .prepare("SELECT count(*) AS n FROM chunks WHERE name = ?")
        .get("sources"),
    ).toHaveProperty("n", expect.any(Number));
    const stored = (await (await workspace.fetch(req("sources"))).json()) as {
      data: { files: { text: string }[] };
    };
    expect(stored.data.files[0]!.text).toContain("O4999");
    expect(
      database.prepare("SELECT max(length(content)) AS n FROM chunks").get(),
    ).toHaveProperty("n", 100000);
    database.close();
  });
});
