import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, Tags } from "lucide-react";
import type { StockRow } from "./source-view";
import { requestJSON } from "./account";
import { DEMAND_LABELS, labelContextSchema, type LabelContext, type LabelResponse } from "./product-labels";

const PAGE_SIZE = 8;
export function ProductLabels({ sourceRevision, blocked, inventory }: { sourceRevision: number; blocked: boolean; inventory?: StockRow[] }) {
  const [data, setData] = useState<LabelResponse | null>(null);
  const [context, setContext] = useState<LabelContext>({ market: "Vietnam", asOf: new Date().toISOString().slice(0, 10), evidence: [] });
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [evidence, setEvidence] = useState({ productCode: "", observedAt: "", source: "", observation: "" });
  const edited = useRef(false);
  const sequence = useRef(0);
  const posting = useRef(false);
  const invalidate = useCallback(() => { sequence.current++; }, []);
  const reload = useCallback(async () => {
    const ticket = ++sequence.current;
    try {
      const next = await requestJSON<LabelResponse>("/api/product-labels");
      if (ticket !== sequence.current) return;
      setData(next);
      if (!edited.current && next.run?.context) setContext(next.run.context);
    } catch (e) { if (ticket === sequence.current) setError(e instanceof Error ? e.message : "Labels unavailable."); }
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => void reload(), 0);
    const changed = (event: Event) => {
      const key = (event as CustomEvent).detail;
      if (!key || key === "product-labels" || key === "sources") void reload();
    };
    window.addEventListener("workspace:changed", changed);
    return () => { clearTimeout(timer); invalidate(); window.removeEventListener("workspace:changed", changed); };
  }, [sourceRevision, reload, invalidate]);
  const current = data?.sourceRevision === sourceRevision;
  const contextChanged = !!data?.run && JSON.stringify(context) !== JSON.stringify(data.run.context);
  const stale = !!data && (!current || data.stale || contextChanged);
  const running = submitting || (!!current && !data?.stale && data?.run?.status === "running");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void reload(), 3000);
    return () => clearInterval(timer);
  }, [running, reload]);
  function updateContext(next: LabelContext) { edited.current = true; setContext(next); }
  async function run() {
    if (posting.current || blocked || !data?.configured || !current || running) return;
    const parsed = labelContextSchema.safeParse(context);
    if (!parsed.success) { setError("Provide a market, assessment date, and evidence dated on or before that date."); return; }
    posting.current = true; setSubmitting(true); setError("");
    try {
      await requestJSON<LabelResponse>("/api/product-labels", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourceRevision, context: parsed.data }),
      });
      window.dispatchEvent(new CustomEvent("workspace:changed", { detail: "sources" }));
    } catch (e) { setError(e instanceof Error ? e.message : "Labeling failed."); }
    finally { posting.current = false; setSubmitting(false); void reload(); }
  }
  const results = new Map(current && !stale && !blocked && !running && data?.run?.status !== "failed"
    ? data?.run?.results.map(r => [r.productId, r]) ?? [] : []);
  const products = current ? data?.products ?? [] : [];
  const rows: StockRow[] = inventory ?? products.map(p => ({ code: p.reference, name: p.name, stock: null, asOf: "" }));
  const hasDemand = rows.some(row => row.demand !== undefined) || !!data?.run?.results.length;
  const filtered = rows.filter(p => `${p.name} ${p.code}`.toLowerCase().includes(search.toLowerCase()));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const activePage = Math.min(page, pages - 1);
  return <section className="product-label-panel" aria-labelledby="product-label-title">
    <header><h3 id="product-label-title"><Tags size={18} aria-hidden /> Inventory</h3>
      <div className="product-label-actions">
        <button disabled={submitting} onClick={() => { setError(""); void reload(); }} aria-label="Refresh product labels"><RefreshCw size={15} aria-hidden /> Refresh</button>
        <button disabled={blocked || !current || !data?.configured || running || !products.length} onClick={() => void run()}>
          {running ? "Labeling…" : "Label data"}
        </button>
      </div>
    </header>
    {!data && !error && <p role="status">Loading product labels…</p>}
    {data && !data.configured && <p role="status">Decision model is not connected.</p>}
    {stale && <p role="status">Labels need updating for these sources or assessment details.</p>}
    {blocked && <p role="status">Save source changes before labeling.</p>}
    {(error || (current && !stale && data?.run?.error)) && <p role="alert">{error || data?.run?.error}</p>}
    <fieldset className="label-context" disabled={running}>
      <legend>Assessment</legend>
      <label>Market<input value={context.market} maxLength={100} onChange={e => updateContext({ ...context, market: e.target.value })} /></label>
      <label>Assessment date<input type="date" value={context.asOf} onChange={e => updateContext({ ...context, asOf: e.target.value })} /></label>
    </fieldset>
    <details className="label-evidence"><summary>Evidence ({context.evidence.length})</summary>
      <fieldset className="label-context" disabled={running}>
        <legend>Add product evidence</legend>
        <label>Product code<select aria-label="Product code" value={evidence.productCode} onChange={e => setEvidence({ ...evidence, productCode: e.target.value })}>
          <option value="">Select product</option>{products.map(p => <option key={p.id} value={p.reference}>{p.reference} · {p.name}</option>)}
        </select></label>
        <label>Observation date<input type="date" max={context.asOf} value={evidence.observedAt} onChange={e => setEvidence({ ...evidence, observedAt: e.target.value })} /></label>
        <label>Evidence source<input maxLength={300} value={evidence.source} onChange={e => setEvidence({ ...evidence, source: e.target.value })} /></label>
        <label>Observation<textarea maxLength={2000} value={evidence.observation} onChange={e => setEvidence({ ...evidence, observation: e.target.value })} /></label>
        <button onClick={() => {
          const parsed = labelContextSchema.safeParse({ ...context, evidence: [...context.evidence, evidence] });
          if (!parsed.success) { setError("Choose a product and provide a source, observation, and date on or before the assessment date."); return; }
          updateContext(parsed.data); setError(""); setEvidence({ productCode: "", observedAt: "", source: "", observation: "" });
        }}>Add evidence</button>
      </fieldset>
      <ul>{context.evidence.map((item, i) => <li key={i}>
        <strong>{item.productCode}</strong> · {item.observedAt} · {item.source}<p>{item.observation}</p>
        <button disabled={running} aria-label={`Remove evidence ${i + 1}`} onClick={() => updateContext({ ...context, evidence: context.evidence.filter((_, index) => index !== i) })}>Remove</button>
      </li>)}</ul>
    </details>
    <label className="product-label-search">Search products<input value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} placeholder="Name or product code" /></label>
    <div className="warehouse-table" role="region" aria-label="Inventory table" tabIndex={0}><table>
      <thead><tr><th>Product</th><th>Available units</th>{hasDemand && <th>Demand label</th>}<th>Source timestamp</th></tr></thead>
      <tbody>{filtered.slice(activePage * PAGE_SIZE, (activePage + 1) * PAGE_SIZE).map(row => {
        const product = products.find(p => p.reference === row.code);
        const label = product ? results.get(product.id) : undefined;
        return <tr key={row.code}><td><strong>{row.name}</strong><br /><code>{row.code}</code></td>
          <td>{row.stock === null ? "Unknown" : row.stock.toLocaleString()}</td>
          {hasDemand && <td><strong className="product-demand-label">{!stale && !blocked && !running && data?.run?.status !== "failed" ? label?.group ? DEMAND_LABELS[label.group] : row.demand || "Unclassified" : "Unclassified"}</strong>
            {label && <p>{label.reason}</p>}</td>}<td>{row.asOf || "—"}</td></tr>;
      })}</tbody>
    </table></div>
    {!rows.length && <p>No inventory observations. Load inventory in Data Studio.</p>}
    <nav className="product-label-pagination" aria-label="Product label pages">
      <button disabled={activePage === 0} onClick={() => setPage(activePage - 1)}>Previous</button>
      <span>{activePage + 1} / {pages}</span>
      <button disabled={activePage + 1 >= pages} onClick={() => setPage(activePage + 1)}>Next</button>
    </nav>
    {data?.run && !stale && <small>{data.run.provider} · {data.run.context?.market} · {data.run.context?.asOf}</small>}
  </section>;
}
