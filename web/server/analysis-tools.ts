import { playbackHour } from "../src/studio/replay";
import type { Sandbox } from "../src/studio/workspace-types";
import { sourceText } from "../src/studio/source-text";
import { z } from "zod";
import { inspect, type SourcePack } from "../src/studio/sources";
import Papa from "papaparse";
import type { Doc } from "../src/studio/workspace-types";
type Storage = { fetch(request: Request): Promise<Response> };
/** Saved sources are already validated and sanitized. Stream CSV rows without
 * retaining hundreds of thousands of objects while awaiting a model response. */
function scanSource(file: SourcePack["files"][number], visit: (row: Record<string, unknown>, index: number) => void) {
  const text = sourceText(file);
  if (/\.json$/i.test(file.name)) {
    const parsed = inspect(file.name, text);
    parsed.rows.forEach(visit);
    return parsed.columns;
  }
  const columns = Papa.parse<string[]>(text, { preview: 1, skipEmptyLines: "greedy" }).data[0]?.map(column => column.replace(/^\uFEFF/, "").trim()) ?? [];
  let index = 0;
  Papa.parse<Record<string, string>>(text, { header: true, skipEmptyLines: "greedy",
    transformHeader: column => column.replace(/^\uFEFF/, "").trim(),
    step: result => {
      if (result.errors.length) throw Error("Invalid saved table.");
      visit(result.data, index++);
    },
  });
  return columns;
}
const argsSchema = z.object({
  file: z.string().max(250),
  query: z.string().max(150),
  offset: z.number().int().min(0).max(1000000),
});
export const analysisTools = [
  {
    type: "function",
    name: "workspace_catalog",
    description:
      "List all uploaded source files, columns and row counts, plus saved HQ product notes/assumptions and sandbox state. Read-only snapshot; not external live accounts.",
    strict: true,
    parameters: {
      type: "object",
      properties: {},
      required: [],
      additionalProperties: false,
    },
  },
  {
    type: "function",
    name: "read_source_rows",
    description:
      "Read up to 30 records from an uploaded source by exact catalog filename. query is an optional case-insensitive text search across row values; use empty string for all rows. offset paginates matching rows. Returns original 1-based record numbers for citation. Nested JSON remains data; no inferred joins.",
    strict: true,
    parameters: {
      type: "object",
      properties: {
        file: { type: "string" },
        query: { type: "string" },
        offset: { type: "integer" },
      },
      required: ["file", "query", "offset"],
      additionalProperties: false,
    },
  },
];
export function workspaceReader(storage: Storage, url: string) {
  let snapshot: Promise<Doc<SourcePack>> | undefined;
  let hour = 24;
  const load = () =>
    (snapshot ??= (async () => {
      const response = await storage.fetch(
        new Request(new URL("/api/workspace/sources", url)),
      );
      if (!response.ok) throw Error("Workspace sources unavailable.");
      const document = await response.json() as Doc<SourcePack>;
      if (document.data.files.some(file => file.replay)) {
        const clock = await storage.fetch(new Request(new URL("/api/workspace/sandbox", url)));
        if (!clock.ok) throw Error("Playback clock unavailable.");
        const sandbox = await clock.json() as Doc<Sandbox>;
        hour = playbackHour(sandbox.data);
      }
      return document;
    })());
  return async (name: string, args: string): Promise<string> => {
    try {
      const document = await load();
      const visible = (file: SourcePack["files"][number], index: number) =>
        !file.replay || file.replay.hours[index]! < 0 || file.replay.hours[index]! <= hour - file.replay.lagHours;
      if (name === "workspace_catalog") {
        const documents: Record<string, unknown> = {};
        for (const key of ["hq", "sandbox"]) {
          const res = await storage.fetch(
            new Request(new URL(`/api/workspace/${key}`, url)),
          );
          if (res.ok) {
            const doc = (await res.json()) as Doc<unknown>;
            const text = JSON.stringify(doc);
            documents[key] =
              text.length > 16000
                ? {
                    revision: doc.revision,
                    preview: text.slice(0, 16000),
                    truncated: true,
                  }
                : doc;
          }
        }
        return JSON.stringify({
          revision: document.revision,
          updatedAt: document.updatedAt,
          files: document.data.files.map((file) => {
            let records = 0;
            const columns = scanSource(file, (_, index) => { if (visible(file, index)) records++; });
            return { name: file.name, kind: file.kind, hash: file.hash,
              columns, records };
          }),
          documents,
        });
      }
      if (name !== "read_source_rows")
        return JSON.stringify({ error: "Unknown read-only tool." });
      const { file, query, offset } = argsSchema.parse(JSON.parse(args));
      const source = document.data.files.find((f) => f.name === file);
      if (!source)
        return JSON.stringify({
          error: "File not found. Use workspace_catalog for exact names.",
        });
      const rows: unknown[] = [];
      let size = 0, totalRecords = 0, matchingRecords = 0, full = false;
      scanSource(source, (value, index) => {
        if (!visible(source, index)) return;
        totalRecords++;
        if (query && !JSON.stringify(value).toLowerCase().includes(query.toLowerCase())) return;
        const match = matchingRecords++;
        if (match < offset || rows.length >= 30 || full) return;
        const row = { record: index + 1, value };
        const text = JSON.stringify(row);
        if (rows.length && size + text.length > 20000) { full = true; return; }
        rows.push(
          text.length > 20000
            ? {
                record: row.record,
                preview: text.slice(0, 20000),
                truncated: true,
              }
            : row,
        );
        size += Math.min(text.length, 20000);
      });
      return JSON.stringify({
        file,
        revision: document.revision,
        hash: source.hash,
        totalRecords,
        matchingRecords,
        offset,
        rows,
        nextOffset:
          offset + rows.length < matchingRecords ? offset + rows.length : null,
      });
    } catch {
      return JSON.stringify({
        error:
          "Could not read these records. Check the file name and parameters.",
      });
    }
  };
}
