import { Button } from "../components/ui/button";
import { useCallback, useEffect, useRef, useState } from "react";
import { Tags } from "lucide-react";
import type { StockRow } from "./source-view";
import { requestJSON } from "./account";
import { DEMAND_LABELS, labelContextSchema, type LabelResponse } from "./product-labels";

const PAGE_SIZE = 8;
export function ProductLabels({ sourceRevision, blocked, inventory, selectedCode }: { sourceRevision: number; blocked: boolean; inventory?: StockRow[]; selectedCode?: string }) {
  const [data, setData] = useState<LabelResponse | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [search, setSearch] = useState(selectedCode ?? "");
  const [page, setPage] = useState(0);
  useEffect(() => {
    if (!selectedCode) return;
    const timer = setTimeout(() => { setSearch(selectedCode); setPage(0); }, 0);
    return () => clearTimeout(timer);
  }, [selectedCode]);
  const sequence = useRef(0);
  const posting = useRef(false);
  const invalidate = useCallback(() => { sequence.current++; }, []);
  const reload = useCallback(async () => {
    const ticket = ++sequence.current;
    try {
      const next = await requestJSON<LabelResponse>("/api/product-labels");
      if (ticket !== sequence.current) return;
      setData(next);
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
  const stale = !!data && (!current || data.stale);
  const context = !stale && data?.run?.context ? data.run.context : { market: "Vietnam", asOf: new Date().toISOString().slice(0, 10), evidence: [] };
  const running = submitting || (!!current && !data?.stale && data?.run?.status === "running");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void reload(), 3000);
    return () => clearInterval(timer);
  }, [running, reload]);
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
  const rows: StockRow[] = inventory?.filter(row => (row.stock ?? 0) > 0 || row.code === selectedCode) ?? products.map(p => ({ code: p.reference, name: p.name, stock: null, asOf: "" }));
  const hasDemand = rows.some(row => row.demand !== undefined) || !!data?.run?.results.length;
  const productByCode = new Map(products.map(product => [product.reference, product]));
  const filtered = rows.filter(p => `${p.name} ${p.code}`.toLowerCase().includes(search.toLowerCase()));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const activePage = Math.min(page, pages - 1);
  return <section className="product-label-panel" aria-labelledby="product-label-title">
    <header><h3 id="product-label-title"><Tags size={18} aria-hidden /> Inventory</h3>
      <div className="product-label-actions">
        <Button variant="outline" disabled={blocked || !current || !data?.configured || running || !products.length} onClick={() => void run()}>
          {running ? "Labeling…" : "Label data"}
        </Button>
      </div>
    </header>
    {!data && !error && <p role="status">Loading product labels…</p>}
    {data && !data.configured && <p role="status">Decision model is not connected.</p>}
    {stale && <p role="status">Labels need updating for these sources or assessment details.</p>}
    {blocked && <p role="status">Save source changes before labeling.</p>}
    {(error || (current && !stale && data?.run?.error)) && <p role="alert">{error || data?.run?.error}</p>}
    <label className="product-label-search">Search products<input value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} placeholder="Name or product code" /></label>
    <div className="warehouse-table" role="region" aria-label="Inventory table" tabIndex={0}><table>
      <thead><tr><th>Product</th><th>Available units</th>{hasDemand && <th>Demand label</th>}<th>Source timestamp</th></tr></thead>
      <tbody>{filtered.slice(activePage * PAGE_SIZE, (activePage + 1) * PAGE_SIZE).map(row => {
        const product = productByCode.get(row.code);
        const label = product ? results.get(product.id) : undefined;
        return <tr key={row.code} className={row.code === selectedCode ? "stock-highlight" : undefined}><td><strong>{row.name}</strong><br /><code>{row.code}</code></td>
          <td>{row.stock === null ? "Unknown" : row.stock.toLocaleString()}</td>
          {hasDemand && <td><strong className="product-demand-label">{!stale && !blocked && !running && data?.run?.status !== "failed" ? label?.group ? DEMAND_LABELS[label.group] : row.demand || "Unclassified" : "Unclassified"}</strong>
            {label && <p>{label.reason}</p>}</td>}<td>{row.asOf || "—"}</td></tr>;
      })}</tbody>
    </table></div>
    {!rows.length && <p>No products in stock.</p>}
    {pages > 1 && <nav className="product-label-pagination" aria-label="Product label pages">
      <Button variant="outline" disabled={activePage === 0} onClick={() => setPage(activePage - 1)}>Previous</Button>
      <span>{activePage + 1} / {pages}</span>
      <Button variant="outline" disabled={activePage + 1 >= pages} onClick={() => setPage(activePage + 1)}>Next</Button>
    </nav>}

  </section>;
}
