import { useState } from "react";
import { SaleGoalPlanner } from "../planner/SaleGoalPlanner";
import { SkuGoalAssistant } from "../planner/SkuGoalAssistant";
import type { TrackedProduct } from "./types";
import { useProductIdentity } from "./useProductIdentity";
import { Link } from "react-router";

export function Goals({ product, campaignId }: { product: TrackedProduct | null; campaignId?: string }) {
  const [scope, setScope] = useState(product && !campaignId ? "product" : "campaign");
  const [showFacts, setShowFacts] = useState(false);
  const identity = useProductIdentity(scope === "product" ? product : null);
  const sku = scope === "product" ? identity.sku : undefined;
  const blocked = scope === "product" && !sku;
  const ids = campaignId ? [campaignId].filter(id => !identity.campaignIds || identity.campaignIds.includes(id)) : identity.campaignIds;
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
    {blocked ? <div role={identity.loading ? "status" : "alert"}><p>{identity.loading ? "Checking the product's backend SKU…" : identity.error}</p>{!identity.loading && <><Link to="/mock-data">Review source matching</Link> <button onClick={identity.retry}>Retry mapping</button></>}</div>
      : <SaleGoalPlanner key={sku || "campaign"} embedded focusSku={sku} campaignIds={ids} />}
  </>;
}
