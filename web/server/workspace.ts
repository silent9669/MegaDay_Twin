import type {
  DurableObjectState,
  WebSocket as WorkerSocket,
} from "@cloudflare/workers-types";
import {
  defaultSettings,
  defaultSandbox,
  settingsSchema,
  sandboxSchema,
  chatArchiveSchema,
  defaultChatArchive,
} from "../src/studio/workspace-types";
import { validatePack, MAX_BYTES } from "../src/studio/sources";
import { repositorySources } from "./repository";
import { fail } from "./auth";
declare const WebSocketPair: { new (): { 0: WorkerSocket; 1: WorkerSocket } };
export class AccountWorkspace {
  private ctx: DurableObjectState;
  constructor(ctx: DurableObjectState) {
    this.ctx = ctx;
    ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS documents (name TEXT PRIMARY KEY, revision INTEGER NOT NULL, updated_at TEXT NOT NULL)",
    );
    ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS chunks (name TEXT, position INTEGER, content TEXT NOT NULL, PRIMARY KEY(name, position))",
    );
  }
  private read(name: string) {
    const meta = this.ctx.storage.sql
      .exec<{ revision: number; updated_at: string }>(
        "SELECT revision, updated_at FROM documents WHERE name = ?",
        name,
      )
      .toArray()[0];
    if (!meta) return null;
    const chunks = this.ctx.storage.sql
      .exec<{ content: string }>(
        "SELECT content FROM chunks WHERE name = ? ORDER BY position",
        name,
      )
      .toArray();
    return {
      revision: meta.revision,
      updatedAt: meta.updated_at,
      data: JSON.parse(chunks.map((c) => c.content).join("")),
    };
  }
  private write(name: string, data: unknown, expected: number) {
    return this.ctx.storage.transactionSync(() => {
      const old = this.ctx.storage.sql
        .exec<{ revision: number }>(
          "SELECT revision FROM documents WHERE name = ?",
          name,
        )
        .toArray()[0];
      if ((old?.revision ?? 0) !== expected) return null;
      const revision = expected + 1,
        updatedAt = new Date().toISOString(),
        text = JSON.stringify(data);
      this.ctx.storage.sql.exec("DELETE FROM chunks WHERE name = ?", name);
      for (let i = 0; i < text.length; i += 100000)
        this.ctx.storage.sql.exec(
          "INSERT INTO chunks VALUES (?, ?, ?)",
          name,
          i / 100000,
          text.slice(i, i + 100000),
        );
      this.ctx.storage.sql.exec(
        "INSERT INTO documents VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET revision = excluded.revision, updated_at = excluded.updated_at",
        name,
        revision,
        updatedAt,
      );
      return { revision, updatedAt, data };
    });
  }
  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url),
      name = url.pathname.split("/").pop() ?? "";
    if (name === "events") {
      if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket")
        return fail(426, "WebSocket upgrade required.");
      if (this.ctx.getWebSockets().length >= 20)
        return fail(429, "Too many workspace connections.");
      const pair = new WebSocketPair();
      pair[1].serializeAttachment({
        expires: Math.min(
          Number(request.headers.get("X-Session-Expires") ?? 0),
          Date.now() + 12 * 3600000,
        ),
      });
      this.ctx.acceptWebSocket(pair[1]);
      return new Response(null, {
        status: 101,
        webSocket: pair[0],
      } as ResponseInit);
    }
    if (!["sources", "settings", "sandbox", "chats"].includes(name))
      return fail(404, "Workspace document not found.");
    if (request.method === "GET") {
      let document = this.read(name);
      if (!document) {
        const data =
          name === "sources"
            ? (await repositorySources()).pack
            : name === "settings"
              ? defaultSettings
              : name === "chats"
                ? defaultChatArchive
                : defaultSandbox;
        document = this.write(name, data, 0) ?? this.read(name);
      }
      return Response.json(document, {
        headers: { "Cache-Control": "no-store" },
      });
    }
    if (request.method !== "PUT") return fail(405, "Method not allowed.");
    if (Number(request.headers.get("content-length") ?? 0) > MAX_BYTES + 100000)
      return fail(413, "Workspace exceeds 20 MiB.");
    try {
      const raw = await request.text();
      if (new TextEncoder().encode(raw).length > MAX_BYTES + 100000)
        return fail(413, "Workspace exceeds 20 MiB.");
      const body = JSON.parse(raw);
      if (!Number.isSafeInteger(body.revision) || body.revision < 0)
        return fail(422, "A valid revision is required.");
      const data =
        name === "sources"
          ? await validatePack(body.data)
          : name === "settings"
            ? settingsSchema.parse(body.data)
            : name === "chats"
              ? chatArchiveSchema.parse(body.data)
              : sandboxSchema.parse(body.data);
      const saved = this.write(name, data, body.revision);
      if (!saved)
        return fail(
          409,
          "Another device saved a newer version. Your draft has been kept.",
          "REVISION_CONFLICT",
        );
      for (const socket of this.ctx.getWebSockets()) {
        try {
          if (
            (socket.deserializeAttachment() as { expires: number }).expires <=
            Date.now()
          )
            socket.close(1008, "Sign in again");
          else socket.send(JSON.stringify({ name, revision: saved.revision }));
        } catch {
          socket.close(1011, "Reconnect");
        }
      }
      return Response.json(saved, { headers: { "Cache-Control": "no-store" } });
    } catch (error) {
      return fail(
        422,
        error instanceof Error && error.name !== "ZodError"
          ? error.message.slice(0, 1000)
          : "Invalid workspace data.",
      );
    }
  }
  webSocketMessage(socket: WorkerSocket) {
    if (
      (socket.deserializeAttachment() as { expires: number }).expires <=
      Date.now()
    )
      socket.close(1008, "Sign in again");
    else socket.send("pong");
  }
  webSocketClose(socket: WorkerSocket, code: number) {
    socket.close(code, "");
  }
  webSocketError(socket: WorkerSocket) {
    socket.close(1011, "Reconnect");
  }
}
