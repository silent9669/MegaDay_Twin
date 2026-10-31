import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CityChat } from "./CityChat";
import type { ChatArchive, Doc } from "../studio/workspace-types";

let document: Doc<ChatArchive>;
let failSave = false;
beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn(query => ({matches:false, media:query, addEventListener:vi.fn(),removeEventListener:vi.fn()})));
  document = {
    revision: 1,
    updatedAt: new Date().toISOString(),
    data: { preferredModel: "gpt-4.1", sessions: [] },
  };
  failSave = false;
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(),
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/api/pm/models")
        return Response.json({
          configured: true,
          models: ["gpt-4.1", "o3", "gpt-6.1-sol"],
          defaultModel: "gpt-6.1-sol",
        });
      if (path === "/api/workspace/chats") {
        if (init?.method === "PUT") {
          if (failSave)
            return Response.json(
              { error: { message: "Storage unavailable" } },
              { status: 503 },
            );
          const body = JSON.parse(String(init.body));
          document = {
            ...document,
            revision: document.revision + 1,
            data: body.data,
          };
        }
        return Response.json(document);
      }
      if (path === "/api/pm/chat") {
        const body = JSON.parse(String(init?.body));
        const session = document.data.sessions.find(
          (s) => s.id === body.sessionId,
        )!;
        session.messages.push({
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Review the **source timestamps**.",
        });
        document.revision++;
        return Response.json({ reply: "Review the **source timestamps**." });
      }
      throw Error(`Unexpected path ${path}`);
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
async function open() {
  fireEvent.click(screen.getByRole("button", { name: "Open PM assistant" }));
  await screen.findByRole("option", { name: "gpt-6.1-sol" });
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "OpenAI model" })).toHaveValue(
      "gpt-4.1",
    ),
  );
}
describe("account chat", () => {
  it("replaces delete with history and persists a model preference", async () => {
    render(<CityChat />);
    await open();
    expect(
      screen.queryByRole("button", { name: /clear|delete|trash/i }),
    ).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "o3" } });
    await waitFor(() => expect(document.data.preferredModel).toBe("o3"));
    fireEvent.click(screen.getByRole("button", { name: "Chat history" }));
    expect(
      screen.getByText("Your conversations will appear here."),
    ).toBeInTheDocument();
  });
  it("reopens a saved session after remounting on another device", async () => {
    const first = render(<CityChat />);
    await open();
    fireEvent.change(screen.getByRole("textbox", { name: "Message Milo" }), {
      target: { value: "Review stock" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await screen.findByText("source timestamps");
    expect(document.data.sessions[0]!.messages).toHaveLength(2);
    first.unmount();
    render(<CityChat />);
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Chat history" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /Review stock/ }),
    );
    expect(await screen.findByText("source timestamps")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message Milo" })).toHaveValue(
      "",
    );
  });
  it("retains a draft when account storage cannot save", async () => {
    render(<CityChat />);
    await open();
    failSave = true;
    fireEvent.change(screen.getByRole("textbox", { name: "Message Milo" }), {
      target: { value: "Keep this draft" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Storage unavailable",
    );
    expect(screen.getByRole("textbox", { name: "Message Milo" })).toHaveValue(
      "Keep this draft",
    );
    expect(document.data.sessions).toHaveLength(0);
    expect(
      vi.mocked(fetch).mock.calls.some(([path]) => path === "/api/pm/chat"),
    ).toBe(false);
  });
  it("keeps unsent drafts while switching between history and a new chat", async () => {
    document.data.sessions.push({
      id: crypto.randomUUID(),
      title: "Earlier chat",
      model: "gpt-4.1",
      updatedAt: new Date().toISOString(),
      messages: [],
    });
    render(<CityChat />);
    await open();
    fireEvent.change(screen.getByRole("textbox", { name: "Message Milo" }), {
      target: { value: "Unsent draft" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Chat history" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /Earlier chat/ }),
    );
    expect(screen.getByRole("textbox", { name: "Message Milo" })).toHaveValue(
      "",
    );
    fireEvent.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.getByRole("textbox", { name: "Message Milo" })).toHaveValue(
      "Unsent draft",
    );
  });
});

it('keeps updates quiet and attaches bounded evidence only when an explanation is sent', async () => {
  const { MemoryRouter } = await import('react-router');
  const reference = {kind:'stock-zero' as const,sourceRevision:2,sandboxRevision:1,hour:1,source:'inventory' as const,entity:'SKU1',before:10,after:0};
  const notice = {id:'2:1:stock-zero',kind:reference.kind,title:'Stock reached zero',detail:'SKU1: 10 → 0',before:10,after:0,important:true,simulation:false,reconnected:false,observedAt:new Date().toISOString(),href:'/city-demo?district=warehouse&stock=SKU1',reference};
  const updates={notices:[notice],toast:notice,unread:1,dismissToast:vi.fn(),markRead:vi.fn(),dismiss:vi.fn()};
  render(<MemoryRouter><CityChat updates={updates}/></MemoryRouter>);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.some(([path])=>path==='/api/pm/chat')).toBe(false);
  await open();
  fireEvent.click(screen.getByRole('tab',{name:/Updates/}));
  expect(updates.markRead).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button',{name:'Explain'}));
  expect(screen.getByRole('textbox',{name:'Message Milo'})).toHaveValue('Explain this workspace change: Stock reached zero. SKU1: 10 → 0');
  expect(vi.mocked(fetch).mock.calls.some(([path])=>path==='/api/pm/chat')).toBe(false);
  fireEvent.click(screen.getByRole('button',{name:'Send message'}));
  await screen.findByText('source timestamps');
  const request=vi.mocked(fetch).mock.calls.find(([path])=>path==='/api/pm/chat')!;
  expect(JSON.parse(String(request[1]?.body)).changeReference).toEqual(reference);
});
