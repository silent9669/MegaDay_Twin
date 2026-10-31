import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ActionCalculation, CalculationEvidence } from './CalculationEvidence';
import type { CalculationTrace } from './calculation-trace';

const dist = (mean: number) => ({ mean, p10: mean / 2, p50: mean, p90: mean * 2 });
const trace: CalculationTrace = {
  version: 1, input_hash: 'immutable-hash', campaign_id: 'C1', focus_sku: 'SKU-A',
  quantity_scope: 'sku', economic_scope: 'campaign', start: '2026-11-11T00:00:00Z',
  horizon_hours: 1, samples: 1000, seed: 123, data_as_of: '2026-11-10T00:00:00Z', panel_version: 2,
  goal: { kind: 'sell_target', target_units: 50, params: { max_spend_vnd: 100000 } },
  equations: [{ id: 'objective', formula: 'J = contribution - spend + leftover_value - penalty', explanation: 'Tính trên từng mẫu' }],
  sources: { batches: [{ batch_id: 'batch-1', source_type: 'orders', file_name: 'don_hang.csv', file_sha256: 'hash', as_of: null }], forecasts: [], parameters: [], iroas: { source: 'prior', estimate_id: null, confidence: 'low', delta: 0.5, spend_scale: 1000 } },
  skus: [{ sku_id: 'SKU-A', in_campaign: true, is_bundle: false, stock_start: 20, stock_unlimited: false,
    unit_cost_vnd: 10000, price_hourly: [20000], contribution_per_unit_hourly: [], forecast_mean_hourly: [5],
    forecast_id: 'forecast-1', forecast_label: null, dispersion_k: 5, sigma: 0.2, leftover_value_per_unit: 0,
    restock: [], economics_by_day: [{ sku_id: 'SKU-A', day: '2026-11-11', eff_price: 20000, price_source: 'sale', unit_cost: 10000, packaging_cost: 1000, fee_rate: 0.1, m: 7000, is_bundle: false }] }],
  actions: [{ key: 'continue', action: { kind: 'continue' },
    metrics: { objective: dist(75000), contribution: dist(100000), spend: dist(30000), leftover_value: dist(10000), stockout_penalty: dist(5000), units_sold: dist(30), focus_units_sold: dist(20) },
    reconciliation: { sum_components_mean: 75000, objective_mean: 75000, residual_vnd: 0 }, spend_hourly: [30000], per_sku: [{ sku_id: 'SKU-A', units_sold: dist(20), contribution: dist(100000) }] }],
  limitations: ['Không truy vết từng dòng CSV.'],
};
describe('calculation evidence', () => {
  it('uses supplied component values and warns not to add quantiles', () => {
    render(<ActionCalculation action={trace.actions[0]!} index={0} />);
    expect(screen.getByText(/100,000 ₫ − 30,000 ₫ \+ 10,000 ₫ − 5,000 ₫/)).toBeInTheDocument();
    expect(screen.getByText(/Reconciliation residual: 0 ₫/)).toBeInTheDocument();
    expect(screen.getByText(/Component P10\/P50\/P90 values cannot be added/)).toBeInTheDocument();
  });
  it('shows immutable source references and actual per-day contribution arithmetic without filling missing values with zero', () => {
    render(<CalculationEvidence trace={trace} />);
    expect(screen.getByText('don_hang.csv')).toBeInTheDocument();
    expect(screen.getByText('immutable-hash')).toBeInTheDocument();
    expect(screen.getByText('Whole campaign C1')).toBeInTheDocument();
    expect(screen.getByText('7,000 ₫')).toBeInTheDocument();
    expect(screen.getByText(/contribution\/unit Not provided/)).toBeInTheDocument();
    expect(screen.getByText('Không truy vết từng dòng CSV.')).toBeInTheDocument();
  });
  it('does not invent an explanation for a legacy result without a trace', () => {
    render(<CalculationEvidence trace={null} />);
    expect(screen.getByText(/No saved calculation trace is available/)).toBeInTheDocument();
    expect(screen.queryByText('Formulas and models')).not.toBeInTheDocument();
  });
});
