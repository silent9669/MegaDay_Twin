import { usePanelResize } from "./usePanelResize";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { createPortal } from "react-dom";
import {
  ArrowUp,
  ArrowRight,
  Bell,
  Maximize2,
  Minimize2,
  History,
  Plus,
  Square,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { requestJSON } from "../studio/account";
import { useChatArchive } from "./useChatArchive";
import { ChatHistory } from "./ChatHistory";
import type { ChatMessage, ChatSession } from "../studio/workspace-types";
import { Sheet, SheetContent, SheetTitle } from "../components/ui/sheet";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../components/ui/tabs";
import { MiloAvatar } from "./MiloAvatar";
import { Updates } from "./monitor/Updates";
import type { WorkspaceUpdates } from "./monitor/useWorkspaceMonitor";
import type { ChangeReference } from "./monitor/changes";
const EMPTY_MESSAGES: ChatMessage[] = [];
export function CityChat({
  launcherAnchor,
  updates,
  onOpenChange,
  open: controlledOpen,
}: {
  launcherAnchor?: HTMLElement | null;
  updates?: WorkspaceUpdates;
  onOpenChange?: (open: boolean) => void;
  open?: boolean;
}) {
  const [mobile, setMobile] = useState(
    () => window.matchMedia("(max-width: 899px)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(max-width: 899px)");
    const update = () => setMobile(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const [tab, setTab] = useState("chat");
  const [changeReference, setChangeReference] = useState<
    ChangeReference | undefined
  >();

  const archive = useChatArchive();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [history, setHistory] = useState(false);
  const [optimistic, setOptimistic] = useState<ChatMessage | null>(null);
  const conversation = archive.document?.data.sessions.find(
    (s) => s.id === sessionId,
  );
  const messages = conversation?.messages ?? EMPTY_MESSAGES;
  const visibleMessages =
    optimistic && !messages.some((m) => m.id === optimistic.id)
      ? [...messages, optimistic]
      : messages;
  const panel = usePanelResize(560, 420);
  const { expanded, setExpanded } = panel;
  const [localOpen, setLocalOpen] = useState(false),
    [draft, setDraft] = useState(""),
    [pending, setPending] = useState(false),
    [models, setModels] = useState<string[]>([]),
    [model, setModel] = useState(""),
    [error, setError] = useState("");
  const input = useRef<HTMLTextAreaElement>(null),
    scroll = useRef<HTMLDivElement>(null),
    controller = useRef<AbortController | null>(null),
    trigger = useRef<HTMLButtonElement>(null);
  const open = controlledOpen ?? localOpen;
  const setOpen = useCallback(
    (value: boolean) => {
      setLocalOpen(value);
      onOpenChange?.(value);
    },
    [onOpenChange],
  );
  const markRead = updates?.markRead;
  useEffect(() => {
    if (open && tab === "updates") markRead?.();
  }, [open, tab, markRead]);
  const drafts = useRef(new Map<string, string>());
  const references = useRef(new Map<string, ChangeReference | undefined>());
  useEffect(() => {
    const show = (event: Event) => {
      setOpen(true);
      setTab(event.type === "workspace:updates" ? "updates" : "chat");
      setHistory(false);
    };
    window.addEventListener("workspace:updates", show);
    window.addEventListener("workspace:chat", show);
    const key = (e: KeyboardEvent) => {
      if (
        (e.altKey && e.key.toLowerCase() === "m") ||
        ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k")
      ) {
        e.preventDefault();
        if (!open) { setTab("chat"); setHistory(false); }
        setOpen(!open);
      }
      if (e.key === "Escape" && open) {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("workspace:chat", show);
      window.removeEventListener("workspace:updates", show);
    };
  }, [open, setOpen]);
  useEffect(() => () => controller.current?.abort(), []);
  const preferredModel = archive.document?.data.preferredModel;
  useEffect(() => {
    if (!open) return;
    input.current?.focus();
    let active = true;
    requestJSON<{
      configured: boolean;
      models: string[];
      defaultModel: string;
    }>("/api/pm/models")
      .then((data) => {
        if (!active) return;
        setModels(data.models);
        setModel(
          data.models.includes(preferredModel ?? "")
            ? preferredModel!
            : data.defaultModel,
        );
        setError(
          data.configured ? "" : "OpenAI is not configured for this Worker.",
        );
      })
      .catch((e: Error) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, [open, preferredModel]);
  useEffect(() => {
    scroll.current?.scrollTo({
      top: scroll.current.scrollHeight,
      behavior: "instant",
    });
  }, [messages, pending]);
  async function send(e: FormEvent) {
    e.preventDefault();
    if (!draft.trim() || pending || !model) return;
    const content = draft.trim();
    const message: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content,
    };
    const session: ChatSession = conversation ?? {
      id: crypto.randomUUID(),
      title: content.slice(0, 100),
      model,
      updatedAt: new Date().toISOString(),
      messages: [],
    };
    setPending(true);
    setError("");
    setOptimistic(message);
    controller.current = new AbortController();
    try {
      await archive.append({ ...session, model }, message);
      drafts.current.delete(sessionId ?? "new");
      setSessionId(session.id);
      setDraft("");
      setOptimistic(null);
      await requestJSON("/api/pm/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.current.signal,
        body: JSON.stringify({
          sessionId: session.id,
          lastMessageId: message.id,
          changeReference,
        }),
      });
      setChangeReference(undefined);
      await archive.reload();
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError"))
        setError(e instanceof Error ? e.message : "Milo could not reply.");
      await archive.reload();
    } finally {
      setOptimistic(null);
      setPending(false);
      input.current?.focus();
    }
  }
  async function chooseModel(next: string) {
    setModel(next);
    try {
      await archive.mutate((data) => ({
        ...data,
        preferredModel: next,
        sessions: data.sessions.map((s) =>
          s.id === sessionId ? { ...s, model: next } : s,
        ),
      }));
      setError("");
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Model preference could not be saved.",
      );
    }
  }
  async function retryReply() {
    if (
      !conversation ||
      pending ||
      conversation.messages.at(-1)?.role !== "user"
    )
      return;
    setPending(true);
    setError("");
    controller.current = new AbortController();
    try {
      await requestJSON("/api/pm/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.current.signal,
        body: JSON.stringify({
          sessionId: conversation.id,
          lastMessageId: conversation.messages.at(-1)!.id,
          changeReference,
        }),
      });
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError"))
        setError(e instanceof Error ? e.message : "Milo could not reply.");
    } finally {
      await archive.reload();
      setPending(false);
    }
  }
  function newChat() {
    references.current.set(sessionId ?? "new", changeReference);
    setChangeReference(references.current.get("new"));
    drafts.current.set(sessionId ?? "new", draft);
    setSessionId(null);
    setDraft(drafts.current.get("new") ?? "");
    setHistory(false);
    setError("");
    setTimeout(() => input.current?.focus(), 0);
  }

  const speaking = !open && !!updates?.toast;
  const avatarState = pending ? "thinking" : speaking ? "notification" : "greeting";
  const launcher = (
    <div className="pm-companion" data-speaking={speaking}>
      <button ref={trigger} className="pm-launcher" aria-label="Open PM assistant"
        aria-expanded={open} aria-controls="city-pm-chat" onClick={() => { setTab("chat"); setHistory(false); setOpen(true); }}>
        <span className="pm-avatar" aria-hidden="true"><img src="/assets/milo/avatar.png" alt="" /></span>
        {!!updates?.unread && <span className="pm-unread" aria-label={`${updates.unread} unread updates`}>{updates.unread}</span>}
        <span className="pm-preview" aria-hidden="true">
          <MiloAvatar key={speaking ? updates?.toast?.id : avatarState} state={avatarState} />
        </span>
      </button>
      {speaking && updates?.toast && (
        <aside className="milo-dialogue" aria-label="Workspace update">
          <button className="milo-dialogue-close" aria-label="Dismiss update" onClick={updates.dismissToast}><X size={15} /></button>
          <strong>{updates.toast.title}</strong>
          <p>{updates.toast.detail}</p>
          <button className="milo-review" onClick={() => { setTab("updates"); setOpen(true); updates.dismissToast(); }}>Review update <ArrowRight size={14} /></button>
          <button onClick={updates.readAll}>Read all</button>
        </aside>
      )}
    </div>
  );

  return (
    <div className="city-pm">
      {launcherAnchor ? createPortal(launcher, launcherAnchor) : launcher}
      <Sheet open={open} onOpenChange={setOpen} modal={mobile}>
        <SheetContent
          id="city-pm-chat"
          style={panel.style}
          className={`city-chat city-sheet ${expanded ? "chat-expanded" : ""}`}
          showCloseButton={false}
          overlay={mobile}
          finalFocus={trigger}
          initialFocus={tab === "chat" ? input : undefined}
          aria-labelledby="chat-title"
        >
          <div className="panel-resize-grip" {...panel.grip} />
          <header>
            <div className="chat-identity">
              <MiloAvatar
                state={
                  pending
                    ? "thinking"
                    : error
                      ? "error"
                      : updates?.unread
                        ? "notification"
                        : open
                          ? "greeting"
                          : "idle"
                }
              />
              <SheetTitle id="chat-title">Milo</SheetTitle>
            </div>
            <button
              className="city-icon"
              aria-label={expanded ? "Compact chat" : "Expand chat"}
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? <Minimize2 /> : <Maximize2 />}
            </button>
            <button
              className="city-icon"
              aria-label="New chat"
              title="New chat"
              disabled={pending}
              onClick={newChat}
            >
              <Plus />
            </button>
            <button
              className="city-icon"
              aria-label="Chat history"
              title="Chat history"
              aria-expanded={history}
              onClick={() => setHistory(!history)}
            >
              <History />
            </button>
            <button
              className="city-icon"
              aria-label="Close chat"
              onClick={() => {
                setOpen(false);
                trigger.current?.focus();
              }}
            >
              <X />
            </button>
          </header>
          <Tabs
            className="milo-body"
            value={tab}
            onValueChange={(value) => {
              setTab(String(value));
            }}
          >
            {updates && (
              <TabsList className="milo-tabs" aria-label="Milo views">
                <TabsTrigger value="chat">Chat</TabsTrigger>
                <TabsTrigger value="updates">
                  <Bell size={15} /> Updates{" "}
                  {updates.unread > 0 && <span>{updates.unread}</span>}
                </TabsTrigger>
              </TabsList>
            )}
            <TabsContent value="updates" className="milo-content">
              {updates && (
                <Updates
                  updates={updates}
                  onNavigate={() => setOpen(false)}
                  onExplain={(notice) => {
                    setChangeReference(notice.reference);
                    setDraft(
                      (previous) =>
                        previous.trim() ||
                        `Explain this workspace change: ${notice.title}. ${notice.detail}`,
                    );
                    setTab("chat");
                    setHistory(false);
                    setTimeout(() => input.current?.focus(), 0);
                  }}
                />
              )}
            </TabsContent>
            <TabsContent value="chat" className="milo-content">
              {history ? (
                <ChatHistory
                  sessions={archive.document?.data.sessions ?? []}
                  activeId={sessionId}
                  busy={pending}
                  onBack={() => {
                    setHistory(false);
                    setTimeout(() => input.current?.focus(), 0);
                  }}
                  onSelect={(id) => {
                    drafts.current.set(sessionId ?? "new", draft);
                    references.current.set(sessionId ?? "new", changeReference);
                    setChangeReference(references.current.get(id));
                    setSessionId(id);
                    setDraft(drafts.current.get(id) ?? "");
                    setError("");
                    setHistory(false);
                    const saved = archive.document?.data.sessions.find(
                      (s) => s.id === id,
                    );
                    if (saved && models.includes(saved.model))
                      setModel(saved.model);
                    setTimeout(() => input.current?.focus(), 0);
                  }}
                />
              ) : (
                <>
                  {conversation && (
                    <div className="chat-session-title">
                      {conversation.title}
                    </div>
                  )}
                  <div
                    className="chat-messages"
                    ref={scroll}
                    role="log"
                    aria-label="PM conversation"
                    aria-live="polite"
                  >
                    {!visibleMessages.length ? (
                      <div className="chat-welcome">
                        <h3>What are we working on?</h3>
                        <div>
                          {[
                            "Review source readiness",
                            "Explain the stock checks",
                            "Help plan the next step",
                          ].map((prompt) => (
                            <button
                              key={prompt}
                              onClick={() => {
                                setDraft(prompt);
                                input.current?.focus();
                              }}
                            >
                              {prompt}
                              <ArrowUp size={14} />
                            </button>
                          ))}
                        </div>
                      </div>
                    ) : (
                      visibleMessages.map((message) => (
                        <article
                          className={`chat-message chat-${message.role}`}
                          key={message.id}
                        >
                          <span className="chat-role">
                            {message.role === "user" ? "You" : "Milo"}
                          </span>
                          <div className="chat-markdown">
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              skipHtml
                              components={{
                                a: ({ children, href }) =>
                                  href &&
                                  /^(https?:\/\/|\/)/.test(href) &&
                                  !href.startsWith("//") ? (
                                    <a
                                      href={href}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                    >
                                      {children}
                                    </a>
                                  ) : (
                                    <span>{children}</span>
                                  ),
                                img: ({ alt }) => <span>{alt ?? "Image"}</span>,
                                table: ({ children }) => (
                                  <div className="chat-table">
                                    <table>{children}</table>
                                  </div>
                                ),
                              }}
                            >
                              {message.content}
                            </ReactMarkdown>
                          </div>
                        </article>
                      ))
                    )}
                    {pending && (
                      <div className="chat-thinking" role="status">
                        Reviewing the workspace<span>…</span>
                      </div>
                    )}
                  </div>
                  {(error ||
                    archive.error ||
                    (!pending &&
                      conversation?.messages.at(-1)?.role === "user")) && (
                    <div className="chat-error" role="alert">
                      {error || archive.error || "No reply saved yet."}
                      {!pending &&
                        conversation?.messages.at(-1)?.role === "user" && (
                          <button
                            type="button"
                            onClick={() => void retryReply()}
                          >
                            Retry reply
                          </button>
                        )}
                    </div>
                  )}
                  {changeReference && (
                    <div className="change-attached">
                      Workspace change attached
                      <button
                        type="button"
                        aria-label="Remove change context"
                        onClick={() => setChangeReference(undefined)}
                      >
                        <X size={14} />
                      </button>
                    </div>
                  )}
                  <form className="chat-form" onSubmit={(e) => void send(e)}>
                    <div className="chat-composer">
                      <textarea
                        aria-label="Message Milo"
                        placeholder="Message Milo…"
                        ref={input}
                        value={draft}
                        maxLength={4000}
                        disabled={pending}
                        rows={2}
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (
                            e.key === "Enter" &&
                            !e.shiftKey &&
                            !e.nativeEvent.isComposing
                          ) {
                            e.preventDefault();
                            void send(e);
                          }
                        }}
                      />
                      {pending ? (
                        <button
                          type="button"
                          className="chat-send"
                          aria-label="Stop reply"
                          onClick={() => controller.current?.abort()}
                        >
                          <Square size={15} />
                        </button>
                      ) : (
                        <button
                          type="submit"
                          className="chat-send"
                          aria-label="Send message"
                          disabled={
                            !draft.trim() || !model || !archive.document
                          }
                        >
                          <ArrowUp size={18} />
                        </button>
                      )}
                    </div>
                    <footer>
                      <label>
                        <span className="sr-only">OpenAI model</span>
                        <select
                          aria-label="OpenAI model"
                          value={model}
                          disabled={pending || !models.length}
                          onChange={(e) => void chooseModel(e.target.value)}
                        >
                          {models.length ? (
                            models.map((m) => <option key={m}>{m}</option>)
                          ) : (
                            <option>OpenAI unavailable</option>
                          )}
                        </select>
                      </label>
                      <span className="chat-save-status" role="status">
                        {pending
                          ? "Working…"
                          : error || archive.error
                            ? "Needs attention"
                            : archive.document
                              ? "Saved to account"
                              : "Connecting…"}
                      </span>
                    </footer>
                  </form>
                </>
              )}
            </TabsContent>
          </Tabs>
        </SheetContent>
      </Sheet>
    </div>
  );
}
