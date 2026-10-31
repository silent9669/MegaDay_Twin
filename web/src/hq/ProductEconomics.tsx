import { useMemo } from "react";
import { useSku } from "../api/queries";
import { config } from "../lib/config";
import type { StockRow } from "../studio/source-view";
import type { SourcePack } from "../studio/sources";
import type { TrackedProduct, Worksheet } from "./types";
import { modelSuggestion, workspaceSuggestion } from "./worksheet-suggestions";

const money = (value: number | null) => value === null ? "—" : `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value)} VND`;
export function ProductEconomics({ product, pack, stock, worksheet }: {
  product: TrackedProduct;
  pack: SourcePack | null;
  stock?: StockRow;
  worksheet?: Worksheet;
}) {
  const model = useSku(product.mode === "model" ? product.sku : undefined);
  const evidence = useMemo(() => product.mode === "model" ? modelSuggestion(model.data?.data, config.useFixtures)
    : workspaceSuggestion(pack, product.sku, stock), [product, pack, stock, model.data]);
  const value = (key: keyof Worksheet) => worksheet?.[key] ?? evidence[key]?.value ?? null;
  const origin = (key: keyof Worksheet) => value(key) === null ? "Missing input"
    : evidence[key]?.value === value(key) ? evidence[key]!.source : "Comparison assumption";
  const price = value("price"), cost = value("cost"), packaging = value("packaging"), rate = value("feePercent");
  const fee = price !== null && rate !== null ? price * rate / 100 : null;
  const contribution = price !== null && cost !== null && packaging !== null && fee !== null ? price - cost - packaging - fee : null;
  return <section className="hq-unit-economics" aria-label="Per-unit economics">
    <h3>Per-unit economics</h3>
    <div className="hq-table-scroll" tabIndex={0} role="region" aria-label="Per-unit cost breakdown">
      <table>
        <caption>{product.sku} · VND per unit</caption>
        <thead><tr><th scope="col">Per unit</th><th scope="col">Amount</th></tr></thead>
        <tbody>
          <tr><th scope="row">Selling price after shop-funded discounts</th><td>{money(price)}<small>{origin("price")}</small></td></tr>
          <tr><th scope="row">Unit cost</th><td>{money(cost)}<small>{origin("cost")}</small></td></tr>
          <tr><th scope="row">Platform fee{rate !== null ? ` (${rate}%)` : ""}</th><td>{money(fee)}<small>{origin("feePercent")}</small></td></tr>
          <tr><th scope="row">Packaging</th><td>{money(packaging)}<small>{origin("packaging")}</small></td></tr>
          <tr><th scope="row">Contribution before ads</th><td><strong>{money(contribution)}</strong></td></tr>
        </tbody>
      </table>
    </div>
    <p>{contribution === null ? "Add missing price, cost, packaging or fee inputs in Comparisons to calculate contribution."
      : `${money(price)} − ${money(cost)} − ${money(fee)} − ${money(packaging)} = ${money(contribution)} before ads, taxes, returns and fixed costs.`}</p>
  </section>;
}
