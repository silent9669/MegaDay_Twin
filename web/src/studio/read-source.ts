import { inspect, type Source } from "./sources";
import { sourceText } from "./source-text";
type Parsed = ReturnType<typeof inspect>;
const browserTables = new Map<string, { text: string; encoding?: string; parsed: WeakRef<Parsed> }>();
const projections = new WeakMap<Source, Parsed>();
/** Reuse parsed tables within the browser, and hold transient replay projections by source identity. */
export function readSource(source: Source): Parsed {
  const cached = projections.get(source);
  if (cached) return cached;
  const key = `${source.kind}:${source.name}:${source.hash}`;
  const shared = typeof window !== "undefined" ? browserTables.get(key) : undefined;
  const parsed = (shared?.text === source.text && shared.encoding === source.encoding ? shared.parsed.deref() : undefined) ?? inspect(source.name, sourceText(source));
  if (typeof window !== "undefined") {
    projections.set(source, parsed);
    browserTables.set(key, { text: source.text, encoding: source.encoding, parsed: new WeakRef(parsed) });
    if (browserTables.size > 24) browserTables.delete(browserTables.keys().next().value!);
  }
  return parsed;
}
export function projectSource(source: Source, parsed: Parsed): Source {
  const view = { ...source, replay: undefined };
  projections.set(view, parsed);
  return view;
}
