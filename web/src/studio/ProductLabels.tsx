import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronUp, RefreshCw, Tags } from "lucide-react";
import { requestJSON } from "./account";
import { DEMAND_LABELS, type LabelResponse } from "./product-labels";

const identities = {
  platform_sku: "Platform SKU",
  internal_code: "Internal code",
  name_only: "Name only · matching needed",
};
const PAGE_SIZE = 8;

export function ProductLabels({ sourceRevision, autoRunRevision, blocked }: {
  sourceRevision: number;
  autoRunRevision: number | null;
  blocked: boolean;
}) {
  const [data, setData] = useState<LabelResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const sequence = useRef(0);
  const latestRevision = useRef(sourceRevision);
  const handledImport = useRef<number | null>(null);
  const posting = useRef(false);
  const invalidateRequests = useCallback(() => { sequence.current++; }, []);
  useEffect(() => { latestRevision.current = sourceRevision; }, [sourceRevision]);

  const reload = useCallback(async (preserveError = false) => {
    const ticket = ++sequence.current;
    setLoading(true);
    try {
      const next = await requestJSON<LabelResponse>("/api/product-labels");
      if (ticket === sequence.current) {
        setData(next);
        if (!preserveError) setError("");
      }
    } catch (e) {
      if (ticket === sequence.current)
        setError(e instanceof Error ? e.message : "Product labels could not be loaded.");
    } finally {
      if (ticket === sequence.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => void reload(), 0);
    const changed = (event: Event) => {
      const key = (event as CustomEvent).detail;
      if (!key || key === "product-labels" || key === "sources") void reload();
    };
    window.addEventListener("workspace:changed", changed);
    return () => {
      clearTimeout(timer);
      invalidateRequests();
      window.removeEventListener("workspace:changed", changed);
    };
  }, [sourceRevision, reload, invalidateRequests]);

  const current = data?.sourceRevision === sourceRevision;
  const stale = !!data && (!current || data.stale);
  const running = submitting || (!!current && !stale && data?.run?.status === "running");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void reload(), 3000);
    return () => clearInterval(timer);
  }, [running, reload]);

  const run = useCallback(async () => {
    if (posting.current || blocked || !data?.configured || !current || running || !data.products.length) return;
    posting.current = true;
    const revision = sourceRevision;
    setSubmitting(true);
    setError("");
    try {
      const result = await requestJSON<LabelResponse>("/api/product-labels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourceRevision: revision }),
      });
      // A response for an older import must never replace the visible current state.
      if (latestRevision.current === revision) {
        sequence.current++;
        setData(result);
      }
    } catch (e) {
      if (latestRevision.current === revision)
        setError(e instanceof Error ? e.message : "Labeling failed. Your imported sources are still saved.");
    } finally {
      posting.current = false;
      setSubmitting(false);
      void reload(true);
    }
  }, [blocked, data, current, running, sourceRevision, reload]);

  useEffect(() => {
    if (autoRunRevision === null || autoRunRevision !== sourceRevision ||
      handledImport.current === autoRunRevision || blocked || !current ||
      !data?.configured || loading || running || !data.products.length) return;
    // One automatic attempt per successful import; failures require explicit retry.
    const timer = setTimeout(() => {
      handledImport.current = autoRunRevision;
      void run();
    }, 0);
    return () => clearTimeout(timer);
  }, [autoRunRevision, sourceRevision, blocked, current, data, loading, running, run]);

  const results = new Map(
    current && !stale && !blocked && !running && data?.run?.status !== "failed"
      ? data?.run?.results.map((result) => [result.productId, result]) ?? [] : [],
  );
  const query = search.trim().toLowerCase();
  const products = current ? data?.products ?? [] : [];
  const filtered = products.filter((product) =>
    !query || [product.name, product.reference, ...product.alternateNames].some((value) => value.toLowerCase().includes(query)),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const activePage = Math.min(page, pages - 1);
  const status = !data ? error ? "Product labels are unavailable" : "Loading product labels…"
    : !data.configured ? "External labeling tool is not connected"
    : blocked ? "Waiting for source changes to be saved"
    : stale ? "Labels need updating for this import"
    : running ? "Labeling products…"
    : data.run?.status === "failed" ? "Labeling failed — retry is available"
    : data.run?.status === "partial" ? "Some products still need a label"
    : data.run?.status === "completed" ? "Labels received from the external tool"
    : "Ready to request product labels";

  return (
    <section className="product-label-panel" aria-labelledby="product-label-title">
      <header>
        <div>
          <h2 id="product-label-title"><Tags size={18} aria-hidden /> Product labels</h2>
          <p role="status">{status}</p>
        </div>
        <div className="product-label-actions">
          <button disabled={loading || submitting} onClick={() => void reload()} aria-label="Refresh product labels">
            <RefreshCw size={15} aria-hidden /> Refresh
          </button>
          <button disabled={blocked || !current || !data?.configured || running || !products.length}
            onClick={() => void run()}>
            {data?.run?.status === "failed" || error ? "Retry labeling" : "Run labeling"}
          </button>
          <button aria-expanded={expanded} aria-controls="product-label-products" onClick={() => setExpanded(!expanded)}>
            {expanded ? <ChevronUp size={15} aria-hidden /> : <ChevronDown size={15} aria-hidden />}
            {expanded ? "Hide products" : `Show products${current ? ` (${products.length})` : ""}`}
          </button>
        </div>
      </header>
      <p className="product-label-note">
        Imported product names are prepared for the tool. Current forecast grouping stays unchanged;
        tool labels shown here are not yet applied to forecasts.
      </p>
      {!data?.configured && data && <p className="product-label-note">
        {data.run ? "Saved results can be reviewed below. " : "No tool labels have been generated. "}
        Connect the provider to enable labeling after an import is saved.
      </p>}
      {stale && <p className="product-label-warning">Previous results belong to older source data and are hidden. Run labeling again for the saved import.</p>}
      {blocked && <p className="product-label-warning">Save or resolve the source conflict first. Draft changes are not sent to the labeling tool.</p>}
      {(error || (current && !stale && data?.run?.error)) && <p className="product-label-warning" role="alert">
        {error || data?.run?.error} Your source files remain saved independently of labeling.
      </p>}
      {expanded && (
        <div id="product-label-products">
          <p className="product-label-note">Names from different source systems remain separate until SKU matching is confirmed.</p>
          <label className="product-label-search">Search products
            <input value={search} onChange={(event) => { setSearch(event.target.value); setPage(0); }} placeholder="Name or product code" />
          </label>
          <ul className="product-label-list">
            {filtered.slice(activePage * PAGE_SIZE, (activePage + 1) * PAGE_SIZE).map((product) => {
              const label = results.get(product.id);
              return <li key={product.id}>
                <div>
                  <h3>{product.name}</h3>
                  <p>{identities[product.identity]} · {product.reference}</p>
                  <small>{product.source} · {product.rowCount} source records</small>
                  {!!product.alternateNames.length && <p>Other names: {product.alternateNames.join("; ")}</p>}
                </div>
                <div>
                  <strong className="product-demand-label">{label?.group ? DEMAND_LABELS[label.group] : "Chưa phân loại"}</strong>
                  {label && <>
                    <p>{label.reason}</p>
                    {label.confidence !== undefined && <p>Tool confidence: {Math.round(label.confidence * 100)}%</p>}
                    <small>Source: {data?.run?.provider}</small>
                  </>}
                </div>
              </li>;
            })}
          </ul>
          {!filtered.length && <p className="product-label-note">{products.length ? "No products match this search." : "No named products available in the saved sources."}</p>}
          <footer>
            <span>{filtered.length} products · page {activePage + 1} of {pages}</span>
            <button disabled={!activePage} onClick={() => setPage(activePage - 1)}>Previous</button>
            <button disabled={activePage + 1 >= pages} onClick={() => setPage(activePage + 1)}>Next</button>
          </footer>
        </div>
      )}
    </section>
  );
}
