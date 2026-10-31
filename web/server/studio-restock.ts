import Papa from "papaparse";
import { inspect, normalize, hashText, type SourcePack } from "../src/studio/sources";
import { encodeSource, sourceText } from "../src/studio/source-text";
import { backendConnection, backendFetch, type BackendEnv } from "./backend-fetch";

export type RestockChange = { code: string; name: string; before: number; after: number; quantity: number; asOf: string };
export type RestockReceipt = { operationId: string; revision: number; change: RestockChange; csv: string; backend: "pending" | "synced" | "unavailable" };
export async function restockInventory(pack: SourcePack, hour: number, operationId: string) {
  const inventory = pack.files.find(file => file.kind === "inventory");
  if (!inventory) throw Error("Import inventory before restocking GD0003.");
  const parsed = inspect(inventory.name, sourceText(inventory));
  const column = (name: string) => parsed.columns.find(c => normalize(c) === normalize(name)) ?? name;
  const code = column("Mã nội bộ"), stock = column("Tồn thực tế"), name = column("Tên hàng"),
    timestamp = column("Thời điểm cập nhật"), date = column("Ngày"), note = column("Ghi chú");
  const time = (row: Record<string, unknown>) => String(row[timestamp] || row[date] || "");
  let index = -1;
  for (let i = 0; i < parsed.rows.length; i++) {
    const row = parsed.rows[i]!;
    if (String(row[code] ?? "").trim() !== "GD0003") continue;
    if (inventory.replay && inventory.replay.hours[i]! >= 0 && inventory.replay.hours[i]! > hour - inventory.replay.lagHours) continue;
    if (index < 0 || time(row) >= time(parsed.rows[index]!)) index = i;
  }
  if (index < 0) throw Error("GD0003 is not available at the current inventory time.");
  const current = parsed.rows[index]!, raw = String(current[stock] ?? "").trim(), before = raw ? Number(raw) : NaN;
  if (!Number.isSafeInteger(before) || before < 0 || before > 1e9 - 10) throw Error("GD0003 needs a valid stock count before restocking.");
  // Carry the receipt into later observations of this replay, never into history.
  for (let i = 0; i < parsed.rows.length; i++) {
    const row = parsed.rows[i]!;
    if (i !== index && (!inventory.replay || inventory.replay.hours[i]! < 0 || String(row[code] ?? "").trim() !== "GD0003" || time(row) < time(current))) continue;
    const value = String(row[stock] ?? "").trim();
    if (!value || !Number.isSafeInteger(Number(value)) || Number(value) < 0 || Number(value) > 1e9 - 10) continue;
    row[stock] = String(Number(value) + 10);
    row[note] = [row[note], "Nhập 10"].filter(Boolean).join("; ");
  }
  const text = Papa.unparse({ fields: [...new Set([...parsed.columns, note])], data: parsed.rows });
  const updated = { ...inventory, origin: "restock trigger GD0003", commit: undefined, encoding: undefined, ...encodeSource(text), hash: await hashText(text) };
  const change: RestockChange = { code: "GD0003", name: String(current[name] ?? "GD0003"), before, after: before + 10, quantity: 10, asOf: time(current) };
  const timeColumn = current[timestamp] ? "Thời điểm cập nhật" : "Ngày";
  const csv = Papa.unparse([{ [timeColumn]: change.asOf, "Mã nội bộ": change.code, "Tên hàng": change.name, "Tồn thực tế": change.after, "Ghi chú": current[note], "Restock ID": operationId }]);
  return { pack: { ...pack, files: pack.files.map(file => file === inventory ? updated : file) }, change, csv };
}

/** Ingest only the changed observation through the existing normalization pipeline. */
export async function syncRestock(csv: string, env: BackendEnv): Promise<RestockReceipt["backend"]> {
  const connection = backendConnection(env);
  if (!connection) return "unavailable";
  const form = new FormData();
  form.append("files", new Blob([csv], { type: "text/csv" }), "ton_kho_google_sheet.csv");
  const response = await backendFetch(new URL("ingest/files?force=true", connection.base), {
    method: "POST", headers: { "X-User": connection.user, "X-Role": connection.role }, body: form,
    signal: AbortSignal.timeout(60000),
  }, connection.service);
  if (!response.ok) throw Error("Inventory is saved; backend sync needs retry.");
  const result = await response.json() as { data?: { batches?: { status: string; rows_held: number }[] } };
  const batches = result.data?.batches;
  if (!batches?.length || batches.some(batch => batch.rows_held > 0 || batch.status !== "loaded"))
    throw Error("Inventory is saved; backend mapping needs review.");
  return "synced";
}
