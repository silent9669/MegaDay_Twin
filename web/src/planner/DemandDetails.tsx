import type { DemandSummary, ModelSelection } from "./demand-contract";

export function ModelChoice({ selection }: { selection?: ModelSelection | null }) {
  if (!selection?.model_id) return <p>No saved model-selection evidence yet. Run a new simulation to inspect it.</p>;
  const status = selection.data_status;
  return <div>
    <p><strong>Saved forecast model:</strong> {selection.model_id} · {selection.from_backtest ? "Selected from historical testing" : "Fallback or label-guided model; not validated by the label"}</p>
    {selection.forecast_origin && <p>Forecast origin: {selection.forecast_origin}</p>}
    {status && <p>History: {status.history_days} days{status.new ? " · New product" : ""}{status.sparse ? " · Sparse sales" : ""}{status.insufficient ? " · Limited usable data" : ""}.</p>}
    <ul>{selection.reasons?.map((reason, i) => <li key={i}>{reason}</li>)}</ul>
    <p>Demand label used in this forecast: {selection.primary_label ?? "Unclassified"}. Policy: {selection.policy_version ?? "Unknown"}.</p>
  </div>;
}
export function DemandDetails({ demand }: { demand?: DemandSummary }) {
  return <section aria-label="Demand classification and model choice">
    <h4>Demand classification</h4>
    <p><strong>{demand?.primary_label ?? "Unclassified"}</strong> · {demand?.source === "warehouse_manual" ? "Manually selected in Warehouse" : demand?.source === "warehouse_ai" ? "Shared Warehouse label" : demand?.source === "history" ? "Estimated from historical pattern" : "No shared classification yet"}</p>
    {demand?.label_evidence && <p>{demand.label_evidence.reason}<br />Assessed: {demand.label_evidence.assessed_on} · Source revision: {demand.label_evidence.source_revision}. AI support scores are not calibrated probabilities.</p>}
    <details><summary>Why this forecast model?</summary><ModelChoice selection={demand?.model_selection} /></details>
  </section>;
}
