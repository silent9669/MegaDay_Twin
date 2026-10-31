import Papa from "papaparse";
import { inspect, makeSource, normalize, piiColumn, DEMAND_COLUMN, type Source } from "../src/studio/sources";
import { previewVnd } from "../src/studio/money-preview";

type Cell = string | number | boolean | null;
export type ModelCorrection = { row: number; column: string; before: Cell; after: Cell; reason: string };

/** Apply explicit cell patches, never model-generated replacement tables. */
export async function applyModelCorrections(source: Source, corrections: ModelCorrection[]) {
  if (!corrections.length) return { source, repairs: [] as string[] };
  const parsed = inspect(source.name, source.text);
  const rows = parsed.rows.map(row => ({ ...row }));
  const seen = new Set<string>();
  const repairs: string[] = [];
  for (const edit of corrections) {
    const key = JSON.stringify([edit.row, edit.column]);
    if (!Number.isInteger(edit.row) || edit.row < 0 || !rows[edit.row] || !parsed.columns.includes(edit.column) || seen.has(key))
      throw Error("AI correction targets an invalid or duplicate cell. No changes saved.");
    seen.add(key);
    const current = rows[edit.row]![edit.column];
    if (current !== null && typeof current === "object") throw Error("AI corrections must target scalar cells.");
    if (current !== edit.before) throw Error("AI correction does not match the original cell. No changes saved.");
    if (edit.after == null || typeof edit.after !== typeof current || (typeof edit.after === "string" && !edit.after.trim()))
      throw Error("AI correction cannot clear a cell or change its value type.");
    const column = normalize(edit.column);
    if (edit.column === DEMAND_COLUMN || piiColumn(edit.column) || /(^ma |(^|_)id($|_)|sku|code|thoi gian|thoi diem|ngay|date|time)/.test(column))
      throw Error("AI cannot change identity, demand labels or source clock fields.");
    if (current == null || (typeof current === "string" && !current.trim()))
      throw Error("AI cannot fill a missing business fact without source evidence.");
    // Numeric corrections may normalize representation, never invent quantities or amounts.
    if (/ton|so luong|luot|gia|phi|tien|price|cost|amount|quantity|stock|units|click|spend/.test(column) || typeof current === "number") {
      const before = previewVnd(String(current ?? "")), after = previewVnd(String(edit.after ?? ""));
      if (before === null || before !== after) throw Error("AI correction would introduce new data errors or change a business amount.");
    }
    if (edit.after === current) continue;
    rows[edit.row]![edit.column] = edit.after;
    repairs.push(`AI corrected row ${edit.row + 1}, ${edit.column}: ${edit.reason}`);
  }
  if (!repairs.length) return { source, repairs };
  const text = parsed.payload === undefined
    ? Papa.unparse({ fields: parsed.columns, data: rows })
    : JSON.stringify(Array.isArray(parsed.payload) ? rows : rows[0]);
  const fixed = await makeSource(source.name, text, source.origin);
  const originalErrors = new Set(parsed.issues.filter(issue => issue.level === "error").map(issue => JSON.stringify(issue)));
  if (inspect(fixed.name, fixed.text).issues.some(issue => issue.level === "error" && !originalErrors.has(JSON.stringify(issue))))
    throw Error("AI correction introduced new data errors. No changes saved.");
  return { source: { ...source, text: fixed.text, hash: fixed.hash }, repairs };
}
