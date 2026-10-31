import Papa from "papaparse";
import { scanSource } from "./analysis-tools";
import { DEMAND_COLUMN, SOURCE_DEFS, type Source } from "../src/studio/sources";
import { sourceText } from "../src/studio/source-text";

/** Keep at most one table's text/bytes in memory while sending the multipart body. */
export function backendImport(files: Source[]) {
  const boundary = `megaday-${crypto.randomUUID()}`;
  const encode = (text: string) => new TextEncoder().encode(text);
  function* parts() {
    for (const source of files) {
      const definition = SOURCE_DEFS.find(d => d.kind === source.kind);
      if (!definition || (source.replay && !source.replay.hours.some(hour => hour < 0))) continue;
      yield encode(`--${boundary}\r\nContent-Disposition: form-data; name="files"; filename="${definition.file}"\r\nContent-Type: ${source.name.endsWith(".json") ? "application/json" : "text/csv"}\r\n\r\n`);
      if (!source.replay && source.kind !== "inventory") {
        yield encode(sourceText(source));
      } else {
        const chunks: Uint8Array[] = [];
        let columns: string[] | undefined, pending = "";
        scanSource(source, (row, index) => {
          if (!columns) {
            columns = Object.keys(row).filter(key => source.kind !== "inventory" || key !== DEMAND_COLUMN);
            pending = Papa.unparse([columns]) + "\r\n";
          }
          if (source.replay && source.replay.hours[index]! >= 0) return;
          pending += Papa.unparse([columns.map(key => row[key] ?? "")]) + "\r\n";
          if (pending.length >= 65536) { chunks.push(encode(pending)); pending = ""; }
        });
        if (pending) chunks.push(encode(pending));
        yield* chunks;
      }
      yield encode("\r\n");
    }
    yield encode(`--${boundary}--\r\n`);
  }
  const iterator = parts();
  return { contentType: `multipart/form-data; boundary=${boundary}`, body: new ReadableStream<Uint8Array>({
    pull(controller) { const next = iterator.next(); if (next.done) controller.close(); else controller.enqueue(next.value); },
    cancel() { iterator.return(); },
  }) };
}
