import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { requestJSON } from "./account";
import { MAX_BYTES, SOURCE_DEFS, inspect, sourceKey, type Source } from "./sources";
import type { ImportAnalysis } from "./import-types";

export function ArchiveImport({ blocked, onLoad }: { blocked: boolean; onLoad: (sources: Source[]) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [analysis, setAnalysis] = useState<ImportAnalysis | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function analyze(file?: File) {
    if (!file || busy) return;
    setBusy(true); setError(""); setAnalysis(null);
    try {
      if (file.size > MAX_BYTES) throw Error("ZIP exceeds 20 MiB.");
      const result = await requestJSON<ImportAnalysis>("/api/studio/import", {
        method: "POST", headers: { "Content-Type": "application/zip" }, body: file,
      });
      setAnalysis(result);
      setSelected(result.tables.filter(table => result.tables.filter(t => sourceKey(t.source) === sourceKey(table.source)).length === 1).map(t => t.file));
    } catch (e) { setError(e instanceof Error ? e.message : "Archive analysis failed."); }
    finally { setBusy(false); if (input.current) input.current.value = ""; }
  }
  return <section className="archive-import" aria-label="Archive import">
    <header>
      <h2>Import tables</h2>
      <button className="primary" disabled={blocked || busy} onClick={() => input.current?.click()}>
        <Upload size={16} aria-hidden /> {busy ? "Analyzing ZIP…" : "Import ZIP"}
      </button>
    </header>
    <input ref={input} type="file" accept=".zip" hidden aria-label="Upload ZIP archive" onChange={e => void analyze(e.target.files?.[0])} />
    {busy && <p role="status">Extracting files and resolving table columns…</p>}
    {error && <p role="alert">{error}</p>}
    {analysis && <>
      {!analysis.tables.length && <p role="status">No supported tables found.</p>}
      {analysis.tables.map(table => {
        const parsed = inspect(table.source.name, table.source.text);
        return <div className="archive-table" key={table.file}>
          <label><input type="checkbox" checked={selected.includes(table.file)} disabled={blocked}
            onChange={e => setSelected(current => e.target.checked
              ? [...current.filter(file => !analysis.tables.some(t => t.file === file && sourceKey(t.source) === sourceKey(table.source))), table.file]
              : current.filter(file => file !== table.file))} />
            <strong>{SOURCE_DEFS.find(d => d.kind === table.source.kind)?.name ?? table.source.name}</strong>
            <span>{table.file} · {table.rowCount.toLocaleString()} rows</span>
          </label>
          <details><summary>Preview {table.method === "model" ? "model-mapped" : table.method} table</summary>
            <p>{table.columns.map(c => c.source === c.target ? c.target : `${c.source} → ${c.target}`).join(" · ")}</p>
            <div className="archive-preview" role="region" aria-label={`Preview ${table.file}`} tabIndex={0}><table><thead><tr>{parsed.columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>{parsed.rows.slice(0, 5).map((row, i) => <tr key={i}>{parsed.columns.map(c => <td key={c}>{String(row[c] ?? "")}</td>)}</tr>)}</tbody></table></div>
          </details>
        </div>;
      })}
      {!!analysis.skipped.length && <details><summary>{analysis.skipped.length} skipped files</summary><ul>
        {analysis.skipped.map(item => <li key={item.file}>{item.file}: {item.reason}</li>)}
      </ul></details>}
      {!!analysis.tables.length && <button className="primary" disabled={blocked || !selected.length} onClick={() => {
        try {
          onLoad(analysis.tables.filter(t => selected.includes(t.file)).map(t => t.source));
          setAnalysis(null); setError("");
        } catch (e) { setError(e instanceof Error ? e.message : "Could not load tables."); }
      }}>Load {selected.length} {selected.length === 1 ? "table" : "tables"} to workspace</button>}
    </>}
  </section>;
}
