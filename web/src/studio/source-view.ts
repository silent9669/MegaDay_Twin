import { inspect, type SourcePack } from "./sources";
export type StockRow = {
  code: string;
  name: string;
  stock: number | null;
  asOf: string;
};
export function sourceView(pack: SourcePack | null, hour: number) {
  const inventory = pack?.files.find((f) => f.kind === "inventory");
  const latest = new Map<string, StockRow>();
  if (inventory)
    for (const row of inspect(inventory.name, inventory.text).rows) {
      const asOf = String(row["Thời điểm cập nhật"] ?? row["Ngày"] ?? "");
      const rowHour = asOf.includes(":") ? Number(asOf.slice(11, 13)) : 0;
      if (rowHour > hour) continue;
      const code = String(row["Mã nội bộ"] ?? "");
      const previous = latest.get(code);
      if (previous && previous.asOf > asOf) continue;
      const raw = String(row["Tồn thực tế"] ?? "").trim(),
        value = raw ? Number(raw) : NaN;
      latest.set(code, {
        code,
        name: String(row["Tên hàng"] ?? code),
        stock: Number.isFinite(value) && value >= 0 ? value : null,
        asOf,
      });
    }
  const orders = pack?.files.find((f) => f.kind === "orders"),
    ads = pack?.files.find((f) => f.kind === "ads"),
    campaigns = pack?.files.find((f) => f.kind === "campaigns");
  const orderRows = orders
    ? inspect(orders.name, orders.text).rows.filter(
        (row) =>
          Number(String(row["Ngày đặt hàng"] ?? "").slice(11, 13)) < hour,
      ).length
    : 0;
  // Report raw ad observations only. Timezone normalization belongs to Python.
  const adRows = ads ? inspect(ads.name, ads.text).rows.length : 0;
  return {
    stock: [...latest.values()],
    orderRows,
    adRows,
    campaignCount: campaigns
      ? inspect(campaigns.name, campaigns.text).rows.length
      : 0,
    importedAds: !!ads && !ads.commit,
  };
}
