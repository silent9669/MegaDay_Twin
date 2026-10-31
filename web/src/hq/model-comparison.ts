import { api } from "../api/client";
import type { Envelope, GoalOut, SkuDetail, ScenarioRun } from "../api/types";
import type { WorksheetEvidence } from "./worksheet-suggestions";
import { suggestedWorksheet } from "./worksheet-suggestions";

/** Use action means from one immutable run, not min(stock, mean demand). */
export function modelComparison(run: ScenarioRun, sku: string) {
  const trace = run.calculation_trace;
  const item = trace?.skus.find(row => row.sku_id === sku && row.in_campaign);
  if (run.blocked || !trace || trace.input_hash !== run.input_hash || !item || item.is_bundle || item.stock_start === null)
    throw Error("A complete SKU calculation trace is required. Review the product in Goals.");
  const economics = item.economics_by_day?.[0];
  if (!economics) throw Error("Per-unit economics are missing from this calculation.");
  const revenueWeight = (row: typeof item) => row.in_campaign ? row.forecast_mean_hourly.reduce((total, demand, i) => total + demand * (row.price_hourly[i] ?? 0), 0) : 0;
  const total = trace.skus.reduce((sum, row) => sum + revenueWeight(row), 0);
  if (total <= 0) throw Error("Forecast revenue is unavailable for allocating campaign advertising costs.");
  const share = revenueWeight(item) / total;
  const source = `Scenario ${run.scenario_id} · ${sku} · ${trace.start} · ${trace.horizon_hours}h`;
  const evidence: WorksheetEvidence = {
    stock: { value: item.stock_start, source }, price: { value: economics.eff_price, source },
    cost: { value: economics.unit_cost, source }, packaging: { value: economics.packaging_cost, source },
    feePercent: { value: economics.fee_rate * 100, source },
  };
  const results = (["continue", "pause"] as const).map(key => {
    const action = trace.actions.find(row => row.key === key);
    const detail = action?.per_sku.find(row => row.sku_id === sku);
    const outcome = run.results.find(row => row.key === key);
    const quantity = outcome?.per_sku.find(row => row.sku_id === sku);
    if (!action || !detail?.revenue || !detail.leftover_units || !quantity)
      throw Error("Both continue and pause estimates are required for this product.");
    const spend = action.metrics.spend.mean * share;
    evidence[key === "continue" ? "continueUnits" : "pauseUnits"] = { value: quantity.demand.mean, source: `${source} · modeled demand` };
    if (key === "continue") evidence.spend = { value: spend, source: `${source} · ${(share * 100).toFixed(1)}% of campaign spend` };
    return { units: detail.units_sold.mean, leftover: detail.leftover_units.mean, revenue: detail.revenue.mean,
      costs: detail.revenue.mean - detail.contribution.mean, beforeAds: detail.contribution.mean,
      spend, afterAds: detail.contribution.mean - spend };
  });
  if (![...Object.values(evidence).map(f => f!.value), ...results.flatMap(row => Object.values(row))].every(Number.isFinite))
    throw Error("The calculation contains incomplete estimates.");
  return { value: suggestedWorksheet(evidence), evidence, results, share, source,
    violations: run.results.filter(row => ["continue", "pause"].includes(row.key) && (!row.feasible || row.goal_eligible === false))
      .map(row => `${row.key === "continue" ? "Continue" : "Pause"}: ${!row.feasible ? "Does not satisfy current campaign constraints" : "Does not meet the active goal"}`),
    trace };
}
export type ModelComparison = ReturnType<typeof modelComparison>;

/** Preview only: never activate a goal or replace the campaign recommendation. */
export async function loadModelComparison(sku: string, campaignId: string) {
  const [context, product] = await Promise.all([
    api.get<Envelope<{ active_goal: GoalOut | null; base_goal: { goal_id: string; version: number } | null }>>(`/sale-plans/context?campaign_id=${encodeURIComponent(campaignId)}`),
    api.get<Envelope<SkuDetail>>(`/skus/${encodeURIComponent(sku)}`),
  ]);
  if (product.data.sku !== sku || product.data.campaign?.id !== campaignId) throw Error("The product campaign changed. Reload the product first.");
  const at = Date.parse(product.data.as_of ?? "");
  if (!Number.isFinite(at)) throw Error("A backend data timestamp is required for model estimates.");
  const start = Math.ceil(at / 3600000) * 3600000;
  const goal = context.data.active_goal;
  const end = goal ? Date.parse(goal.deadline) : start + 24 * 3600000;
  const hours = (end - start) / 3600000;
  if (!Number.isInteger(hours) || hours < 1 || hours > 72) throw Error("Update the campaign goal deadline in Goals before calculating this comparison.");
  const response = await api.post<Envelope<{ scenario: ScenarioRun }>>("/sale-plans/preview", {
    campaign_id: campaignId, expected_goal: context.data.base_goal,
    start: new Date(start).toISOString(), horizon_hours: hours, samples: 1000, seed: 20261111,
    focus_sku: goal?.params?.focus_sku ?? (goal ? null : sku),
    goal: { kind: goal?.kind ?? "profit", target_units: goal?.target_units ?? null,
      deadline: new Date(end).toISOString(), params: goal?.params ?? {} },
  });
  return modelComparison(response.data.scenario, sku);
}
