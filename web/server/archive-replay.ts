import Papa from "papaparse";
import { z } from "zod";
import type { ImportTable } from "../src/studio/import-types";
const kinds = ["orders", "ads", "inventory"] as const;
const schema = z.object({
  ngay: z.iso.date(), adsLagHours: z.number().int().min(0).max(24),
  files: z.object({ orders: z.string(), ads: z.string(), inventory: z.string() }),
  rows: z.object({ orders: z.array(z.tuple([z.number().int().min(0).max(23), z.string()])), ads: z.array(z.tuple([z.number().int().min(0).max(23), z.string()])), inventory: z.array(z.tuple([z.number().int().min(0).max(23), z.string()])) }),
});
/** Validate replay buckets against the exact uploaded rows; never trust replay as a second data source. */
export function applyArchiveReplay(files: { name: string; text: string }[], tables: ImportTable[]) {
  const manifests = files.filter(file => /(?:^|\/)_replay\.json$/.test(file.name));
  if (!manifests.length) return;
  if (manifests.length !== 1) throw Error("Choose one event replay per import.");
  const manifest = manifests[0]!;
  const replay = schema.parse(JSON.parse(manifest.text));
  const prefix = manifest.name.slice(0, -"_replay.json".length);
  for (const kind of kinds) {
    const file = files.find(file => file.name === prefix + replay.files[kind]);
    const table = tables.find(table => table.file === file?.name && table.source.kind === kind);
    if (!file || !table) throw Error("Replay references a missing business table.");
    const rows = Papa.parse<string[]>(file.text.replace(/^\uFEFF/, ""), { skipEmptyLines: "greedy" }).data.slice(1);
    const events = replay.rows[kind];
    if (rows.length !== events.length || rows.some((row, index) => JSON.stringify(row) !== JSON.stringify(Papa.parse<string[]>(events[index]![1]).data[0])))
      throw Error("Replay rows do not match the uploaded tables.");
    table.source.replay = { date: replay.ngay, hours: events.map(([hour]) => hour), lagHours: kind === "ads" ? replay.adsLagHours : 0 };
  }
}
