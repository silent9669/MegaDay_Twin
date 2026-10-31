import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CityChat } from "./CityChat";
import type { ChatArchive, Doc } from "../studio/workspace-types";
import { openMilo, openMiloForSku, type MiloReplyPlan, type MiloCampaignPlan } from "./milo-contract";

let document: Doc<ChatArchive>;
let failSave = false;
let replyPlan: MiloReplyPlan | null = null;
let replyCampaignPlan: MiloCampaignPlan | null = null;
let waitForReply: (() => Promise<void>) | undefined;
beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn(query => ({matches:false, media:query, addEventListener:vi.fn(),removeEventListener:vi.fn()})));
  document = {
    revision: 1,
    updatedAt: new Date().toISOString(),
    data: { preferredModel: "gpt-4.1", sessions: [] },
  };
  failSave = false;
  replyPlan = null;
  replyCampaignPlan = null;
  waitForReply = undefined;
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
        await waitForReply?.();
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
        return Response.json({ reply: "Review the **source timestamps**.", plan: replyPlan, campaignPlan: replyCampaignPlan });
      }
      if (path === "/api/sale-planner/context") return Response.json({ backendConfigured: true, canCreateGoals: true, canPreview: true, canApply: true, referenceTime: '2026-11-11T00:00:00Z', dataAsOf: '2026-11-11T00:00:00Z', notice: null, campaigns: [{ id: 'C1', name: 'Sale test' }] });
      if (String(path).includes('/api/sale-planner/active?')) return Response.json({ campaignId: 'C1', activeGoal: null, baseGoal: null });
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
  it("reviews a campaign-only Milo draft without inventing a SKU and invalidates it on edits", async () => {
    replyCampaignPlan = { campaignId: "C1", name: "Sale test", interpretation: { method: "ai", notices: [], draft: {
      text: "Tối ưu lợi nhuận, ngân sách 2 triệu", kind: "profit", targetUnits: null, maxSpendVnd: 2000000,
      maxLossVnd: 0, start: "2026-11-11T01:00:00Z", deadline: "2026-11-11T05:00:00Z", sellTargetProbability: null, minMarginalReturn: null, unresolved: [],
    } } };
    render(<CityChat />); await open();
    act(() => openMilo({ screen: "sale-planner", campaignId: "C1" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Message Milo" }), { target: { value: replyCampaignPlan.interpretation.draft.text } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    fireEvent.click(await screen.findByRole("button", { name: "Review goal" }));
    await waitFor(() => expect(screen.getByLabelText("Campaign")).toHaveValue("C1"));
    expect(document.data.sessions[0]!.context).toEqual({ screen: "sale-planner", campaignId: "C1" });
    expect(screen.getByRole("button", { name: "Preview simulation" })).toBeDisabled();
    expect(screen.queryByText(/Goal for SKU/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "Message Milo" }), { target: { value: "Đổi ngân sách" } });
    expect(screen.queryByRole("button", { name: "Preview simulation" })).not.toBeInTheDocument();
  });
  it("keeps SKU identifiers with the saved conversation and reviews before any simulation", async () => {
    replyPlan = { context: { sku: { sku: 'SKU-A', name: 'Product A', variant: '', stockAvailable: 200, soldTodayUnits: 20, priceEffectiveVnd: 50000, contributionPerUnitVnd: 10000, demandGroup: 'normal' }, campaigns: [{ id: 'C1', name: 'Sale test', skus: ['SKU-A'] }], dataAsOf: '2026-11-11T00:00:00Z', source: null, canPlan: true, notices: [], opening: 'Your goal?' }, interpretation: { method: 'ai', notices: [], draft: { text: 'Sell 100 units', kind: 'sell_target', targetUnits: 100, maxSpendVnd: 2000000, maxLossVnd: null, start: '2026-11-11T01:00:00Z', deadline: '2026-11-11T05:00:00Z', sellTargetProbability: null, minMarginalReturn: null, unresolved: [] } } };
    render(<CityChat />); await open();
    act(() => openMiloForSku('SKU-A'));
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Milo' }), { target: { value: 'Sell 100 units' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Review goal' }));
    expect(document.data.sessions[0]!.context).toEqual({ screen: 'hq', sku: 'SKU-A' });
    const call = vi.mocked(fetch).mock.calls.find(([path]) => path === '/api/pm/chat')!;
    expect(JSON.parse(String(call[1]?.body)).context).toEqual({ screen: 'hq', sku: 'SKU-A' });
    await waitFor(() => expect(screen.getByLabelText('Campaign')).toHaveValue('C1'));
    expect(screen.getByLabelText('Target units')).toHaveValue(100);
    expect(screen.getByRole('button', { name: 'Preview simulation' })).toBeDisabled();
    expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).endsWith('/run'))).toBe(false);
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Milo' }), { target: { value: 'Actually 150' } });
    expect(screen.queryByRole('button', { name: 'Preview simulation' })).not.toBeInTheDocument();
    let finish!: () => void;
    waitForReply = () => new Promise((resolve) => { finish = resolve; });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([path]) => path === '/api/pm/chat')).toHaveLength(2));
    act(() => openMiloForSku('SKU-B'));
    act(() => openMiloForSku('SKU-A'));
    expect(screen.queryByRole('button', { name: 'Review goal' })).not.toBeInTheDocument();
    await act(async () => { finish(); });
    expect(screen.queryByRole('button', { name: 'Review goal' })).not.toBeInTheDocument();
  });
  it('opens a separate result context even when the SKU matches an existing goal chat', async () => {
    render(<CityChat />); await open();
    act(() => openMiloForSku('SKU-A'));
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Milo' }), { target: { value: 'Plan this sale' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await screen.findByText('source timestamps');
    act(() => openMilo({ screen: 'sale-planner', sku: 'SKU-A', campaignId: 'C1', scenarioId: 'scn-1' }));
    expect(screen.queryByText('source timestamps')).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Milo' }), { target: { value: 'Explain the calculation' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await screen.findByText('source timestamps');
    expect(document.data.sessions).toHaveLength(2);
    expect(document.data.sessions[0]!.context).toMatchObject({ sku: 'SKU-A', campaignId: 'C1', scenarioId: 'scn-1' });
  });
  it("ignores late replies and drafts after switching to another SKU", async () => {
    let release!: () => void;
    waitForReply = () => new Promise((resolve) => { release = resolve; });
    render(<CityChat />); await open();
    act(() => openMiloForSku('SKU-A'));
    fireEvent.change(screen.getByRole('textbox', { name: 'Message Milo' }), { target: { value: 'Sell 100' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([path]) => path === '/api/pm/chat')).toBe(true));
    const pending = vi.mocked(fetch).mock.calls.find(([path]) => path === '/api/pm/chat')![1]!.signal!;
    act(() => openMiloForSku('SKU-B'));
    expect(pending.aborted).toBe(true);
    await act(async () => { release(); });
    expect(screen.getByText('SKU-B', { exact: true })).toBeInTheDocument();
    expect(screen.queryByText('source timestamps')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Review goal' })).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Message Milo' })).toHaveValue('');
  });
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
  const updates={notices:[notice],toast:notice,unread:1,dismissToast:vi.fn(),markRead:vi.fn(),readAll:vi.fn(),dismiss:vi.fn()};
  render(<MemoryRouter><CityChat updates={updates}/></MemoryRouter>);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.some(([path])=>path==='/api/pm/chat')).toBe(false);
  expect(screen.getByLabelText('Workspace update')).toHaveTextContent('Stock reached zero');
  fireEvent.click(screen.getByRole('button', { name: 'Read all' }));
  expect(updates.readAll).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Review update' }));
  expect(updates.dismissToast).toHaveBeenCalledOnce();
  await screen.findByRole('tab', { name: /Updates/ });
  expect(updates.markRead).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Read all' }));
  expect(updates.readAll).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole('button',{name:'Explain'}));
  expect(screen.getByRole('textbox',{name:'Message Milo'})).toHaveValue('Explain this workspace change: Stock reached zero. SKU1: 10 → 0');
  expect(vi.mocked(fetch).mock.calls.some(([path])=>path==='/api/pm/chat')).toBe(false);
  fireEvent.click(screen.getByRole('button',{name:'Send message'}));
  await screen.findByText('source timestamps');
  const request=vi.mocked(fetch).mock.calls.find(([path])=>path==='/api/pm/chat')!;
  expect(JSON.parse(String(request[1]?.body)).changeReference).toEqual(reference);
});

it('opens Chat directly from the avatar and sidebar after viewing Updates', async () => {
  const { MemoryRouter } = await import('react-router');
  const updates = { notices: [], toast: null, unread: 0, dismissToast: vi.fn(), markRead: vi.fn(), readAll: vi.fn(), dismiss: vi.fn() };
  render(<MemoryRouter><CityChat updates={updates} /></MemoryRouter>);
  await open();
  fireEvent.click(screen.getByRole('tab', { name: /Updates/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Close chat' }));
  fireEvent.click(screen.getByRole('button', { name: 'Open PM assistant' }));
  expect(screen.getByRole('tab', { name: 'Chat' })).toHaveAttribute('aria-selected', 'true');
  fireEvent.click(screen.getByRole('tab', { name: /Updates/ }));
  fireEvent(window, new Event('workspace:chat'));
  expect(screen.getByRole('textbox', { name: 'Message Milo' })).toBeVisible();
});

it('opens contextual analysis without sending and persists the evidence with the user message',async()=>{
 render(<CityChat/>);await open();
 fireEvent.change(screen.getByRole('textbox',{name:'Message Milo'}),{target:{value:'My unsent question'}});
 const analysis={title:'Comparison',source:'user-assumptions',data:'{"stock":83}'};
 fireEvent(window,new CustomEvent('workspace:analyze',{detail:{context:analysis,prompt:'Explain this comparison'}}));
 expect(screen.getByRole('textbox',{name:'Message Milo'})).toHaveValue('My unsent question');
 expect(screen.getByRole('button',{name:'Remove analysis context'})).toBeVisible();
 expect(vi.mocked(fetch).mock.calls.some(([path])=>path==='/api/pm/chat')).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Send message'}));await screen.findByText('source timestamps');
 expect(document.data.sessions[0]?.messages[0]?.analysis).toEqual(analysis);
});
