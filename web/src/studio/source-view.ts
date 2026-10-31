import { readSource } from "./read-source";
import { visibleSources } from "./replay";
import { DEMAND_COLUMN, normalize, type SourcePack } from "./sources";
export type StockRow = {
  code: string;
  name: string;
  stock: number | null;
  asOf: string;
  demand?: string;
};
export function sourceView(pack: SourcePack | null, hour: number) {
  pack = visibleSources(pack, hour);
  const inventory = pack?.files.find((f) => f.kind === "inventory");
  const latest = new Map<string, StockRow>();
  if (inventory) {
    const parsed = readSource(inventory);
    const column = (name: string) => parsed.columns.find(c => normalize(c) === normalize(name)) ?? name;
    const timestampColumn = column("Thời điểm cập nhật"), dateColumn = column("Ngày"),
      codeColumn = column("Mã nội bộ"), stockColumn = column("Tồn thực tế"), nameColumn = column("Tên hàng");
    const hasDemand = parsed.columns.includes(DEMAND_COLUMN);
    for (const row of parsed.rows) {
      const asOf = String(row[timestampColumn] || row[dateColumn] || "");
      const code = String(row[codeColumn] ?? "").trim();
      if (!code) continue;
      const previous = latest.get(code);
      if (previous && previous.asOf > asOf) continue;
      const raw = String(row[stockColumn] ?? "").trim(),
        value = raw ? Number(raw) : NaN;
      latest.set(code, {
        code,
        name: String(row[nameColumn] ?? code),
        stock: Number.isFinite(value) && value >= 0 ? value : null,
        asOf,
        ...(hasDemand ? { demand: String(row[DEMAND_COLUMN] ?? "") } : {}),
      });
    }
  }
  const orders = pack?.files.find((f) => f.kind === "orders"),
    ads = pack?.files.find((f) => f.kind === "ads"),
    campaigns = pack?.files.find((f) => f.kind === "campaigns");
  const orderRows = orders ? readSource(orders).rows.length : 0;
  // Report raw ad observations only. Timezone normalization belongs to Python.
  const adRows = ads ? readSource(ads).rows.length : 0;
  return {
    stock: [...latest.values()],
    orderRows,
    adRows,
    campaignCount: campaigns
      ? readSource(campaigns).rows.length
      : 0,
    importedAds: !!ads && !ads.commit,
  };
}
