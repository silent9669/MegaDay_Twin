import type { Dist } from '../api/types';

/** Immutable snapshot supplied by the simulator, never reconstructed by the UI. */
export type CalculationTrace = {
  version: 1; input_hash: string; campaign_id: string; focus_sku: string | null;
  quantity_scope: 'sku' | 'campaign'; economic_scope: 'campaign';
  start: string; horizon_hours: number; samples: number; seed: number;
  data_as_of: string | null; panel_version: unknown;
  goal: { kind: string; target_units: number | null; params: Record<string, unknown> };
  equations: { id: string; formula: string; explanation: string }[];
  sources: {
    batches_truncated?: boolean;
    batches: { batch_id: string; source_type: string; file_name: string; file_sha256: string | null; as_of: string | null }[];
    forecasts: { forecast_id: string; sku_id: string; model_id: string; origin: string | null; data_as_of: string | null; panel_version: unknown }[];
    parameters: { key: string; value: unknown; version: unknown; scope: string | null; scope_id: string | null; set_by: string | null }[];
    iroas: { source: string; estimate_id: string | null; confidence: string | null; delta: number; spend_scale: number };
  };
  assumptions?: { stockout_penalty_per_unit: number; rank_loss: number; adstock_start: number;
    budget_source: string; daily_budget: number[]; must_run_hours: number[] };
  skus: { sku_id: string; in_campaign: boolean; is_bundle: boolean; stock_start: number | null;
    stock_unlimited: boolean; unit_cost_vnd: number | null; price_hourly: number[];
    contribution_per_unit_hourly: number[]; forecast_mean_hourly: number[];
    forecast_id: string | null; forecast_label: string | null; dispersion_k: number; sigma: number;
    economics_by_day?: { sku_id: string; day: string; eff_price: number; price_source: string;
      unit_cost: number; packaging_cost: number; fee_rate: number; m: number; is_bundle: boolean }[];
    leftover_value_per_unit: number; restock: { hour: number; units: number }[] }[];
  actions: { key: string; action: unknown; metrics: {
    objective: Dist; contribution: Dist; spend: Dist; leftover_value: Dist;
    revenue?: Dist; selling_cost?: Dist; after_ads?: Dist;
    stockout_penalty: Dist; units_sold: Dist; focus_units_sold: Dist | null;
  }; reconciliation: { sum_components_mean: number; objective_mean: number; residual_vnd: number };
    spend_hourly: number[]; per_sku: { sku_id: string; units_sold: Dist; contribution: Dist; revenue?: Dist; leftover_units?: Dist | null }[] }[];
  limitations: string[];
};
