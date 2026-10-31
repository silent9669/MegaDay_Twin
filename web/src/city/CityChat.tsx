import { analysisContextSchema, type AnalysisContext } from "./analysis-context";
import { prepareChatImage, type ImageAttachment } from "./chat-evidence";
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
  ImagePlus,
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
import { miloContextSchema, type MiloContext, type MiloReply, type MiloReplyPlan, type MiloCampaignPlan } from "./milo-contract";
import { SaleGoalPlanner } from "../planner/SaleGoalPlanner";
const draftScope=(context?:MiloContext)=>"new:"+JSON.stringify([context?.sku??null,context?.campaignId??null,context?.scenarioId??null]);
const EMPTY_MESSAGES: ChatMessage[] = [];
type SavedPlan = { plan?: MiloReplyPlan; campaignPlan?: MiloCampaignPlan; userMessageId: string };
export function CityChat({
  workspaceOpen = false,
  onWidthChange,
  launcherAnchor,
  updates,
  onOpenChange,
  open: controlledOpen,
  context: screenContext,
}: {
  workspaceOpen?: boolean;
  onWidthChange?: (width:number)=>void;
  launcherAnchor?: HTMLElement | null;
  updates?: WorkspaceUpdates;
  onOpenChange?: (open: boolean) => void;
  open?: boolean;
  context?: MiloContext;
}) {
  const [mobile, setMobile] = useState(
    () => window.matchMedia("(max-width: 1023px)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1023px)");
    const update = () => setMobile(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const [tab, setTab] = useState("chat");
  const [changeReference, setChangeReference] = useState<
    ChangeReference | undefined
  >();

  const [analysis,setAnalysis]=useState<AnalysisContext|undefined>();
  const [attachments,setAttachments]=useState<ImageAttachment[]>([]);
  const [preparingImage,setPreparingImage]=useState(false);
  const evidence=useRef(new Map<string,{analysis?:AnalysisContext;attachments:ImageAttachment[]}>());
  const imageInput=useRef<HTMLInputElement>(null);
  const archive = useChatArchive();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [selectedContext, setSelectedContext] = useState<MiloContext | undefined>();
  const [savedPlan, setPlan] = useState<SavedPlan | null>(null);
  const [review, setReview] = useState(false);
  const operation = useRef(0);
  const [history, setHistory] = useState(false);
  const [optimistic, setOptimistic] = useState<ChatMessage | null>(null);
  const conversation = archive.document?.data.sessions.find(
    (s) => s.id === sessionId,
  );
  const messages = conversation?.messages ?? EMPTY_MESSAGES;
  const currentPlan = savedPlan && messages.at(-1)?.role === "assistant" && messages.at(-2)?.id === savedPlan.userMessageId ? savedPlan : null;
  const plan = currentPlan?.plan, campaignPlan = currentPlan?.campaignPlan;
  const context = conversation?.context ?? selectedContext ?? screenContext;
  const draftKey=sessionId??draftScope(context);
  const visibleMessages =
    optimistic && !messages.some((m) => m.id === optimistic.id)
      ? [...messages, optimistic]
      : messages;
  const panel = usePanelResize(400, 300, "left", workspaceOpen ? 572 : 0);
  useEffect(()=>{onWidthChange?.(panel.width);},[panel.width,onWidthChange]);
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
  const plans = useRef(new Map<string, SavedPlan>());
  useEffect(() => {
    const selectSku = (event: Event) => {
      const result = miloContextSchema.safeParse((event as CustomEvent).detail);
      if (!result.success || (!result.data.sku && !result.data.scenarioId && !result.data.campaignId)) return;
      drafts.current.set(draftKey,draft);
      evidence.current.set(draftKey,{analysis,attachments});
      operation.current++;
      controller.current?.abort();
      setPending(false);
      setOptimistic(null);
      setSelectedContext(result.data);
      const saved = archive.document?.data.sessions.find((session) => session.context?.sku === result.data.sku && session.context?.campaignId === result.data.campaignId && session.context?.scenarioId === result.data.scenarioId);
      const savedEvidence=evidence.current.get(saved?.id??draftScope(result.data));
      setAnalysis(savedEvidence?.analysis);setAttachments(savedEvidence?.attachments??[]);
      setSessionId(saved?.id ?? null);
      setPlan(saved ? plans.current.get(saved.id) ?? null : null);
      setReview(false);
      setChangeReference(undefined);
      setDraft(drafts.current.get(saved?.id??draftScope(result.data))??(result.data.scenarioId ? "Explain the numbers and evidence for this scenario." : ""));
      setError("");
      setHistory(false);
      setTab("chat");
      setOpen(true);
    };
    window.addEventListener("workspace:milo-sku", selectSku);
    return () => window.removeEventListener("workspace:milo-sku", selectSku);
  }, [archive.document, setOpen,draftKey,draft,analysis,attachments]);
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
  useEffect(()=>{
    const analyze=(event:Event)=>{
      const detail=(event as CustomEvent).detail;
      const parsed=analysisContextSchema.safeParse(detail?.context);
      if(!parsed.success||typeof detail?.prompt!=='string')return;
      if(parsed.data.sku){const selection=miloContextSchema.safeParse({sku:parsed.data.sku,screen:'hq'});if(selection.success)window.dispatchEvent(new CustomEvent('workspace:milo-sku',{detail:selection.data}));}
      setAnalysis(parsed.data);setDraft(previous=>previous.trim()||detail.prompt.slice(0,4000));
      setOpen(true);setTab('chat');setHistory(false);
      setTimeout(()=>input.current?.focus(),0);
    };
    window.addEventListener('workspace:analyze',analyze);
    return()=>window.removeEventListener('workspace:analyze',analyze);
  },[setOpen]);
  async function attachImages(files:FileList|null) {
    if(!files?.length)return;
    if(files.length+attachments.length>2){setError('Attach up to two images per message.');return;}
    setPreparingImage(true);setError('');
    try{const images=await Promise.all(Array.from(files).map(prepareChatImage));setAttachments(previous=>[...previous,...images]);}
    catch(e){setError(e instanceof Error?e.message:'Could not prepare image.');}
    finally{setPreparingImage(false);if(imageInput.current)imageInput.current.value='';}
  }
  function rememberEvidence(){evidence.current.set(draftKey,{analysis,attachments});}
  function restoreEvidence(id:string){const saved=evidence.current.get(id);setAnalysis(saved?.analysis);setAttachments(saved?.attachments??[]);}
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
    if ((!draft.trim()&&!attachments.length) || pending || preparingImage || !model) return;
    const content = draft.trim() || "Analyze the attached image in the workspace context.";
    const message: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content,
      ...(analysis?{analysis}:{}),
      ...(attachments.length?{attachments}:{}),
    };
    const session: ChatSession = conversation ?? {
      id: crypto.randomUUID(),
      title: content.slice(0, 100),
      model,
      updatedAt: new Date().toISOString(),
      messages: [],
      context,
    };
    const sequence = ++operation.current;
    plans.current.delete(session.id);
    setPlan(null);
    setReview(false);
    setPending(true);
    setError("");
    setOptimistic(message);
    controller.current = new AbortController();
    try {
      await archive.append({ ...session, model }, message);
      if (sequence !== operation.current) return;
      drafts.current.delete(draftKey);
      evidence.current.delete(draftKey);
      setAnalysis(undefined);setAttachments([]);
      setSessionId(session.id);
      setDraft("");
      setOptimistic(null);
      const result = await requestJSON<MiloReply>("/api/pm/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.current.signal,
        body: JSON.stringify({
          sessionId: session.id,
          lastMessageId: message.id,
          changeReference,
          context: { ...session.context, screen: screenContext?.screen ?? session.context?.screen },
        }),
      });
      if (sequence !== operation.current) return;
      if (result.plan && result.plan.context.sku.sku === session.context?.sku) {
        const received = { plan: result.plan, userMessageId: message.id };
        plans.current.set(session.id, received);
        setPlan(received);
      } else if (!session.context?.sku && result.campaignPlan?.campaignId === session.context?.campaignId && result.campaignPlan) {
        const received = { campaignPlan: result.campaignPlan, userMessageId: message.id };
        plans.current.set(session.id, received); setPlan(received);
      }
      setChangeReference(undefined);
      await archive.reload();
    } catch (e) {
      if (sequence !== operation.current) return;
      if (!(e instanceof DOMException && e.name === "AbortError"))
        setError(e instanceof Error ? e.message : "Milo could not reply.");
      await archive.reload();
    } finally {
      if (sequence === operation.current) {
        setOptimistic(null);
        setPending(false);
        input.current?.focus();
      }
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
    const sequence = ++operation.current;
    plans.current.delete(conversation.id);
    setReview(false);
    setPlan(null);
    controller.current = new AbortController();
    try {
      const result = await requestJSON<MiloReply>("/api/pm/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.current.signal,
        body: JSON.stringify({
          sessionId: conversation.id,
          lastMessageId: conversation.messages.at(-1)!.id,
          changeReference,
          context: { ...conversation.context, screen: screenContext?.screen ?? conversation.context?.screen },
        }),
      });
      if (sequence !== operation.current) return;
      if (result.plan && result.plan.context.sku.sku === conversation.context?.sku) {
        const received = { plan: result.plan, userMessageId: conversation.messages.at(-1)!.id };
        plans.current.set(conversation.id, received);
        setPlan(received);
      } else if (!conversation.context?.sku && result.campaignPlan?.campaignId === conversation.context?.campaignId && result.campaignPlan) {
        const received = { campaignPlan: result.campaignPlan, userMessageId: conversation.messages.at(-1)!.id };
        plans.current.set(conversation.id, received); setPlan(received);
      }
    } catch (e) {
      if (sequence !== operation.current) return;
      if (!(e instanceof DOMException && e.name === "AbortError"))
        setError(e instanceof Error ? e.message : "Milo could not reply.");
    } finally {
      await archive.reload();
      if (sequence === operation.current) setPending(false);
    }
  }
  function newChat() {
    rememberEvidence();restoreEvidence(draftScope(context));
    operation.current++;
    controller.current?.abort();
    setPending(false);
    setOptimistic(null);
    setSelectedContext(context);
    setPlan(null);
    setReview(false);
    references.current.set(draftKey, changeReference);
    setChangeReference(references.current.get(draftScope(context)));
    drafts.current.set(draftKey, draft);
    setSessionId(null);
    setDraft(drafts.current.get(draftScope(context)) ?? "");
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
    <div className={`city-pm ${launcherAnchor ? "" : "pm-global-launcher"}`}>
      {launcherAnchor ? createPortal(launcher, launcherAnchor) : launcher}
      <Sheet open={open} onOpenChange={setOpen} modal={mobile} disablePointerDismissal={!mobile}>
        <SheetContent
          id="city-pm-chat"
          side="left"
          style={panel.style}
          className={`city-chat city-sheet ${expanded ? "chat-expanded" : ""}`}
          showCloseButton={false}
          overlay={mobile}
          finalFocus={trigger}
          initialFocus={tab === "chat" ? input : undefined}
          aria-labelledby="chat-title"
        >
          <div className="panel-resize-grip panel-resize-right" {...panel.grip} />
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
              disabled={pending||preparingImage}
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
                  busy={pending||preparingImage}
                  onBack={() => {
                    setHistory(false);
                    setTimeout(() => input.current?.focus(), 0);
                  }}
                  onSelect={(id) => {
                    rememberEvidence();restoreEvidence(id);
                    operation.current++;
                    controller.current?.abort();
                    setPending(false);
                    setOptimistic(null);
                    setReview(false);
                    setPlan(plans.current.get(id) ?? null);
                    drafts.current.set(draftKey, draft);
                    references.current.set(draftKey, changeReference);
                    setChangeReference(references.current.get(id));
                    setSessionId(id);
                    setDraft(drafts.current.get(id) ?? "");
                    setError("");
                    setHistory(false);
                    const saved = archive.document?.data.sessions.find(
                      (s) => s.id === id,
                    );
                    setSelectedContext(saved?.context);
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
                  {(context?.sku || context?.scenarioId || context?.campaignId) && (
                    <div className="milo-context" role="status">
                      <span>Discussing <strong>{context.sku ?? context.campaignId}</strong>{context.scenarioId && <small> · Scenario {context.scenarioId}</small>}</span>
                      <button type="button" disabled={pending} onClick={() => {
                        newChat(); setSelectedContext(screenContext?.sku ? { screen: screenContext.screen } : screenContext);
                      }}>Clear {context.scenarioId ? "scenario" : context.sku ? "SKU" : "campaign"}</button>
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
                        <h3>{context?.sku ? `What would you like to achieve with ${context.sku}?` : "What are we working on?"}</h3>
                        <div>
                          {(context?.sku ? ["Help increase sales for this product", "Help clear remaining inventory", "Review goals supported by the current data"] : [
                            "Is the data ready for a scenario?",
                            "Explain current inventory",
                            "Help plan a sale goal",
                          ]).map((prompt) => (
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
                          {message.analysis&&<details className="chat-context"><summary>{message.analysis.title} · {message.analysis.source}</summary><pre>{message.analysis.data}</pre></details>}
                          {!!message.attachments?.length&&<div className="chat-images">{message.attachments.map((image,i)=><a key={i} href={image.dataUrl} download={image.name}><img src={image.dataUrl} alt={image.name}/></a>)}</div>}
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
                    {(plan || campaignPlan) && !pending && !draft.trim() && (
                      <section className="milo-goal-handoff" aria-label="Goal draft">
                        <p>Review and edit the goal before previewing a simulation.</p>
                        <button type="button" onClick={() => { setReview((value) => !value); setExpanded(true); }}>
                          {review ? "Close goal draft" : "Review goal"}
                        </button>
                        {review && plan && <SaleGoalPlanner key={`${sessionId}-${messages.at(-1)?.id}`} embedded
                          initialInterpretation={plan.interpretation} focusSku={plan.context.sku.sku}
                          campaignIds={plan.context.campaigns.map((campaign) => campaign.id)} allowRun={plan.context.canPlan} />}
                        {review && campaignPlan && <SaleGoalPlanner key={`${sessionId}-${messages.at(-1)?.id}`} embedded
                          initialInterpretation={campaignPlan.interpretation} campaignIds={[campaignPlan.campaignId]} />}
                      </section>
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
                  {analysis&&<div className="change-attached"><span>{analysis.title} · {analysis.source}</span><button type="button" aria-label="Remove analysis context" onClick={()=>setAnalysis(undefined)}><X size={14}/></button></div>}
                  {!!attachments.length&&<div className="chat-images chat-attachments">{attachments.map((image,i)=><figure key={i}><img src={image.dataUrl} alt={image.name}/><button aria-label={`Remove ${image.name}`} disabled={pending} onClick={()=>setAttachments(previous=>previous.filter((_,index)=>index!==i))}><X size={14}/></button></figure>)}</div>}
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
                        onChange={(e) => { setDraft(e.target.value); setReview(false); }}
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
                            (!draft.trim()&&!attachments.length) || !model || !archive.document || preparingImage
                          }
                        >
                          <ArrowUp size={18} />
                        </button>
                      )}
                    </div>
                    <footer>
                      <input ref={imageInput} type="file" accept="image/png,image/jpeg,image/webp" multiple hidden onChange={e=>void attachImages(e.target.files)}/>
                      <button type="button" className="city-icon" aria-label="Attach images" title="Attach images" disabled={pending||preparingImage||attachments.length>=2} onClick={()=>imageInput.current?.click()}><ImagePlus size={18}/></button>
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
                        {preparingImage?"Preparing image…":pending
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
