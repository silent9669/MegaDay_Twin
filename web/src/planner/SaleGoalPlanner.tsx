import { useEffect, useId, useRef, useState } from "react";
import { openMilo } from "../city/milo-contract";
import { Link } from "react-router";
import { ArrowRight, Check, RefreshCw, Target } from "lucide-react";
import type { Dist, ScenarioRun } from "../api/types";
import { viText } from "../lib/i18n";
import { sameAction, violationLabel } from "../pages/compare/compareLogic";
import { goalInterpretResponseSchema, type GoalDraft, type GoalKind, type GoalInterpretResponse } from "./goal-contract";
import type { PlannerContext, PlannerRunError, PlannerRunRequest, PlannerRunResponse, PlannerActiveContext, PlannerApplyResponse, PlannerGoal } from "./planner-api";
import { ActionCalculation, CalculationEvidence } from "./CalculationEvidence";
import "./planner.css";

const kinds: Record<GoalKind, string> = {
  profit: "Maximize profit", sell_target: "Reach sales target",
  clear_stock: "Clear stock", efficiency: "Improve ad efficiency",
};
const templates = [
  ["Profit", "Maximize profit for this campaign during the sale."],
  ["Sales volume", "Sell 100 units with budget 2 million VND."],
  ["Clear stock", "Clear stock with maximum loss 500 thousand VND."],
  ["Ad efficiency", "Optimize ad efficiency for this campaign."],
] as const;
const number = (value: number) => new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(value);
const money = (value: number) => `${number(value)} ₫`;
const dateText = (value?: string | null) => value
  ? new Date(value).toLocaleString("en-US", { timeZone: "Asia/Ho_Chi_Minh" }) : "Not available";
// The planning clock is always Vietnam time, regardless of the browser timezone.
function localDate(value: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return "";
  return new Date(Date.parse(value) + 7 * 3_600_000).toISOString().slice(0, 16);
}
const isoDate = (value: string) => value ? `${value}:00+07:00` : null;
function blankDraft(text: string): GoalDraft {
  return { text, kind: null, targetUnits: null, start: null, deadline: null,
    maxSpendVnd: null, maxLossVnd: null, sellTargetProbability: null,
    minMarginalReturn: null, unresolved: [] };
}
class PlannerError extends Error {
  constructor(message: string, readonly payload?: Partial<PlannerRunError>, readonly status?: number) { super(message); }
}
async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  // Intentionally bypasses the fixture-aware API client: results must be from the backend.
  const response = await fetch(`/api/sale-planner/${path}`, {
    method: body === undefined ? "GET" : "POST", cache: "no-store", signal,
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new PlannerError(payload?.error?.message || `Could not complete request (${response.status}).`, payload ?? undefined, response.status);
  if (!payload) throw new PlannerError("The server returned invalid data.");
  return payload as T;
}
function draftIssues(draft: GoalDraft): string[] {
  const issues: string[] = [];
  if (!draft.text.trim()) issues.push("Describe your goal before confirming.");
  if (!draft.kind) issues.push("Choose the campaign's primary goal.");
  if (draft.kind === "sell_target" && (!draft.targetUnits || !Number.isSafeInteger(draft.targetUnits) || draft.targetUnits < 1))
    issues.push("Target units must be a positive integer.");
  if (draft.kind !== "sell_target" && (draft.targetUnits !== null || draft.sellTargetProbability !== null))
    issues.push("Units and target probability only apply to Reach sales target. Change the goal or clear these fields.");
  if (draft.kind !== "efficiency" && draft.minMarginalReturn !== null)
    issues.push("Marginal return only applies to Improve ad efficiency. Change the goal or clear this field.");
  const start = Date.parse(draft.start ?? ""), end = Date.parse(draft.deadline ?? "");
  if (!Number.isFinite(start) || !Number.isFinite(end)) issues.push("Choose a start and end time.");
  else if (start % 3_600_000 || end % 3_600_000 || end <= start || end - start > 72 * 3_600_000)
    issues.push("Use whole hours and a duration of 1 to 72 hours.");
  if ([draft.maxSpendVnd, draft.maxLossVnd, draft.minMarginalReturn].some((v) => v !== null && (!Number.isFinite(v) || v < 0)))
    issues.push("Budget, loss limit and marginal return must be zero or greater.");
  if (draft.sellTargetProbability !== null && (!Number.isFinite(draft.sellTargetProbability) || draft.sellTargetProbability <= 0 || draft.sellTargetProbability > 1))
    issues.push("Target probability must be above 0% and at most 100%.");
  return issues;
}

type PlannerProps = {
  initialInterpretation?: GoalInterpretResponse;
  focusSku?: string;
  campaignIds?: string[];
  embedded?: boolean;
  allowRun?: boolean;
  hideHeading?: boolean;
};
// The assistant remounts this review on a new interpretation or SKU, clearing approval/results.
export function SaleGoalPlanner({ initialInterpretation, focusSku, campaignIds, embedded = false, allowRun = true, hideHeading = false }: PlannerProps = {}) {
  const id = useId();
  const [context, setContext] = useState<PlannerContext | null>(null);
  const [contextError, setContextError] = useState("");
  const [reload, setReload] = useState(0);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState(initialInterpretation?.draft.text ?? "");
  const [draft, setDraft] = useState<GoalDraft | null>(initialInterpretation?.draft ?? null);
  const [method, setMethod] = useState<"ai" | "rules" | "manual">(initialInterpretation?.method ?? "manual");
  const [notices, setNotices] = useState<string[]>(initialInterpretation?.notices ?? []);
  const [campaignId, setCampaignId] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState<"interpret" | "run" | "apply" | null>(null);
  const [error, setError] = useState("");
  const [errorDetails, setErrorDetails] = useState<Record<string, unknown> | null>(null);
  const [result, setResult] = useState<PlannerRunResponse | null>(null);
  const [active, setActive] = useState<PlannerActiveContext | null>(null);
  const [activeError, setActiveError] = useState("");
  const [activeLoading, setActiveLoading] = useState(false);
  const [activeReload, setActiveReload] = useState(0);
  const [applyConfirmed, setApplyConfirmed] = useState(false);
  const [applied, setApplied] = useState<PlannerApplyResponse | null>(null);
  const review = useRef<HTMLElement>(null), results = useRef<HTMLElement>(null);
  const activeRequest = useRef<AbortController | null>(null);
  const campaignScope = campaignIds?.join("\u0000");
  useEffect(() => () => activeRequest.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    void request<PlannerContext>("context", undefined, controller.signal).then((value) => {
      if (!controller.signal.aborted) {
        const ids = campaignScope?.split("\u0000");
        const scoped = ids ? { ...value, campaigns: value.campaigns.filter((campaign) => ids.includes(campaign.id)) } : value;
        setContext(scoped); setContextError("");
        if (campaignScope && scoped.campaigns.length === 1) {
          setActive(null); setActiveError(""); setActiveLoading(true); setActiveReload((n) => n + 1);
          setCampaignId(scoped.campaigns[0]!.id);
        }
      }
    }).catch((e: unknown) => {
      if (!controller.signal.aborted) { setContext(null); setContextError(e instanceof Error ? e.message : "Could not load campaigns."); }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [reload, campaignScope, focusSku]);
  useEffect(() => {
    const controller = new AbortController();
    if (campaignId) void request<PlannerActiveContext>(`active?campaignId=${encodeURIComponent(campaignId)}`, undefined, controller.signal)
      .then((value) => {
        if (value.campaignId !== campaignId) throw Error("Returned goal does not match the selected campaign.");
        if (!controller.signal.aborted) setActive(value);
      }).catch((e: unknown) => { if (!controller.signal.aborted) setActiveError(e instanceof Error ? e.message : "Could not load the active goal."); })
      .finally(() => { if (!controller.signal.aborted) setActiveLoading(false); });
    return () => controller.abort();
  }, [campaignId, activeReload]);
  function reloadActive() {
    setActive(null); setActiveError(""); setActiveLoading(!!campaignId); setActiveReload((n) => n + 1);
  }
  function invalidate() {
    setConfirmed(false); setResult(null); setApplyConfirmed(false); setApplied(null); setError(""); setErrorDetails(null);
  }
  function changeText(value: string) {
    invalidate(); setAcknowledged(false); setText(value); setDraft(null); setNotices([]);
  }
  function edit(patch: Partial<GoalDraft>) {
    invalidate(); setAcknowledged(false); setDraft((old) => old ? { ...old, ...patch } : old);
  }
  function openReview() { requestAnimationFrame(() => review.current?.focus()); }
  async function interpret() {
    invalidate(); setAcknowledged(false); setBusy("interpret"); setDraft(null);
    const controller = new AbortController(); activeRequest.current = controller;
    try {
      const interpreted = goalInterpretResponseSchema.parse(await request("interpret", { text }, controller.signal));
      if (controller.signal.aborted) return;
      setDraft({ ...interpreted.draft, text }); setMethod(interpreted.method); setNotices(interpreted.notices); openReview();
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Could not interpret the goal. You can enter it manually."); }
    finally { if (!controller.signal.aborted) setBusy(null); }
  }
  const issues = draft ? draftIssues(draft) : [];
  const selected = context?.campaigns.find((c) => c.id === campaignId);
  const canConfirm = !!draft && !issues.length && (!draft.unresolved.length || acknowledged);
  const canRun = allowRun && canConfirm && confirmed && !!selected && !!context?.backendConfigured && context.canPreview === true && !!active && !activeLoading && !busy && !loading;
  async function run() {
    if (!canRun || !draft) return;
    setBusy("run"); setError(""); setErrorDetails(null);
    const controller = new AbortController(); activeRequest.current = controller;
    try {
      const body: PlannerRunRequest = { campaignId, draft, confirmed: true, acknowledgedUnresolved: acknowledged, expectedGoal: active!.baseGoal, ...(focusSku ? { focusSku } : {}) };
      const response = await request<PlannerRunResponse>("run", body, controller.signal);
      if (controller.signal.aborted) return;
      setResult(response); setApplyConfirmed(false); setApplied(null);
      requestAnimationFrame(() => results.current?.focus());
    } catch (e) {
      if (controller.signal.aborted) return;
      if (e instanceof PlannerError) {
        setErrorDetails(e.payload?.error?.details ?? null);
        if (e.status === 409) {
          setResult(null); setConfirmed(false); reloadActive();
        }
      }
      setError(e instanceof Error ? e.message : "Simulation did not complete.");
    } finally { if (!controller.signal.aborted) setBusy(null); }
  }
  async function apply() {
    if (!result?.canApply || !applyConfirmed || applied || busy || context?.canApply !== true) return;
    setBusy("apply"); setError(""); setErrorDetails(null);
    const controller = new AbortController(); activeRequest.current = controller;
    try {
      const response = await request<PlannerApplyResponse>("apply", { previewId: result.previewId, confirmed: true,
        expectedGoal: result.baseGoal ? { goal_id: result.baseGoal.goal_id, version: result.baseGoal.version } : null }, controller.signal);
      if (controller.signal.aborted) return;
      setApplied(response); setApplyConfirmed(false); reloadActive();
    } catch (e) {
      if (controller.signal.aborted) return;
      setError(e instanceof Error ? e.message : "Could not apply the goal.");
      if (e instanceof PlannerError) {
        setErrorDetails(e.payload?.error?.details ?? null);
        if (e.status === 409) {
          setResult(null); setConfirmed(false); setApplyConfirmed(false); reloadActive();
        }
      }
    } finally { if (!controller.signal.aborted) setBusy(null); }
  }
  return (
    <div className={`sale-planner ${embedded ? "planner-embedded" : ""}`}>
      {!embedded && <>
      {!hideHeading &&
      <header className="planner-heading">
        <div><span className="planner-eyebrow">MEGADAY HQ / PLANNING</span><h1><Target aria-hidden size={26} /> Sale planning</h1>
          <p>Review your goal, then compare options using campaign data.</p></div>
        <Link to="/mock-data">Check data <ArrowRight aria-hidden size={16} /></Link>
      </header>}
      <ol className="planner-steps" aria-label="Planning steps">
        <li aria-current={!draft ? "step" : undefined}><span>1</span> Describe goal</li>
        <li aria-current={draft && !result ? "step" : undefined}><span>2</span> Review & confirm</li>
        <li aria-current={result && !applied ? "step" : undefined}><span>3</span> Preview simulation</li>
        <li aria-current={applied ? "step" : undefined}><span>4</span> Apply goal</li>
      </ol>
      </>}
      <section className="planner-context" aria-label="Data connection">
        <div role="status"><strong>{loading ? "Checking data…" : context?.backendConfigured ? "Connected campaigns" : "Decision backend not connected"}</strong>
          <p>{contextError || context?.notice || (context?.backendConfigured ? `Data as of: ${dateText(context.dataAsOf)} (Vietnam time).` : "You can draft and review goals. Connect the decision backend to run simulations.")}</p>
          {context?.backendConfigured && !context.canPreview && <p>Preview simulation is unavailable. Check backend capabilities and permissions.</p>}
          {context?.backendConfigured && !context.campaigns.length && <p>No campaigns. Import and send data to the backend from Data Studio first.</p>}</div>
        <button type="button" disabled={loading || !!busy} onClick={() => { invalidate(); setLoading(true); setReload((n) => n + 1); reloadActive(); }}><RefreshCw aria-hidden size={16} /> Refresh</button>
      </section>
      <section className="planner-card planner-campaign-selection">
              <label htmlFor={`${id}-sale-campaign`}>Campaign</label><select disabled={loading || !!busy} id={`${id}-sale-campaign`} value={campaignId} onChange={(e) => { invalidate(); setActive(null); setActiveError(""); setActiveLoading(!!e.target.value); setCampaignId(e.target.value); }}><option value="">Choose a connected campaign</option>{context?.campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
              {campaignId && <section className="planner-active-goal" aria-label="Active goal">
                <h3>Active goal</h3>
                {activeLoading ? <p role="status">Checking the campaign goal…</p> : activeError ? <><p role="alert">{activeError}</p><button type="button" onClick={reloadActive}>Reload active goal</button></> : active ? <GoalSummary goal={active.activeGoal} /> : <p>The active goal has not been verified.</p>}
                <p className="planner-help">Preview simulations preserve this goal. {focusSku ? "SKU goals must respect campaign limits; stricter constraints are retained." : "Review the changes before applying."}</p>
                {!initialInterpretation && !focusSku && <button type="button" onClick={() => openMilo({ screen: "sale-planner", campaignId })}>Discuss campaign with Milo</button>}
              </section>}
      </section>
      <div className={`planner-layout ${embedded ? "planner-review-only" : ""}`}>
        {(!embedded || !initialInterpretation) &&
        <section className="planner-card" aria-labelledby={`${id}-planner-input-title`}>
          <span className="planner-eyebrow">STEP 1</span><h2 id={`${id}-planner-input-title`}>What do you want to achieve?</h2>
          <fieldset disabled={!!busy}>
            <label htmlFor={`${id}-sale-goal-text`}>Goal description</label>
            <textarea id={`${id}-sale-goal-text`} rows={5} maxLength={4000} value={text} onChange={(e) => changeText(e.target.value)} placeholder="Example: Sell 100 units in 12 hours with an ad budget of up to 2 million VND…" aria-describedby={`${id}-sale-goal-text-hint`} />
            <p id={`${id}-sale-goal-text-hint`} className="planner-help">Include your priority, time window, budget and risk limit. {text.length}/4,000 characters.</p>
            <div className="planner-templates" aria-label="Goal templates">{templates.map(([label, value]) => <button type="button" key={label} onClick={() => changeText(value)}>{label}</button>)}</div>
            <div className="planner-actions"><button className="planner-primary" type="button" disabled={!text.trim()} onClick={() => void interpret()}>{busy === "interpret" ? "Interpreting…" : "Interpret goal"}<ArrowRight aria-hidden size={16} /></button>
              <button type="button" disabled={!text.trim()} onClick={() => { invalidate(); setAcknowledged(false); setDraft(blankDraft(text)); setMethod("manual"); setNotices([]); openReview(); }}>Enter parameters manually</button></div>
          </fieldset>
          <aside className="planner-scope"><strong>Planning scope</strong><p>One campaign, over 1–72 hours. Compare continuing, pausing, reducing budget and scheduling ads.</p><p>Does not optimize budgets across campaigns or execute price, voucher or ad changes.</p></aside>
        </section>}
        <section ref={review} tabIndex={-1} className="planner-card planner-review" aria-labelledby={`${id}-planner-review-title`}>
          <span className="planner-eyebrow">STEP 2</span><h2 id={`${id}-planner-review-title`}>Review parameters</h2>
          {!draft ? <div className="planner-empty"><Target aria-hidden size={30} /><p>Interpret a goal to review and edit its parameters.</p></div> : <>
            <p className="planner-help">{method === "ai" ? "AI-interpreted draft" : method === "rules" ? "Rule-based draft" : "Manual draft"}. These parameters will be used for the calculation.</p>
            {embedded && <details><summary>Interpreted goal</summary><pre>{draft.text}</pre></details>}
            {!!notices.length && <ul className="planner-notices">{notices.map((notice, i) => <li key={i}>{notice}</li>)}</ul>}
            <fieldset disabled={!!busy}>
              {focusSku && <div className="planner-warning"><strong>Goal for SKU {focusSku}</strong><p>Sales and clearance quantities apply to this SKU. Economic value J, budget, expected loss and ad efficiency apply to the whole selected campaign; ad changes can affect other SKUs.</p><p>Confirm that you have reviewed this scope. This is not a SKU-only profit calculation.</p></div>}
              <label htmlFor={`${id}-sale-kind`}>Primary goal</label><select id={`${id}-sale-kind`} value={draft.kind ?? ""} onChange={(e) => edit({ kind: (e.target.value || null) as GoalKind | null })}><option value="">Choose a goal</option>{Object.entries(kinds).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
              <div className="planner-fields">
                <label>Start (Vietnam time)<input type="datetime-local" step={3600} value={localDate(draft.start)} onChange={(e) => edit({ start: isoDate(e.target.value) })} /></label>
                <label>End (Vietnam time)<input type="datetime-local" step={3600} value={localDate(draft.deadline)} onChange={(e) => edit({ deadline: isoDate(e.target.value) })} /></label>
              </div>
              <p className="planner-help">Use whole hours, up to 72 hours. Check the window against imported data.</p>
              <div className="planner-fields"><label>Target units<input type="number" min={1} step={1} value={draft.targetUnits ?? ""} onChange={(e) => edit({ targetUnits: e.target.value ? Number(e.target.value) : null })} /></label><label>Minimum target probability (%)<input type="number" min={0.01} max={100} step="any" value={draft.sellTargetProbability === null ? "" : draft.sellTargetProbability * 100} placeholder="Backend default" onChange={(e) => edit({ sellTargetProbability: e.target.value ? Number(e.target.value) / 100 : null })} /></label></div>
              <p className="planner-help">These two fields apply only to Reach sales target. Leave them blank for other goals.</p>
              <div className="planner-fields"><label>Maximum ad budget (₫)<input type="number" min={0} step="any" value={draft.maxSpendVnd ?? ""} placeholder="No additional limit" onChange={(e) => edit({ maxSpendVnd: e.target.value ? Number(e.target.value) : null })} /></label><label>Maximum expected loss (₫)<input type="number" min={0} step="any" value={draft.maxLossVnd ?? ""} placeholder={draft.kind === "clear_stock" ? "Backend default" : "No additional limit"} onChange={(e) => edit({ maxLossVnd: e.target.value ? Number(e.target.value) : null })} /></label></div>
              <p className="planner-help">Budget covers the full simulation window. The loss limit applies to expected J; it does not guarantee every outcome stays within the limit.</p>
              <label>Minimum marginal return<input type="number" min={0} step="any" value={draft.minMarginalReturn ?? ""} placeholder="Backend default" onChange={(e) => edit({ minMarginalReturn: e.target.value ? Number(e.target.value) : null })} /><span className="planner-help">Only applies to Improve ad efficiency. Leave blank for the backend default, or for any other goal.</span></label>
              {draft.unresolved.length > 0 && <div className="planner-warning"><strong>Unresolved or unsupported requirements</strong><ul>{draft.unresolved.map((item, i) => <li key={i}>{item}</li>)}</ul><p>Revise and reinterpret the goal, or confirm only the reviewed parameters.</p><label className="planner-check"><input type="checkbox" checked={acknowledged} onChange={(e) => { invalidate(); setAcknowledged(e.target.checked); }} /><span>I have reviewed and agree to exclude requirements not represented by these parameters from this simulation.</span></label></div>}
              {!!issues.length && <ul className="planner-validation" aria-label="Required information">{issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>}
              <div className="planner-confirm"><label className="planner-check"><input type="checkbox" checked={confirmed} disabled={!canConfirm} onChange={(e) => setConfirmed(e.target.checked)} /><span>I confirm these parameters for a preview. The active goal and recommendation remain unchanged.</span></label><p>Edits require confirmation again. Previewing does not apply a goal or execute ads.</p></div>
              <button className="planner-primary planner-run" type="button" disabled={!canRun} onClick={() => void run()}>{busy === "run" ? "Previewing…" : "Preview simulation"}<ArrowRight aria-hidden size={16} /></button>
              {!selected && <p className="planner-help">Choose a connected campaign to run.</p>}
            </fieldset>
          </>}
        </section>
      </div>
      {error && <div className="planner-error" role="alert"><strong>{error}</strong>{errorDetails && <details><summary>Data details to review</summary><pre>{JSON.stringify(errorDetails, null, 2)}</pre></details>}</div>}
      {result && <section ref={results} tabIndex={-1} className="planner-card planner-results" aria-labelledby={`${id}-planner-results-title`}>
        <PlannerResults id={id} result={result} campaignName={selected?.name ?? campaignId} />
        <section className="planner-apply" aria-labelledby={`${id}-planner-apply-title`}><span className="planner-eyebrow">STEP 4 / EXPLICIT APPLICATION</span><h2 id={`${id}-planner-apply-title`}>Review before applying</h2>
          <div className="planner-goal-comparison"><div><h3>Active at preview time</h3><GoalSummary goal={result.baseGoal} /></div><div><h3>Preview goal</h3><GoalSummary goal={result.effectiveGoal} /></div></div>
          {result.changes.length > 0 ? <div className="planner-change-table"><table role="table"><caption>Proposed changes</caption><thead role="rowgroup"><tr role="row"><th scope="col">Parameter</th><th scope="col">Before</th><th scope="col">After</th></tr></thead><tbody role="rowgroup">{result.changes.map((change, i) => <tr role="row" key={`${change.field}-${i}`}><th scope="row" role="rowheader">{changeLabel(change.field)}</th>{([['Before', change.before], ['After', change.after]] as const).map(([label, value]) => <td role="cell" key={label}><span className="planner-change-mobile-label" aria-hidden="true">{label}</span><span className={typeof value === 'number' ? 'planner-change-number' : undefined}>{changeValue(change.field, value)}</span></td>)}</tr>)}</tbody></table></div> : <p>No parameter changes from the base goal were reported.</p>}
          {!!result.notices.length && <ul className="planner-notices">{result.notices.map((notice, i) => <li key={i}>{notice}</li>)}</ul>}
          {applied ? <p className="planner-saved" role="status"><Check aria-hidden size={16} /> Applied goal {applied.goal.goal_id}. This does not execute ads.</p> : <>
            {!result.canApply && <p className="planner-warning">This preview cannot be applied. Review unmet conditions, revise the goal and preview again.</p>}
            {context?.canApply !== true && <p className="planner-warning">This account cannot apply goals.</p>}
            <label className="planner-check"><input type="checkbox" checked={applyConfirmed} disabled={!result.canApply || context?.canApply !== true || !!busy} onChange={(e) => setApplyConfirmed(e.target.checked)} /><span>I have reviewed the changes and want to replace the active campaign goal with this goal.</span></label>
            <button type="button" className="planner-primary" disabled={!result.canApply || !applyConfirmed || context?.canApply !== true || !!busy} onClick={() => void apply()}>{busy === "apply" ? "Applying goal…" : "Apply goal"}</button>
            <p className="planner-help">If someone changes the active goal, review and preview again. Separate previews do not reserve shares of a common budget.</p>
          </>}
        </section>
      </section>}
    </div>
  );
}

function changeLabel(field: string): string {
  const key = field.replace(/^params\./, "");
  return ({ kind: "Priority", target_units: "Target units", deadline: "Deadline", max_spend_vnd: "Maximum budget", max_loss_vnd: "Maximum expected loss", sell_target_prob: "Minimum target probability", min_marginal_return: "Minimum ad efficiency", focus_sku: "Focused product", planner_start: "Start", parent_goal_id: "Base campaign goal" } as Record<string, string>)[key] ?? key.replaceAll("_", " ");
}
function changeValue(field: string, value: unknown): string {
  if (value == null) return "Not set";
  if (field.endsWith("kind") && typeof value === "string") return kinds[value as GoalKind] ?? value;
  if (typeof value === "number") return field.endsWith("_vnd") ? money(value) : field.endsWith("_prob") ? `${number(value * 100)}%` : number(value);
  if (typeof value === "string") return field === "deadline" || field.endsWith("planner_start") ? dateText(value) : value;
  return JSON.stringify(value);
}
function GoalSummary({ goal }: { goal: PlannerGoal | null }) {
  if (!goal) return <p>No active goal.</p>;
  return <dl className="planner-goal-summary"><dt>Priority</dt><dd>{goal.label ?? kinds[goal.kind as GoalKind] ?? goal.kind}</dd>
    {goal.target_units !== null && <><dt>Target units</dt><dd>{number(goal.target_units)} units</dd></>}
    <dt>Deadline</dt><dd>{dateText(goal.deadline)}</dd>
    {(["focus_sku", "max_spend_vnd", "max_loss_vnd", "sell_target_prob", "min_marginal_return"] as const).map((key) => goal.params[key] != null ? <div className="planner-goal-line" key={key}><dt>{changeLabel(key)}</dt><dd>{changeValue(key, goal.params[key])}</dd></div> : null)}
    <dt>Version</dt><dd>{goal.version}</dd>
  </dl>;
}

function Metric({ title, dist, currency = false, field, evidenceId }: { evidenceId: string; title: string; dist: Dist; currency?: boolean; field: string }) {
  const format = currency ? money : number;
  return <details className="planner-metric"><summary><span>{title} (expected)</span><strong>{format(dist.mean)}</strong></summary><div><p>P10: {format(dist.p10)} · P50: {format(dist.p50)} · P90: {format(dist.p90)}</p><p>Mean and quantiles from backend simulation samples are estimates, not guaranteed outcomes.</p><p>Data field: <code>{field}</code>. <a href={`#${evidenceId}`}>View sources and parameters</a></p></div></details>;
}
function PlannerResults({ id, result, campaignName }: { id: string; result: PlannerRunResponse; campaignName: string }) {
  const { data: scenario, meta } = result.scenario, rec = scenario.recommendation;
  const status: Record<NonNullable<ScenarioRun["recommendation"]>["status"], string> = { recommend: "Recommended option available", undetermined: "No clearly better option", none_meets_goal: "No option meets the goal" };
  return <>
    <span className="planner-eyebrow">STEP 3 / BACKEND RESULTS</span><h2 id={`${id}-planner-results-title`}>Options for {campaignName}</h2>
    <div className="planner-result-status" role="status"><strong>{scenario.blocked ? "Simulation blocked by data issues" : rec ? status[rec.status] : "No recommendation returned"}</strong>
      {rec?.status === "recommend" && rec.best && <p>Recommendation: {viText.action(rec.best)}. Requires review before execution.</p>}
      {rec?.reasons.length ? <ul>{rec.reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul> : null}
    </div>
    <p>Expand metrics for P10–P90 and sources. J = contribution − ad spend + leftover value − stockout penalty. This model economic value includes inventory value.</p>
    <div className="planner-options">{scenario.results.map((option, i) => {
      const best = rec?.status === "recommend" && sameAction(option.action, rec.best);
      return <article className={`planner-option ${best ? "planner-option-best" : ""}`} key={option.key || i}>
        <header><h3>{viText.action(option.action)}</h3>{best && <span className="planner-badge">Recommended</span>}</header>
        <p className="planner-option-status">{!option.feasible ? "Infeasible" : !option.goal_eligible ? "Does not meet goal conditions" : "Meets model conditions"}</p>
        {!!option.violations.length && <ul>{option.violations.map((v) => <li key={v}>{violationLabel(v)}</li>)}</ul>}
        {option.violation_details?.map((v, j) => <p key={j}>{v}</p>)}{option.goal_note && <p>{option.goal_note}</p>}
        <Metric evidenceId={`${id}-planner-evidence`} title="Economic value J" dist={option.objective} currency field={`results[${i}].objective`} />
        <Metric evidenceId={`${id}-planner-evidence`} title="Ad spend" dist={option.spend} currency field={`results[${i}].spend`} />
        {option.goal_sku && option.goal_units_sold && <Metric evidenceId={`${id}-planner-evidence`} title={`Units sold · ${option.goal_sku}`} dist={option.goal_units_sold} field={`results[${i}].goal_units_sold`} />}
        <Metric evidenceId={`${id}-planner-evidence`} title={option.goal_sku ? "Units sold · whole campaign" : "Units sold"} dist={option.units_sold} field={`results[${i}].units_sold`} />
        {option.p_target != null && <p>Probability of reaching target units: <strong>{number(option.p_target * 100)}%</strong></p>}
        <details className="planner-components"><summary>Economic components and inventory</summary><Metric evidenceId={`${id}-planner-evidence`} title="Contribution" dist={option.contribution} currency field={`results[${i}].contribution`} /><Metric evidenceId={`${id}-planner-evidence`} title="Leftover value" dist={option.leftover_value} currency field={`results[${i}].leftover_value`} /><Metric evidenceId={`${id}-planner-evidence`} title="Leftover units" dist={option.leftover_units} field={`results[${i}].leftover_units`} />{option.lost_units && <Metric evidenceId={`${id}-planner-evidence`} title="Unmet demand" dist={option.lost_units} field={`results[${i}].lost_units`} />}{option.stockout_hour_p50 && <p>Stockout P50: {dateText(option.stockout_hour_p50)}</p>}</details>
        {option.duplicate_of && <p className="planner-help">Same result as option: {option.duplicate_of}.</p>}
        {scenario.calculation_trace?.version === 1 && scenario.calculation_trace.actions.find((action) => action.key === option.key) && <ActionCalculation action={scenario.calculation_trace.actions.find((action) => action.key === option.key)!} index={i} />}
      </article>;
    })}</div>
    {!scenario.results.length && <p>No simulation results to compare. Check source data and backend notices.</p>}
    <CalculationEvidence trace={scenario.calculation_trace} />
    <button type="button" onClick={() => openMilo({ screen: 'sale-planner',
      ...(scenario.calculation_trace?.focus_sku ? { sku: scenario.calculation_trace.focus_sku } : {}),
      campaignId: scenario.request.campaign_id, scenarioId: scenario.scenario_id,
    })}>Ask Milo to explain</button>
    <details className="planner-evidence" id={`${id}-planner-evidence`}><summary>Calculation sources and parameters</summary><dl>
      <dt>Simulation ID</dt><dd>{scenario.scenario_id}</dd><dt>Goal</dt><dd>{result.goalId}</dd>
      <dt>Data as of</dt><dd>{dateText(rec?.inputs_ref.data_as_of ?? meta?.data_as_of)} (Vietnam time)</dd>
      <dt>Simulation window</dt><dd>{dateText(scenario.request.start)} · {scenario.request.horizon_hours} hours</dd>
      <dt>Samples / seed</dt><dd>{scenario.request.samples} / {scenario.request.seed}</dd>
      <dt>iROAS source</dt><dd>{rec?.inputs_ref.iroas_source === "prior" ? "Model prior assumption" : rec?.inputs_ref.iroas_source === "estimate" ? "Data estimate" : "Not provided"}</dd>
      <dt>Input forecasts</dt><dd>{rec?.inputs_ref.forecast_ids.join(", ") || "Not provided"}</dd>
      <dt>Parameter versions</dt><dd><pre>{JSON.stringify(rec?.inputs_ref.param_version ?? {}, null, 2)}</pre></dd>
      {scenario.input_hash && <><dt>Input hash</dt><dd>{scenario.input_hash}</dd></>}
    </dl>{rec?.flip_conditions.length ? <><h3>When should this recommendation be reviewed?</h3><ul>{rec.flip_conditions.map((item, i) => <li key={i}>{item.text}</li>)}</ul></> : null}
      <Link to="/mock-data">Open Data Studio</Link>
    </details>
  </>;
}
