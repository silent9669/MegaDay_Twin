import { fail } from "./auth";
import { pmChat, type PMEnv } from "./pm";
import type {
  ChatArchive,
  ChatSession,
  Doc,
} from "../src/studio/workspace-types";
import { resolveChange } from "./workspace-change";
import { changeReferenceSchema } from "../src/city/monitor/changes";
type Storage = { fetch(request: Request): Promise<Response> };

// Read the conversation from authenticated account storage, never from client-supplied history.
export async function savedChat(
  request: Request,
  env: PMEnv,
  context: unknown,
  storage: Storage,
) {
  const raw = await request.text();
  if (raw.length > 3000) return fail(413, "Request too large.");
  let body: { sessionId?: string; lastMessageId?: string; changeReference?: unknown };
  try {
    body = JSON.parse(raw);
  } catch {
    return fail(422, "Invalid conversation.");
  }
  if (
    !body ||
    typeof body.sessionId !== "string" ||
    typeof body.lastMessageId !== "string"
  )
    return fail(422, "Choose a saved conversation.");
  let change;
  if (body.changeReference !== undefined) {
    const parsed = changeReferenceSchema.safeParse(body.changeReference);
    if (!parsed.success) return fail(422, "Invalid workspace change.");
    change = await resolveChange(parsed.data, storage, request.url);
  }
  const url = new URL("/api/workspace/chats", request.url);
  async function load() {
    const response = await storage.fetch(new Request(url));
    if (!response.ok) throw Error("Chat storage unavailable.");
    return response.json() as Promise<Doc<ChatArchive>>;
  }
  const document = await load();
  const conversation = document.data.sessions.find(
    (s) => s.id === body.sessionId,
  );
  if (!conversation) return fail(404, "Conversation not found.");
  const current = (s: ChatSession) =>
    s.messages.at(-1)?.id === body.lastMessageId &&
    s.messages.at(-1)?.role === "user";
  if (!current(conversation))
    return fail(
      409,
      "This conversation changed on another device. Reopen it before replying.",
    );
  const response = await pmChat(
    new Request(request.url, {
      method: "POST",
      signal: request.signal,
      body: JSON.stringify({
        model: conversation.model,
        messages: conversation.messages
          .slice(-16)
          .map(({ role, content }) => ({ role, content })),
      }),
    }),
    env,
    change ? { workspace: context, change } : context,
  );
  if (!response.ok) return response;
  const result = (await response.json()) as { reply: string; model: string };
  const reply = {
    id: crypto.randomUUID(),
    role: "assistant" as const,
    content: result.reply,
  };
  for (let attempt = 0; attempt < 3; attempt++) {
    const latest = await load();
    const session = latest.data.sessions.find((s) => s.id === conversation.id);
    if (!session || !current(session))
      return fail(
        409,
        "This conversation changed on another device. Your saved messages are retained.",
      );
    const updated = {
      ...session,
      updatedAt: new Date().toISOString(),
      messages: [...session.messages, reply],
    };
    const saved = await storage.fetch(
      new Request(url, {
        method: "PUT",
        body: JSON.stringify({
          revision: latest.revision,
          data: {
            ...latest.data,
            sessions: latest.data.sessions.map((s) =>
              s.id === updated.id ? updated : s,
            ),
          },
        }),
      }),
    );
    if (saved.ok)
      return Response.json({ ...result, document: await saved.json() });
    if (saved.status !== 409)
      return fail(
        503,
        "Reply could not be saved. Your messages are retained; try again.",
      );
  }
  return fail(
    409,
    "Another device is saving. Reopen this conversation and try again.",
  );
}
