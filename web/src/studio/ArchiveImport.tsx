import { sourceText } from "./source-text";
import { forwardRef, useImperativeHandle, useRef, useState } from "react";
import { Upload, Sparkles } from "lucide-react";
import { strToU8, zipSync } from "fflate";
import { requestJSON } from "./account";
import { MAX_BYTES, SOURCE_DEFS, sourceKey, type Source } from "./sources";
import type { ImportAnalysis } from "./import-types";

export type ArchiveImportHandle = { importFile: (file: File) => void };
export const ArchiveImport = forwardRef<ArchiveImportHandle, {
  blocked: boolean;
  sources: Source[];
  onLoad: (sources: Source[], replace?: boolean) => Promise<void>;
  onBusyChange?: (busy: boolean) => void;
}>(function ArchiveImport({ blocked, sources, onLoad, onBusyChange }, ref) {
  const input = useRef<HTMLInputElement>(null);
  const reviewing = useRef(false);
  const inFlight = useRef(false);
  const [analysis, setAnalysis] = useState<ImportAnalysis | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [needsSelection, setNeedsSelection] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [replayReady, setReplayReady] = useState(false);
  async function load(result: ImportAnalysis, files: string[]) {
    setBusy("Saving tables…");
    const tables = result.tables.filter(table => files.includes(table.file));
    const unchanged = reviewing.current && tables.length === sources.length && tables.every(table =>
      sources.some(source => sourceKey(source) === sourceKey(table.source) && source.hash === table.source.hash));
    if (!unchanged) await onLoad(tables.map(table => table.source), reviewing.current);
    setNeedsSelection(false);
    const unresolved = tables.reduce((sum, table) => sum + (table.remainingIssues?.length ?? 0), 0);
    const reviewStatus = unresolved ? `${unresolved} issue${unresolved === 1 ? " needs" : "s need"} input` : unchanged && tables.some(table => table.review?.length) ? "Review AI notes" : "No repairs needed";
    setNotice(reviewing.current
      ? unchanged ? `AI checked ${tables.length} tables · ${reviewStatus}` : `AI fixes saved · ${tables.length} tables${unresolved ? ` · ${reviewStatus}` : ""}`
      : `${tables.length} tables loaded · ${tables.reduce((sum, table) => sum + table.rowCount, 0).toLocaleString()} records`);
  }
  async function analyze(file?: File, review = false) {
    if (!file || inFlight.current || blocked) return;
    if (!/\.zip$/i.test(file.name)) { setError("Drop one ZIP file, or use Import CSV / JSON."); return; }
    reviewing.current = review;
    inFlight.current = true; onBusyChange?.(true);
    setBusy(review ? "Checking and fixing data…" : "Extracting ZIP…"); setError(""); setNotice(""); setReplayReady(false);
    if (!review) { setAnalysis(null); setNeedsSelection(false); }
    try {
      if (file.size > MAX_BYTES) throw Error("ZIP exceeds 20 MiB.");
      const result = await requestJSON<ImportAnalysis>(`/api/studio/import${review ? "?review=1" : ""}`, {
        method: "POST", headers: { "Content-Type": "application/zip" }, body: file,
      });
      if (review && result.skipped.length) throw Error("AI review could not read every saved table. Your tables are unchanged.");
      if (review) for (const table of result.tables) {
        const original = sources.find(source => source.name === table.file);
        if (original?.replay) table.source.replay = original.replay;
        if (original && table.source.kind === "general") table.source.name = original.name;
      }
      setAnalysis(result);
      if (!result.tables.length) throw Error("No CSV or JSON tables found in this ZIP.");
      const unique = result.tables.filter(table => result.tables.filter(t => sourceKey(t.source) === sourceKey(table.source)).length === 1).map(table => table.file);
      setSelected(unique);
      if (unique.length !== result.tables.length) { setNeedsSelection(true); setNotice("Choose one file per business table."); }
      else {
        await load(result, unique);
        if (!review && result.tables.some(table => table.source.replay)) {
          const backend = await requestJSON<{ configured: boolean }>("/api/data-backend/status");
          if (backend.configured) {
            await requestJSON("/api/v1/live/replay-archive", { method: "POST", headers: { "Content-Type": "application/zip" }, body: file });
            setReplayReady(true);
          }
        }
      }
    } catch (e) {
      if (!review && e instanceof Error && "code" in e && e.code === "REPLAY_ARCHIVE") {
        setBusy("Đang lưu ZIP làm nguồn replay…");
        try {
          const replay = await requestJSON<{ day: string; adsLagHours: number }>("/api/v1/live/replay-archive", {
            method: "POST", headers: { "Content-Type": "application/zip" }, body: file,
          });
          setReplayReady(true);
          setNotice(`Đã lưu ZIP replay ngày ${replay.day} · Ads trễ ${replay.adsLagHours} giờ. Các CSV cả ngày chưa được đưa vào dữ liệu phân tích.`);
        } catch (uploadError) { setError(uploadError instanceof Error ? uploadError.message : "Không lưu được ZIP replay."); }
      } else setError(e instanceof Error ? e.message : "Import failed. Retry the ZIP.");
    }
    finally { inFlight.current = false; setBusy(""); onBusyChange?.(false); if (input.current) input.current.value = ""; }
  }
  useImperativeHandle(ref, () => ({ importFile: file => { void analyze(file); } }));
  return <section className="archive-import" aria-label="Archive import">
    <header>
      <div><h2>Drop your ZIP here</h2><span>CSV / JSON hoặc ZIP replay live_11_11</span></div>
      <div className="archive-actions">
        {!!sources.length && <button disabled={blocked || !!busy} onClick={() => {
          if (new Set(sources.map(source => source.name)).size !== sources.length) { setError("Resolve duplicate filenames before AI repair."); return; }
          const bytes = zipSync(Object.fromEntries(sources.map(source => [source.name, strToU8(sourceText(source))])), { level: 1 });
          void analyze(new File([new Uint8Array(bytes)], "workspace.zip", { type: "application/zip" }), true);
        }}><Sparkles size={16} aria-hidden /> Review &amp; fix with AI</button>}
        <button className="primary" disabled={blocked || !!busy} onClick={() => input.current?.click()}><Upload size={16} aria-hidden /> Import ZIP</button>
      </div>
    </header>
    <input ref={input} type="file" accept=".zip" hidden aria-label="Upload ZIP archive" onChange={e => void analyze(e.target.files?.[0])} />
    {(busy || notice) && <p role="status">{busy || notice}</p>}
    {replayReady && <p><a href="/campaign-hq#replay">Mở CD002 để chạy replay →</a></p>}
    {error && <p role="alert">{error}{error.includes("replay") && <> <a href="/campaign-hq#replay">Mở Replay demo CD002 →</a></>}</p>}
    {analysis && <details open={needsSelection || reviewing.current || undefined}>
      <summary>Import details</summary>
      {analysis.tables.map(table => <div className="archive-table" key={table.file}>
        {needsSelection && <input type="checkbox" aria-label={`Load ${table.file}`} checked={selected.includes(table.file)} disabled={blocked || !!busy}
          onChange={e => setSelected(current => e.target.checked
            ? [...current.filter(file => !analysis.tables.some(t => t.file === file && sourceKey(t.source) === sourceKey(table.source))), table.file]
            : current.filter(file => file !== table.file))} />}
        <strong>{SOURCE_DEFS.find(d => d.kind === table.source.kind)?.name ?? table.source.name}</strong> · {table.rowCount.toLocaleString()} records
        {table.method === "model" && <p>{table.columns.filter(c => c.source !== c.target).map(c => `${c.source} → ${c.target}`).join(" · ")}</p>}
        {!!table.repairs?.length && <p>{table.repairs.join(" ")}</p>}
        {!!table.remainingIssues?.length && <p>Needs review: {table.remainingIssues.join(" ")}</p>}
        {!!table.review?.length && <p>AI notes: {table.review.join(" · ")}</p>}
      </div>)}
      {analysis.skipped.map(item => <p key={item.file}>{item.file}: {item.reason}</p>)}
      {needsSelection && <button disabled={blocked || !!busy || !selected.length} onClick={async () => {
        if (inFlight.current) return;
        inFlight.current = true; onBusyChange?.(true); setError("");
        try { await load(analysis, selected); }
        catch (e) { setError(e instanceof Error ? e.message : "Could not save tables."); }
        finally { inFlight.current = false; setBusy(""); onBusyChange?.(false); }
      }}>Load selected tables</button>}
    </details>}
  </section>;
});
