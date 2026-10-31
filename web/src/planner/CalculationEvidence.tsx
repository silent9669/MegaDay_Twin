import { useId } from "react";
import { ModelChoice } from './DemandDetails';
import type { CalculationTrace } from './calculation-trace';

const n = (value: number) => new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value);
const money = (value: number) => `${n(value)} ₫`;
const valueText = (value: unknown) => value == null ? 'Not provided' : typeof value === 'object' ? JSON.stringify(value) : String(value);

export function ActionCalculation({ action, index }: { action: CalculationTrace['actions'][number]; index: number }) {
  const id = useId();
  const m = action.metrics;
  return <details className="planner-calculation" id={`${id}-calculation-${index}`}>
    <summary>Calculation and reconciliation</summary>
    <p><strong>Expected J</strong> = contribution − ad spend + leftover value − stockout penalty.</p>
    <p className="planner-substitution">{money(m.contribution.mean)} − {money(m.spend.mean)} + {money(m.leftover_value.mean)} − {money(m.stockout_penalty.mean)} = <strong>{money(action.reconciliation.sum_components_mean)}</strong></p>
    <p>Simulator J: {money(action.reconciliation.objective_mean)}. Reconciliation residual: {money(action.reconciliation.residual_vnd)}.</p>
    <p className="planner-help">This sum uses means from the same samples. Component P10/P50/P90 values cannot be added to obtain J quantiles.</p>
    <details><summary>Hourly ad spend</summary><ol className="planner-hourly">{action.spend_hourly.map((value, i) => <li key={i}>Hour {i + 1}: {money(value)}</li>)}</ol></details>
    <details><summary>Contribution and units by SKU</summary><ul>{action.per_sku.map((sku) => <li key={sku.sku_id}><strong>{sku.sku_id}</strong>: expected units sold {n(sku.units_sold.mean)}; P10/P50/P90: {n(sku.units_sold.p10)} / {n(sku.units_sold.p50)} / {n(sku.units_sold.p90)}. Expected contribution: {money(sku.contribution.mean)}.</li>)}</ul><p className="planner-help">SKU contribution excludes shared campaign ad spend.</p></details>
  </details>;
}

export function CalculationEvidence({ trace }: { trace?: CalculationTrace | null }) {
  const id = useId();
  if (!trace || trace.version !== 1) return <p className="planner-warning">No saved calculation trace is available. Quantiles are backend results, but their full inputs and calculation cannot be verified here.</p>;
  return <details className="planner-evidence" id={`${id}-calculation-evidence`}>
    <summary>Saved inputs and formulas</summary>
    <p>This record was saved with the simulation. Inputs are model data and assumptions; outputs are estimates.</p>
    <dl><dt>Quantity scope</dt><dd>{trace.quantity_scope === 'sku' ? `SKU ${trace.focus_sku}` : 'Whole campaign'}</dd><dt>J, budget and loss scope</dt><dd>Whole campaign {trace.campaign_id}</dd><dt>Calculation input hash</dt><dd>{trace.input_hash}</dd><dt>Samples / seed</dt><dd>{trace.samples} / {trace.seed}</dd><dt>Data as of</dt><dd>{trace.data_as_of ?? 'Not provided'}</dd></dl>
    <details><summary>Formulas and models</summary>{trace.equations.map((equation) => <div className="planner-trace-item" key={equation.id}><code>{equation.formula}</code><p>{equation.explanation}</p></div>)}</details>
    <details><summary>Source files and import batches</summary>{trace.sources.batches.length ? <ul>{trace.sources.batches.map((batch) => <li key={batch.batch_id}><strong>{batch.file_name}</strong> · {batch.source_type}<br />Batch: {batch.batch_id} · Data as of: {batch.as_of ?? 'Not provided'}<br />SHA-256: <code>{batch.file_sha256 ?? 'Not provided'}</code></li>)}</ul> : <p>No source files were recorded for this calculation.</p>}</details>
    {trace.sources.batches_truncated && <p className="planner-help">Sources are limited to 200 relevant import batches.</p>}
    <details><summary>Forecasts and ad effectiveness</summary><ul>{trace.sources.forecasts.map((forecast) => <li key={`${forecast.forecast_id}-${forecast.sku_id}`}><strong>{forecast.sku_id}</strong> · model {forecast.model_id}<br />Forecast: {forecast.forecast_id} · as of {forecast.origin ?? 'Not provided'}</li>)}</ul><p>iROAS: {trace.sources.iroas.source === 'prior' ? 'Prior assumption' : 'Data estimate'} · {trace.sources.iroas.estimate_id ?? 'No estimate ID'} · confidence: {trace.sources.iroas.confidence ?? 'Not provided'}.</p><p>Adstock delta: {n(trace.sources.iroas.delta)}; spend scale: {n(trace.sources.iroas.spend_scale)}.</p></details>
    <details><summary>Demand labels and model choices</summary>{trace.sources.forecasts.map(forecast => <section key={forecast.forecast_id}><h4>{forecast.sku_id}</h4><ModelChoice selection={forecast.model_selection} /></section>)}</details>
    {trace.assumptions && <details><summary>Budget, stockout and schedule assumptions</summary><p>Stockout penalty: {money(trace.assumptions.stockout_penalty_per_unit)}/unit. Rank loss: {n(trace.assumptions.rank_loss)}. Initial adstock: {n(trace.assumptions.adstock_start)}.</p><p>Budget source: {trace.assumptions.budget_source}. Simulation budget schedule: {trace.assumptions.daily_budget.map(money).join(', ')}.</p><p>Required running hours (from window start): {trace.assumptions.must_run_hours.map((hour) => hour + 1).join(', ') || 'None'}.</p></details>}
    <details><summary>Parameters and authors</summary><ul>{trace.sources.parameters.map((param) => <li key={`${param.key}-${param.scope}-${param.scope_id}`}><strong>{param.key}</strong> = {valueText(param.value)} · version {valueText(param.version)} · {param.scope ?? 'Unknown scope'} {param.scope_id ?? ''} · {param.set_by ?? 'Unknown author'}</li>)}</ul></details>
    <details><summary>Inputs and assumptions per SKU</summary>{trace.skus.map((sku) => <details key={sku.sku_id}><summary>{sku.sku_id}{sku.sku_id === trace.focus_sku ? ' · Active goal SKU' : ''}</summary><dl><dt>Starting stock</dt><dd>{sku.stock_unlimited ? 'Assumed unlimited stock' : sku.stock_start == null ? 'Not provided' : n(sku.stock_start)}</dd><dt>Unit cost</dt><dd>{sku.unit_cost_vnd == null ? 'Not provided' : money(sku.unit_cost_vnd)}</dd><dt>Leftover value per unit</dt><dd>{money(sku.leftover_value_per_unit)}</dd><dt>Dispersion assumptions</dt><dd>k = {n(sku.dispersion_k)}; sigma = {n(sku.sigma)}</dd></dl>{sku.economics_by_day?.map((economics) => <div className="planner-trace-item" key={economics.day}><strong>Contribution per unit · {economics.day}</strong><p>{money(economics.eff_price)} − {money(economics.unit_cost)} − ({n(economics.fee_rate * 100)}% × {money(economics.eff_price)}) − {money(economics.packaging_cost)} = <strong>{money(economics.m)}</strong>.</p><p>Price source: {economics.price_source}. Price − unit cost − platform fee − packaging.</p></div>)}<ol className="planner-hourly">{sku.price_hourly.map((price, i) => <li key={i}>Hour {i + 1}: price {money(price)}; contribution/unit {sku.contribution_per_unit_hourly[i] == null ? 'Not provided' : money(sku.contribution_per_unit_hourly[i])}; baseline demand {sku.forecast_mean_hourly[i] == null ? 'Not provided' : n(sku.forecast_mean_hourly[i])}.</li>)}</ol>{sku.restock.length > 0 && <p>Restocking: {sku.restock.map((item) => `hour ${item.hour + 1}: ${n(item.units)}`).join('; ')}.</p>}</details>)}</details>
    <div className="planner-warning"><strong>Result limitations</strong><ul>{trace.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></div>
  </details>;
}
