import { readSource, projectSource } from "./read-source";
import Papa from "papaparse";
import { inspect, hashText, sourceKey, type Source, type SourcePack } from "./sources";
import { encodeSource, sourceText } from "./source-text";
import type { Sandbox } from "./workspace-types";

export function playbackHour(clock?: Sandbox | null, now = Date.now()) {
  return Math.min(24, (clock?.hour ?? 1) + (clock?.startedAt ? Math.max(0, Math.floor((now - Date.parse(clock.startedAt)) / 6000)) : 0));
}
export function visibleSources(pack: SourcePack | null, hour: number): SourcePack | null {
  if (!pack?.files.some(file => file.replay)) return pack;
  return { ...pack, files: pack.files.map(source => {
    if (!source.replay) return source;
    const parsed = readSource(source);
    const rows = parsed.rows.filter((_, index) => source.replay!.hours[index]! < 0 || source.replay!.hours[index]! <= hour - source.replay!.lagHours);
    return projectSource(source, { ...parsed, rows });
  }) };
}
/** Keep the baseline, replace a previous event instead of appending duplicate event rows. */
export async function mergeReplaySources(existing: Source[], incoming: Source[]) {
  const merged = new Map(existing.map(source => [sourceKey(source), source]));
  for (const source of incoming) {
    const previous = merged.get(sourceKey(source));
    if (!source.replay || !previous) { merged.set(sourceKey(source), source); continue; }
    const old = inspect(previous.name, sourceText(previous));
    const fresh = inspect(source.name, sourceText(source));
    const baseline = old.rows.filter((_, index) => !previous.replay || previous.replay.hours[index]! < 0);
    const columns = [...new Set([...old.columns, ...fresh.columns])];
    const text = Papa.unparse({ fields: columns, data: [...baseline, ...fresh.rows] });
    merged.set(sourceKey(source), { ...source, ...encodeSource(text), hash: await hashText(text),
      replay: { ...source.replay, hours: [...baseline.map(() => -1), ...source.replay.hours] } });
  }
  return [...merged.values()];
}
