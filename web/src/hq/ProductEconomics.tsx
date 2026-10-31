import { Button } from "../components/ui/button";
import type { StockRow } from "../studio/source-view";
import type { SourcePack } from "../studio/sources";
import type { TrackedProduct, Worksheet } from "./types";
import { useProductEvidence } from "./product-evidence";

const money = (value: number | null) => value === null ? "—" : `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value)} VND`;
export function ProductEconomics({ product, pack, stock, worksheet, revision = 0 }: {
  product: TrackedProduct;
  pack: SourcePack | null;
  stock?: StockRow;
  worksheet?: Worksheet;
  revision?: number;
}) {
  const { evidence, loading, error, retry } = useProductEvidence(product, pack, stock, revision);
  const value = (key: keyof Worksheet) => evidence[key]?.value ?? worksheet?.[key] ?? null;
  const origin = (key: keyof Worksheet) => value(key) === null ? "Missing input"
    : evidence[key]?.value === value(key) ? evidence[key]!.source : "Comparison assumption";
  const price = value("price"), cost = value("cost"), packaging = value("packaging"), rate = value("feePercent");
  const fee = price !== null && rate !== null ? price * rate / 100 : null;
  const contribution = price !== null && cost !== null && packaging !== null && fee !== null ? price - cost - packaging - fee : null;
  return <section className="hq-unit-economics" aria-label="Per-unit economics">
    <h3>Per-unit economics</h3>
    {loading && <p role="status">Loading product data…</p>}
    {error && <Button variant="outline" onClick={retry}>Retry product data</Button>}
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
