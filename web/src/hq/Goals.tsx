import { useState } from "react";
import { SaleGoalPlanner } from "../planner/SaleGoalPlanner";
import { SkuGoalAssistant } from "../planner/SkuGoalAssistant";
import type { TrackedProduct } from "./types";

export function Goals({ product, campaignId }: { product: TrackedProduct | null; campaignId?: string }) {
  const [scope, setScope] = useState(product && !campaignId ? "product" : "campaign");
  const [showFacts, setShowFacts] = useState(false);
  const sku = scope === "product" ? product?.sku : undefined;
  return <>
    <div className="hq-product-toolbar">
      <h2>Goals</h2>
      <label>Quantity scope
        <select aria-label="Goal quantity scope" value={scope} onChange={e => setScope(e.target.value)}>
          {product && <option value="product">{product.sku} · {product.name}</option>}
          <option value="campaign">Whole campaign</option>
        </select>
      </label>
    </div>
    {sku && <details className="hq-goal-context" onToggle={e => setShowFacts(e.currentTarget.open)}><summary>Product facts &amp; Milo</summary>{showFacts && <SkuGoalAssistant key={sku} sku={sku} />}</details>}
    <SaleGoalPlanner key={sku || "campaign"} embedded focusSku={sku} campaignIds={campaignId ? [campaignId] : undefined} />
  </>;
}
