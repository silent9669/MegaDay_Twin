import Papa from "papaparse";
import { scanSource } from "./analysis-tools";
import { DEMAND_COLUMN, SOURCE_DEFS, normalize, type Source } from "../src/studio/sources";
import { sourceText } from "../src/studio/source-text";

/** The exact baseline bytes used both for ingestion and label provenance. */
export function backendSourceChunks(source: Source): string[] {
  if (!source.replay && source.kind !== "inventory") return [sourceText(source)];
  const baseline = (index: number) => !source.replay || source.replay.hours[index]! < 0;
  let hasHourlyValue = false;
  let columns = scanSource(source, (row, index) => {
    if (baseline(index) && Object.entries(row).some(([key, value]) =>
      normalize(key) === normalize("Thời điểm cập nhật") && String(value ?? "").trim())) hasHourlyValue = true;
  }).filter(key => source.kind !== "inventory" || key !== DEMAND_COLUMN);
  // Replay adds hourly columns to daily history. An empty hourly column would
  // make Python discard every daily row instead of using its end-of-day date.
  if (source.kind === "inventory" && !hasHourlyValue && columns.some(key => normalize(key) === normalize("Ngày"))) {
    columns = columns.filter(key => normalize(key) !== normalize("Thời điểm cập nhật"));
  }
  const chunks: string[] = [];
  let pending = Papa.unparse([columns]);
  scanSource(source, (row, index) => {
    if (!baseline(index)) return;
    pending += "\r\n" + Papa.unparse([columns.map(key => row[key] ?? "")]);
    if (pending.length >= 65536) { chunks.push(pending); pending = ""; }
  });
  if (pending) chunks.push(pending);
  return chunks;
}

/** Keep at most one table's text/bytes in memory while sending the multipart body. */
export function backendImport(files: Source[]) {
  const boundary = `megaday-${crypto.randomUUID()}`;
  const encode = (text: string) => new TextEncoder().encode(text);
  function* parts() {
    for (const source of files) {
      const definition = SOURCE_DEFS.find(d => d.kind === source.kind);
      if (!definition || (source.replay && !source.replay.hours.some(hour => hour < 0))) continue;
      yield encode(`--${boundary}\r\nContent-Disposition: form-data; name="files"; filename="${definition.file}"\r\nContent-Type: ${source.name.endsWith(".json") ? "application/json" : "text/csv"}\r\n\r\n`);
      for (const chunk of backendSourceChunks(source)) yield encode(chunk);
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
