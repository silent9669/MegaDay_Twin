import { readSource } from "./read-source";
import { sourceText, MAX_SOURCE_BYTES } from "./source-text";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  Download,
  FileJson,
  FileSpreadsheet,
  FolderOpen,
  Trash2,
  Save,
  Search,
  Upload,
  X,
  AlertTriangle,
  Database,
} from "lucide-react";
import Papa from "papaparse";
import { useSearchParams } from "react-router";
import { useAccountDocument, requestJSON } from "./account";
import {
  SOURCE_DEFS,
  makeSource,
  validatePack,
  sourceKey, mergeSources, DEMAND_COLUMN,
  type Source,
  type SourcePack,
} from "./sources";
import { mergeReplaySources } from "./replay";
import { SandboxControl } from "./SandboxControl";
import { SourceDiagnostics } from "./SourceDiagnostics";
import { ArchiveImport, type ArchiveImportHandle } from "./ArchiveImport";
import "./studio.css";
function download(name: string, text: string) {
  const url = URL.createObjectURL(
    new Blob([text], {
      type: name.endsWith(".csv")
        ? "text/csv;charset=utf-8"
        : "application/json",
    }),
  );
  const link = window.document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function DataStudio() {
  const cloud = useAccountDocument<SourcePack>("sources");
  const saveCloud = cloud.save;
  const [draft, setDraft] = useState<SourcePack | null>(null),
    [base, setBase] = useState(0),
    [dirty, setDirty] = useState(false),
    [saving, setSaving] = useState(false),
    [busy, setBusy] = useState(false),
    [importingShop, setImportingShop] = useState(false),
    [message, setMessage] = useState(""),
    [saveError, setSaveError] = useState("");
  const [sourceParams, setSourceParams] = useSearchParams();
  const selected = sourceParams.get('source') ?? 'orders';
  const setSelected = (kind: string) => setSourceParams(previous => { const next = new URLSearchParams(previous); next.set('source', kind); return next; });
  const [tab, setTab] = useState<"records" | "diagnostics" | "schema">("records"),
    [query, setQuery] = useState(""),
    [page, setPage] = useState(0),
    [focusRow, setFocusRow] = useState<number | null>(null),
    [cell, setCell] = useState<{
      row: number;
      column: string;
      value: string;
    } | null>(null);
  const files = useMemo(
    () => draft?.files ?? cloud.document?.data.files ?? [],
    [draft, cloud.document],
  );
  const revision = draft ? base : (cloud.document?.revision ?? 0);
  const conflict =
    dirty && !!cloud.document && cloud.document.revision > revision;
  const archive = useRef<ArchiveImportHandle>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null),
    edit = useRef<HTMLDialogElement>(null);
  const latest = useRef({ draft, base, dirty });
  useEffect(() => {
    latest.current = { draft, base, dirty };
  }, [draft, base, dirty]);
  const current = files.find((f) => sourceKey(f) === selected) ?? files[0];
  const parsed = useMemo(
    () => files.map((f) => ({ source: f, parsed: readSource(f) })),
    [files],
  );
  const currentParsed = parsed.find(
    (f) => current && sourceKey(f.source) === sourceKey(current),
  )?.parsed;
  const logs = parsed.flatMap(({ source, parsed }) =>
    parsed.issues.map((i) => ({ ...i, kind: source.kind, file: source.name })),
  );
  const filtered =
    currentParsed?.rows
      .map((row, index) => ({ row, index }))
      .filter(
        ({ row }) =>
          !query ||
          JSON.stringify(row).toLowerCase().includes(query.toLowerCase()),
      ) ?? [];
  const pages = Math.max(1, Math.ceil(filtered.length / 30));
  const sourceErrors = logs.filter((i) => i.level === "error").length;
  const sourceWarnings = logs.filter((i) => i.level === "warning").length;
  const demoCount = files.filter((f) => f.commit).length;
  useEffect(() => {
    if (tab === "records" && focusRow !== null && current) {
      window.document.getElementById(`source-record-${current.kind}-${focusRow}`)?.focus();
    }
  }, [tab, focusRow, current, page]);
  const change = (next: SourcePack) => {
    if (!dirty) setBase(cloud.document?.revision ?? revision);
    setDraft(next);
    setDirty(true);
    setSaveError("");
  };
  const save = useCallback(async () => {
    const pending = latest.current;
    if (!pending.draft || !pending.dirty || saving || conflict) return;
    setSaving(true);
    try {
      const saved = await saveCloud(pending.draft, pending.base);
      setBase(saved.revision);
      if (latest.current.draft === pending.draft) {
        setDirty(false);
        setDraft(null);
      }
      setSaveError("");
    } catch (e) {
      setSaveError(
        e instanceof Error ? e.message : "Save failed. Export your draft.",
      );
    } finally {
      setSaving(false);
    }
  }, [saveCloud, saving, conflict]);
  useEffect(() => {
    if (!dirty || saving || conflict || saveError) return;
    const timer = setTimeout(() => void save(), 1200);
    return () => clearTimeout(timer);
  }, [draft, dirty, saving, conflict, saveError, save]);
  useEffect(() => {
    const prevent = (event: BeforeUnloadEvent) => {
      if (latest.current.dirty) event.preventDefault();
    };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, []);
  useEffect(() => {
    if (cell) edit.current?.showModal();
  }, [cell]);
  const importShop = async () => {
    if (busy || dirty || saving || conflict || !cloud.document) return;
    setBusy(true); setImportingShop(true); setMessage(""); setSaveError("");
    try {
      const pack = await validatePack(await requestJSON<SourcePack>("/api/studio/shop"));
      await saveCloud({ schema: "hackathon.sources.v1", files: mergeSources(files, pack.files) }, revision);
      setDraft(null); setDirty(false);
      setSelected(sourceKey(pack.files[0]!));
      setPage(0); setQuery(""); setFocusRow(null); setTab("records");
      setMessage(`${pack.files.length} shop tables loaded.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load shop data.");
    } finally { setBusy(false); setImportingShop(false); }
  };
  const importFiles = async (list: FileList | null) => {
    if (!list?.length || busy) return;
    setBusy(true);
    setMessage("");
    try {
      const incoming = Array.from(list);
      if (incoming.length > 24)
        throw Error(`Selected ${incoming.length} files; choose at most 24 tables. Nothing was imported; existing sources are unchanged.`);
      const bytes = incoming.reduce((sum, f) => sum + f.size, 0);
      if (bytes > MAX_SOURCE_BYTES)
        throw Error(`Selected files total ${(bytes / 1024 / 1024).toFixed(1)} MiB; the limit is 48 MiB. Nothing was imported; existing sources are unchanged.`);
      const imported: Source[] = [];
      if (incoming.length === 1 && incoming[0]!.name.endsWith(".json")) {
        const text = await incoming[0]!.text();
        let value: unknown;
        try {
          value = JSON.parse(text);
        } catch {
          /* Source inspector reports syntax. */
        }
        if (value && typeof value === "object" && "schema" in value)
          imported.push(...(await validatePack(value)).files);
      }
      if (!imported.length)
        for (const file of incoming) {
          if (!/\.(csv|json)$/i.test(file.name))
            throw Error("Upload CSV or JSON files.");
          imported.push(
            await makeSource(
              file.name,
              await file.text(),
              file.webkitRelativePath || file.name,
            ),
          );
        }
      if (new Set(imported.map(sourceKey)).size !== imported.length)
        throw Error(
          "Duplicate source type in this upload. Choose one file per source.",
        );
      change({
        schema: "hackathon.sources.v1",
        files: mergeSources(files, imported),
      });
      setSelected(sourceKey(imported[0]!));
      setPage(0);
      setQuery("");
      setFocusRow(null);
      setTab("records");
      setMessage(
        `${imported.length} table${imported.length === 1 ? "" : "s"} imported.`,
      );
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Could not read files.");
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };
  const deleteTables = async (all = false) => {
    if (dirty || saving || busy || conflict || !cloud.document || (!all && !current)) return;
    if (!window.confirm(all ? "Delete all tables from this workspace?" : `Delete ${current!.name}?`)) return;
    setBusy(true); setSaveError("");
    try {
      const next = all ? [] : files.filter(source => sourceKey(source) !== sourceKey(current!));
      await saveCloud({ schema: "hackathon.sources.v1", files: next }, cloud.document.revision);
      setDraft(null); setDirty(false); setSelected(next[0] ? sourceKey(next[0]) : "");
      setPage(0); setQuery(""); setCell(null); setMessage("");
    } catch (error) { setSaveError(error instanceof Error ? error.message : "Could not delete tables."); }
    finally { setBusy(false); }
  };
  const exportPack = () => {
    if (files.length)
      download(
        "hackathon.sources.json",
        JSON.stringify({ schema: "hackathon.sources.v1", files }, null, 2),
      );
  };
  const importBackend = async () => {
    setBusy(true);
    try {
      const result = await requestJSON<unknown>("/api/data-backend/import", {
        method: "POST",
      });
      download("backend-import-report.json", JSON.stringify(result, null, 2));
      setMessage("Backend import finished. Downloaded the ingestion report.");
    } catch (e) {
      setMessage(
        e instanceof Error ? e.message : "Backend import unavailable.",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={`data-studio${dragging ? " data-studio-dragging" : ""}`}
      onDragOver={event => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; setDragging(true); } }}
      onDragLeave={event => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDragging(false); }}
      onDrop={event => { event.preventDefault(); setDragging(false); const files = event.dataTransfer.files;
        if (files.length !== 1) { setMessage("Drop one ZIP file at a time."); return; }
        archive.current?.importFile(files[0]!);
      }}>
      <header className="studio-header">
        <div>
          <span className="eyebrow">Workspace / Data</span>
          <h1>
            Data Studio<span className="source-count">{files.length} tables</span>
          </h1>
        </div>
        <div className="studio-actions">
          <span
            className={`sync-state ${dirty || cloud.error || saveError ? "sync-pending" : ""}`}
            role="status"
          >
            {saving ? (
              "Saving…"
            ) : conflict ? (
              "Conflict"
            ) : saveError ? (
              "Not saved"
            ) : dirty ? (
              "Unsaved"
            ) : cloud.document ? (
              <>
                <Check size={14} /> Saved to account
              </>
            ) : (
              "Loading sources…"
            )}
          </span>
          <button onClick={() => void importShop()} disabled={busy || dirty || saving || conflict || !cloud.document}>
            <FolderOpen size={16} />
            {importingShop ? "Importing shop…" : "Import shop folder"}
          </button>
          <button onClick={exportPack} disabled={busy || !files.length}>
            <Download size={16} />
            Export pack
          </button>
          <button
            onClick={() => input.current?.click()}
            disabled={busy || saving || !cloud.document}
          >
            <Upload size={16} />
            Import CSV / JSON
          </button>
        </div>
      </header>
      {demoCount > 0 && (
        <p className="studio-source-summary">
          {demoCount === files.length
            ? `All ${files.length} sources are repository demo data.`
            : `${demoCount} demo sources · ${files.length - demoCount} uploaded or edited sources.`}
        </p>
      )}
      <input
        type="file"
        ref={input}
        accept=".csv,.json"
        multiple
        hidden
        aria-label="Import source files"
        onChange={(e) => void importFiles(e.target.files)}
      />
      {(message || cloud.error || saveError) && (
        <div className="studio-notice" role="status">
          <AlertTriangle size={17} />
          <span>{cloud.error || saveError || message}</span>
          <button
            className="studio-icon"
            aria-label="Dismiss message"
            onClick={() => {
              setMessage("");
              setSaveError("");
            }}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {conflict && (
        <div className="studio-conflict" role="alert">
          <strong>Another device updated these sources.</strong>
          <span>Export your draft before loading the account version.</span>
          <button onClick={exportPack}>Export draft</button>
          <button
            onClick={() => {
              setDraft(null);
              setDirty(false);
              setSaveError("");
            }}
          >
            Load account version
          </button>
        </div>
      )}
      <ArchiveImport sources={files} ref={archive} onBusyChange={setBusy} blocked={dirty || saving || conflict || busy || !cloud.document || !!saveError}
        onLoad={async (incoming, replace) => {
          setBusy(true);
          try {
            await saveCloud({ schema: "hackathon.sources.v1", files: replace ? incoming : await mergeReplaySources(files, incoming) }, revision);
            setDraft(null); setDirty(false);
            setSelected(sourceKey(incoming[0]!));
            setPage(0); setQuery(""); setFocusRow(null); setTab("records"); setMessage("");
          } finally { setBusy(false); }
        }} />
      {(demoCount > 0 || files.some(file => file.replay)) && <SandboxControl />}
      {!files.length && cloud.document && <p className="source-empty">No tables yet.</p>}
      {!!files.length && <>
      <section className="studio-body" aria-label="Source files">
        <aside className="source-list">
          <div className="source-list-heading">
            <strong>Source files</strong>
            <span>{files.length}</span>
          </div>
          {files.map((source) => {
            const def = { kind: source.kind, file: source.name, name: SOURCE_DEFS.find(d => d.kind === source.kind)?.name ?? source.name };
            const entry = parsed.find((f) => sourceKey(f.source) === sourceKey(source)),
              count =
                entry?.parsed.issues.filter((i) => i.level !== "info").length ??
                0;
            return (
              <button
                key={sourceKey(source)}
                className={current && sourceKey(current) === sourceKey(source) ? "selected" : ""}
                aria-pressed={current && sourceKey(current) === sourceKey(source)}
                onClick={() => {
                  setSelected(sourceKey(source));
                  setPage(0);
                  setQuery("");
                  setFocusRow(null);
                }}
              >
                <span className={`source-file-icon source-${def.kind}`}>
                  {def.file.endsWith(".csv") ? (
                    <FileSpreadsheet size={20} />
                  ) : (
                    <FileJson size={20} />
                  )}
                </span>
                <span>
                  <strong>{def.name}</strong>
                  <small>
                    {entry
                      ? `${entry.parsed.rows.length.toLocaleString()} records`
                      : "Loading…"}
                  </small>
                  {entry && <small className="source-origin-label">{entry.source.commit ? "Demo data" : "Uploaded / edited"}</small>}
                </span>
                {count > 0 && <em aria-label={`${count} issues`}>{count}</em>}
              </button>
            );
          })}
          <div className="source-list-tools">
            <button onClick={() => void deleteTables(true)} disabled={busy || dirty || saving || conflict}>
              <Trash2 size={16} /> Delete all data
            </button>
            <button
              onClick={() => void save()}
              disabled={!dirty || saving || conflict}
            >
              <Save size={16} />
              Save now
            </button>
          </div>
        </aside>
        <div className="source-detail">
          <header className="source-detail-heading">
            <div>
              <h2>
                {SOURCE_DEFS.find((d) => d.kind === current?.kind)?.name ??
                  current?.name ?? "Tables"}
              </h2>
              <code>{current?.name}</code>
            </div>
            <button disabled={busy || dirty || saving || conflict || !current} onClick={() => void deleteTables()}>
              <Trash2 size={15} /> Delete table
            </button>
            <button
              disabled={!current}
              onClick={() => current && download(current.name, sourceText(current))}
            >
              <Download size={15} />
              Export file
            </button>
          </header>
          {current && (
            <div className="source-provenance">
              <span title={current.origin}>
                {current.commit
                  ? `Demo data · GitHub ${current.commit.slice(0, 7)}`
                  : current.origin === "edited in workspace" ? "Edited in workspace" : "Uploaded source"}
              </span>
              <code title={current.hash}>
                SHA-256 {current.hash.slice(0, 12)}
              </code>
              <span>Notes are untrusted</span>
            </div>
          )}
          <div className="source-toolbar">
            <div
              className="studio-tabs"
              role="tablist"
              aria-label="Source views"
            >
              {(["records", "diagnostics", "schema"] as const).map((t) => (
                <button
                  key={t}
                  role="tab"
                  aria-selected={tab === t}
                  aria-controls="source-tab-panel"
                  onClick={() => {
                    setTab(t);
                    setQuery("");
                  }}
                >
                  {t === "schema" ? "Format" : t[0]!.toUpperCase() + t.slice(1)}
                  {t === "diagnostics" && (
                    <span>{currentParsed?.issues.length ?? 0}</span>
                  )}
                </button>
              ))}
            </div>
            <label className="source-search">
              <Search size={16} />
              <input
                placeholder={
                  tab === "diagnostics"
                    ? "Search diagnostics"
                    : "Search records"
                }
                aria-label="Search source"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setPage(0);
                }}
              />
            </label>
          </div>
          <div
            id="source-tab-panel"
            className="source-content"
            data-view={tab}
            role="tabpanel"
            aria-label={tab}
          >
            {!currentParsed ? (
              <p className="source-empty">Loading your account sources…</p>
            ) : tab === "records" ? (
              <>
                <div className="source-table-wrap">
                  <table className="source-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        {currentParsed.columns.map((c) => (
                          <th key={c}>{c}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {filtered
                        .slice(
                          Math.min(page, pages - 1) * 30,
                          (Math.min(page, pages - 1) + 1) * 30,
                        )
                        .map(({ row, index }) => (
                          <tr
                            key={index}
                            id={`source-record-${current?.kind}-${index}`}
                            tabIndex={-1}
                            className={focusRow === index ? "source-record-selected" : undefined}
                          >
                            <td>{index + 1}</td>
                            {currentParsed.columns.map((column) => (
                              <td key={column}>
                                {typeof row[column] === "object" ? (
                                  <code>{JSON.stringify(row[column])}</code>
                                ) : (
                                  <button
                                    className="source-cell"
                                    disabled={busy || (current?.kind === "inventory" && column === DEMAND_COLUMN)}
                                    title={`Edit row ${index + 1}, ${column}`}
                                    onClick={() =>
                                      setCell({
                                        row: index,
                                        column,
                                        value: String(row[column] ?? ""),
                                      })
                                    }
                                  >
                                    {String(row[column] ?? "") || (
                                      <span className="empty-cell">—</span>
                                    )}
                                  </button>
                                )}
                              </td>
                            ))}
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
                {!filtered.length && (
                  <p className="source-empty">No matching records.</p>
                )}
                <footer className="source-pagination">
                  <span>{filtered.length.toLocaleString()} records</span>
                  <button
                    disabled={page === 0}
                    onClick={() => setPage(page - 1)}
                  >
                    Previous
                  </button>
                  <span>
                    {Math.min(page + 1, pages)} / {pages}
                  </span>
                  <button
                    disabled={page >= pages - 1}
                    onClick={() => setPage(page + 1)}
                  >
                    Next
                  </button>
                </footer>
              </>
            ) : tab === "diagnostics" ? (
              <SourceDiagnostics
                key={current?.kind}
                issues={currentParsed.issues}
                query={query}
                format={currentParsed.payload === undefined ? "csv" : "json"}
                rowCount={currentParsed.rows.length}
                repositorySample={!!current?.commit}
                onViewRecord={(index) => {
                  setQuery("");
                  setPage(Math.floor(index / 30));
                  setFocusRow(index);
                  setTab("records");
                }}
              />
            ) : (
              <div className="source-schema">
                <h3>Expected format</h3>
                <dl>
                  {SOURCE_DEFS.find(
                    (d) => d.kind === current?.kind,
                  )?.required.map((c) => (
                    <div key={c}>
                      <dt>{c}</dt>
                      <dd>Required</dd>
                    </div>
                  ))}
                </dl>
                <p>
                  {current?.kind === "inventory"
                    ? "Supports daily “Ngày” and hourly “Thời điểm cập nhật” snapshots. Internal codes require backend SKU matching."
                    : current?.kind === "ads"
                      ? "Timestamps are preserved as written. Backend verifies timezone before combining ads and orders."
                      : current?.kind === "cost"
                        ? "Blank cost stays unknown. VND variants such as 45k and 45.000đ are normalized by the backend."
                        : "Headers detect source type. Uploaded filenames may differ. Originals remain unchanged."}
                </p>
              </div>
            )}
          </div>
          <footer className="source-footer">
            <span>
              <Database size={14} />
              {sourceErrors} errors · {sourceWarnings} warnings across sources
            </span>
            <button
              disabled={busy || dirty || saving || conflict || !files.length}
              onClick={() => void importBackend()}
            >
              Send saved sources to backend
            </button>
          </footer>
        </div>
      </section>
      </>}
      {cell && (
        <dialog
          ref={edit}
          className="source-edit"
          onCancel={() => setCell(null)}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (!current || !currentParsed) return;
              const rows = currentParsed.rows.map((r, i) =>
                i === cell.row ? { ...r, [cell.column]: cell.value } : r,
              );
              const text =
                currentParsed.payload === undefined
                  ? Papa.unparse({ fields: currentParsed.columns, data: rows })
                  : JSON.stringify(
                      Array.isArray(currentParsed.payload) ? rows : rows[0],
                    );
              void makeSource(current.name, text, "edited in workspace").then(
                (source) => {
                  change({
                    schema: "hackathon.sources.v1",
                    files: files.map((f) =>
                      sourceKey(f) === sourceKey(current) ? { ...source, replay: current.replay } : f,
                    ),
                  });
                  setCell(null);
                },
              );
            }}
          >
            <header>
              <h2>Edit value</h2>
              <button
                type="button"
                aria-label="Cancel edit"
                onClick={() => setCell(null)}
              >
                <X size={18} />
              </button>
            </header>
            <label>
              {cell.column}
              <textarea
                autoFocus
                value={cell.value}
                onChange={(e) => setCell({ ...cell, value: e.target.value })}
              />
            </label>
            <footer>
              <button type="button" onClick={() => setCell(null)}>
                Cancel
              </button>
              <button className="primary" type="submit">
                Apply edit
              </button>
            </footer>
          </form>
        </dialog>
      )}
    </div>
  );
}
