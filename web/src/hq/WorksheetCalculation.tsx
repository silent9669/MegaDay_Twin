import { worksheetResults, type Worksheet } from "./types";
import type { WorksheetEvidence } from "./worksheet-suggestions";

const n = (value: number) => new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
export function WorksheetCalculation({ value: w, evidence, fields }: {
  value: Worksheet;
  evidence: WorksheetEvidence;
  fields: [keyof Worksheet, string][];
}) {
  const results = worksheetResults(w);
  const fee = w.price !== null && w.feePercent !== null ? w.price * w.feePercent / 100 : null;
  const margin = fee !== null && w.cost !== null && w.packaging !== null ? w.price! - w.cost - w.packaging - fee : null;
  const difference = results ? results[0]!.afterAds - results[1]!.afterAds : null;
  return <details className="hq-calculation">
    <summary>How this is calculated</summary>
    <h4>Inputs & source evidence</h4>
    <dl className="hq-evidence-list">
      {fields.map(([key, label]) => <div key={key}>
        <dt>{label}</dt>
        <dd><strong>{w[key] === null ? "Missing" : n(w[key])}</strong>
          <span>{evidence[key] ? <>
            {w[key] === evidence[key].value ? "Matches source" : `Current source: ${n(evidence[key].value)}; worksheet ${w[key] === null ? "not filled" : "overridden"}`} · {evidence[key].source}
          </> : w[key] === null ? "No verified SKU-linked value. Enter an assumption." : "User input / saved assumption; no matching source evidence."}</span>
        </dd>
      </div>)}
    </dl>
    <p>Product facts use exact source matches or accepted backend SKU mappings. Each input shows its source; demand and spend remain assumptions.</p>
    {results && margin !== null && fee !== null ? <>
      <h4>Per-unit calculation</h4>
      <p>Platform fee = {n(w.price!)} × {n(w.feePercent!)}% = {n(fee)} VND.</p>
      <p>Contribution per unit = price − unit cost − packaging − platform fee = {n(w.price!)} − {n(w.cost!)} − {n(w.packaging!)} − {n(fee)} = <strong>{n(margin)} VND</strong>.</p>
      <div className="hq-calculation-options">
        {results.map((result, index) => <section key={index} aria-label={`${index === 0 ? "Continue" : "Pause"} ads calculation`}>
          <h4>{index === 0 ? "Continue ads" : "Pause ads"}</h4>
          <ol>
            <li>Units sold = min({n(w.stock!)} available, {n(index === 0 ? w.continueUnits! : w.pauseUnits!)} assumed demand) = <strong>{n(result.units)}</strong>.</li>
            <li>Units remaining = {n(w.stock!)} − {n(result.units)} = {n(result.leftover)}.</li>
            <li>Revenue = {n(result.units)} × {n(w.price!)} = {n(result.revenue)} VND.</li>
            <li>Costs = {n(result.units)} × ({n(w.cost!)} + {n(w.packaging!)} + {n(fee)}) = {n(result.costs)} VND.</li>
            <li>Contribution = {n(result.revenue)} − {n(result.costs)} = {n(result.beforeAds)} VND.</li>
            <li>Result = {n(result.beforeAds)} − {n(result.spend)} ad spend = <strong>{n(result.afterAds)} VND</strong>.</li>
          </ol>
        </section>)}
      </div>
      <h4>{difference === 0 ? "Both options have the same result" : `${difference! > 0 ? "Continue" : "Pause"} ads has the higher estimated result`}</h4>
      <p>Continue − pause = {n(results[0]!.afterAds)} − ({n(results[1]!.afterAds)}) = <strong>{n(difference!)} VND</strong>. The difference comes from {n(results[0]!.units - results[1]!.units)} additional units × {n(margin)} VND contribution − {n(w.spend!)} VND new ad spend.</p>
      {margin > 0 ? <p>To match the pause result, ads must add at least <strong>{n(Math.ceil(w.spend! / margin))} whole units</strong> beyond pause sales ({n(w.spend!)} ÷ {n(margin)}, rounded up). {Math.ceil(w.spend! / margin) > w.stock! - results[1]!.units ? "Available stock cannot cover that threshold." : "This is a break-even threshold, not a demand forecast."}</p>
        : <p>Contribution per unit is zero or negative. Extra sales cannot recover positive advertising spend under these inputs.</p>}
    </> : <p>Complete all eight inputs to see each calculation with your numbers. Unknown demand, fees and spend are never treated as zero.</p>}
    <p>These estimates exclude taxes, returns, fixed costs, restocking and leftover inventory value. Both demand inputs must cover the same period. Campaign commitments and causal ad uplift are not evaluated; this is not an optimized recommendation.</p>
  </details>;
}
