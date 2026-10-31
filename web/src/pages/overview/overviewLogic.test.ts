import type { SkuListItem } from '../../api/types';
import { coverText, groupAttention, lastComputed, needsAttention, staleSources, stockoutAt } from './overviewLogic';

/** Shape of a real GET /skus row (M3-api §5). */
function row(over: Partial<SkuListItem>): SkuListItem {
  return {
    sku: 'NOICHIEN2L',
    name: 'Nồi chiên không dầu mini 2L',
    variant: '',
    product_id: 'NOICHIEN2L',
    is_bundle: false,
    demand_group: 'on_dinh',
    stock_available: 83,
    hours_of_cover: { p10: 12, p50: 17, p90: 35, horizon_hours: 72 },
    sold_today_units: 36,
    sold_3h_units: 10,
    deviation: null,
    campaign: { id: 'CD002', status: 'Đang chạy', status_code: 'running' },
    recommendation: null,
    open_alerts: 0,
    priority_score: 21.67,
    confidence: 'reference',
    forecast_id: 'fc-1',
    compute_state: 'fresh',
    computed_at: '2026-10-30T14:47:02+07:00',
    ...over,
  };
}

const schedule = {
  recommendation_id: 'rec-1',
  status: 'recommend' as const,
  best: { kind: 'schedule_hours' as const, pct: null, windows_local: [[0, 2], [12, 14]] as [number, number][] },
  confidence: 'reference' as const,
  scenario_id: 'scn-1',
  created_at: null,
};

describe('Tổng quan ca · needsAttention / groupAttention', () => {
  it('needs attention: open alert, non-continue recommendation, none_meets_goal', () => {
    expect(needsAttention(row({}))).toBe(false);
    expect(needsAttention(row({ open_alerts: 1 }))).toBe(true);
    expect(needsAttention(row({ recommendation: schedule }))).toBe(true);
    expect(needsAttention(row({ recommendation: { ...schedule, best: { kind: 'continue' } } }))).toBe(false);
    expect(needsAttention(row({ recommendation: { ...schedule, status: 'none_meets_goal', best: null } }))).toBe(true);
    // A stocked-out SKU with no alert / recommendation is not in the block (it is first in the table).
    expect(needsAttention(row({ stock_available: 0, priority_score: 50 }))).toBe(false);
  });

  it('one card per decision: variants and campaign siblings sharing a recommendation merge', () => {
    const recAo = { ...schedule, recommendation_id: 'rec-AO' };
    const recNoi = { ...schedule, recommendation_id: 'rec-NOI' };
    const items = [
      row({ sku: 'AO-S', name: 'Áo giữ nhiệt nam', variant: 'Size S', product_id: 'AO', priority_score: 10, recommendation: recAo }),
      row({ sku: 'AO-M', name: 'Áo giữ nhiệt nam', variant: 'Size M', product_id: 'AO', priority_score: 30, recommendation: recAo }),
      row({ sku: 'NOI', product_id: 'NOI', priority_score: 20, recommendation: recNoi }),
      row({ sku: 'GIAYLOT', product_id: 'GIAYLOT', priority_score: 5, recommendation: recNoi }),
      row({ sku: 'X', product_id: 'X', priority_score: 99 }),
      row({ sku: 'Y', product_id: 'Y', priority_score: 1, open_alerts: 2 }),
    ];
    const groups = groupAttention(items, 5);
    expect(groups.map((g) => g.key)).toEqual(['rec-AO', 'rec-NOI', 'product:Y']);
    expect(groups[0]).toMatchObject({ sameProduct: true, lead: { sku: 'AO-M' } });
    expect(groups[0]?.items.map((i) => i.sku)).toEqual(['AO-M', 'AO-S']);
    expect(groups[1]).toMatchObject({ sameProduct: false });
    expect(groupAttention(items, 1)).toHaveLength(1);
  });
});

describe('Tổng quan ca · cover and stock-out time', () => {
  it('cover p50, or "> horizon" when it does not run out (p50 = null)', () => {
    expect(coverText(row({}))).toBe('17');
    expect(coverText(row({ hours_of_cover: { p10: 40, p50: null, p90: null, horizon_hours: 72 } }))).toBe('> 72');
    expect(coverText(row({ hours_of_cover: null }))).toBe('—');
  });

  it('stock-out clock = data time + p50 hours', () => {
    const at = stockoutAt('2026-11-11T00:00:00+07:00', row({ hours_of_cover: { p50: 14, horizon_hours: 72 } }));
    expect(at?.toISOString()).toBe('2026-11-11T07:00:00.000Z');
    expect(stockoutAt('2026-11-11T00:00:00+07:00', row({ stock_available: 0 }))).toBeNull();
    expect(stockoutAt(null, row({}))).toBeNull();
  });

  it('stale banner sources and last computed time', () => {
    const sources = [
      { source: 'orders', label: 'Đơn hàng', as_of: null, status: 'fresh' as const },
      { source: 'inventory', label: 'Inventory', as_of: null, status: 'stale' as const },
      { source: 'cost', label: 'Cost', as_of: null, status: 'partial' as const },
    ];
    expect(staleSources(sources).map((s) => s.source)).toEqual(['inventory']);
    expect(lastComputed([{ computed_at: '2026-10-30T14:47:02+07:00' }, { computed_at: '2026-10-30T15:00:00+07:00' }, { computed_at: null }])).toBe(
      '2026-10-30T15:00:00+07:00',
    );
  });
});
