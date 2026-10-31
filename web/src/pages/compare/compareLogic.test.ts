import { ApiError } from '../../api/client';
import type { Recommendation, ScenarioRun } from '../../api/types';
import {
  blockingInfo,
  buildSimRequest,
  citationFromSource,
  clampLeftover,
  defaultDeadline,
  flipHits,
  fromLocalInput,
  goalText,
  headline,
  leftoverMax,
  leftoverMin,
  localZoneBar,
  noZoneBarText,
  outOfRangeMax,
  paramVersionText,
  pauseBlockedLine,
  splitFirstNumber,
  toLocalInput,
  webHref,
} from './compareLogic';
import { pickActive } from './useCompareData';

const rec: Recommendation = {
  recommendation_id: 'rec-1',
  campaign_id: 'CD002',
  status: 'recommend',
  best: { kind: 'schedule_hours', windows_local: [[0, 2], [12, 14]] },
  runner_up: { kind: 'continue' },
  p_best_beats_runner_up: 0.99,
  expected_gain_vnd: 4_300_000,
  zone_bar: null,
  flip_conditions: [{ factor: 'restock_delay_days', flip_at: -1, base_value: 0, unit: 'ngày', text: 'x' }],
  confidence: 'reference',
  reasons: [],
  inputs_ref: {
    forecast_ids: [],
    estimate_id: 'ie',
    param_version: { leftover_value_per_unit: 0 },
    goal_id: 'g',
    data_as_of: '2026-11-11T01:00:00+07:00',
    iroas_source: 'estimate',
  },
  active: true,
};

/** `request` echoed by the real POST /scenarios/run (M3-api §5). */
const echoed: Pick<ScenarioRun, 'request'> = {
  request: {
    campaign_id: 'CD002',
    start: '2026-11-11T00:00:00+07:00',
    horizon_hours: 24,
    goal_id: 'goal-01M65WR7G4',
    actions: null,
    param_overrides: {},
    samples: 1000,
    seed: 20261111,
    session_id: null,
  },
};

describe('compareLogic', () => {
  it('picks the figure with a unit, not the event name', () => {
    expect(splitFirstNumber('Ở giá 11.11 mỗi nồi chỉ lãi 9.280đ')?.number).toBe('9.280đ');
    expect(splitFirstNumber('Hàng sẽ hết khoảng 13:00: ads không bán thêm')?.number).toBe('13:00');
    expect(splitFirstNumber('vẫn bán khoảng 18–31 sp/giờ')?.number).toBe('18–31 sp/giờ');
    expect(splitFirstNumber('Không được tắt hẳn')).toBeNull();
    // Real reason text: keep a clock range together.
    expect(splitFirstNumber('Chỉ chạy theo khung giờ 00:00–02:00, 12:00–14:00 lợi hơn')?.number).toBe('00:00–02:00');
  });

  it('headline in Vietnamese with the gain in millions', () => {
    expect(headline(rec)).toBe('Chỉ chạy 0–2h, 12–14h. Lợi hơn tiếp tục khoảng 4,3 triệu đ.');
    expect(headline({ ...rec, status: 'none_meets_goal' })).toBeNull();
  });

  it('flip on restock only when the slider crosses flip_at in its direction', () => {
    expect(flipHits(rec, { leftoverValue: null, restockDelayDays: 0 })).toHaveLength(0);
    expect(flipHits(rec, { leftoverValue: null, restockDelayDays: 2 })).toHaveLength(0);
    expect(flipHits(rec, { leftoverValue: null, restockDelayDays: -1 })).toHaveLength(1);
  });
});

describe('buildSimRequest (POST /scenarios/run)', () => {
  it('re-sends the echoed request unchanged for the base case, so the API cache hits', () => {
    const body = buildSimRequest(echoed, rec, { leftoverValue: null, restockDelayDays: 0 }, 1000);
    expect(body).toEqual({ ...echoed.request, param_overrides: {} });
  });

  it('adds 05 §3 overrides and the slider sample count on top of the echo', () => {
    const body = buildSimRequest(echoed, rec, { leftoverValue: 20_000, restockDelayDays: -1 }, 300);
    expect(body).toMatchObject({
      campaign_id: 'CD002',
      goal_id: 'goal-01M65WR7G4',
      start: '2026-11-11T00:00:00+07:00',
      horizon_hours: 24,
      samples: 300,
      seed: 20261111,
      param_overrides: { leftover_value_per_unit: 20_000, restock_delay_days: -1 },
    });
  });

  it('keeps the overrides of the echoed run; the sliders win on their keys', () => {
    const whatIf = { request: { ...echoed.request, param_overrides: { leftover_value_per_unit: 20_000, 'restock_delay_days@NOI': 2 } } };
    expect(buildSimRequest(whatIf, rec, { leftoverValue: null, restockDelayDays: 0 }, 1000).param_overrides).toEqual({
      leftover_value_per_unit: 20_000,
      'restock_delay_days@NOI': 2,
    });
    expect(buildSimRequest(whatIf, rec, { leftoverValue: 0, restockDelayDays: 1 }, 1000).param_overrides).toEqual({
      leftover_value_per_unit: 0,
      'restock_delay_days@NOI': 2,
      restock_delay_days: 1,
    });
  });

  it('without an echo lets the backend fill start/horizon from the goal', () => {
    const body = buildSimRequest(undefined, rec, { leftoverValue: null, restockDelayDays: 3 }, 300);
    expect(body).toEqual({
      campaign_id: 'CD002',
      goal_id: 'g',
      actions: null,
      samples: 300,
      seed: 20261111,
      param_overrides: { restock_delay_days: 3 },
    });
    expect(body).not.toHaveProperty('start');
  });
});

describe('leftover-value slider bounds (V ≤ selling price)', () => {
  it('uses price_max_vnd from GET /campaigns/{id}', () => {
    expect(leftoverMax({ price_max_vnd: 690_000 }, rec, false)).toBe(690_000);
    expect(leftoverMax(undefined, rec, false)).toBe(1_000_000);
    expect(clampLeftover(900_000, 690_000)).toBe(690_000);
    expect(clampLeftover(-5, 690_000)).toBe(0);
    const campaign = {
      sku_detail: [
        { sku: 'NOI', name: '', variant: '', is_bundle: false, list_price_vnd: 690_000, price_effective_vnd: 690_000, stock_available: 83, unit_cost_vnd: 455_000 },
        { sku: 'COMBO', name: '', variant: '', is_bundle: true, list_price_vnd: 719_000, price_effective_vnd: 719_000, stock_available: 83, unit_cost_vnd: 473_000 },
      ],
    };
    expect(leftoverMin(campaign, false)).toBe(-455_000);
    expect(leftoverMin(campaign, true)).toBe(0);
    expect(clampLeftover(-900_000, 690_000, -455_000)).toBe(-455_000);
  });

  it('reads details.max of a 400 PARAM_OUT_OF_RANGE', () => {
    const err = new ApiError(400, 'PARAM_OUT_OF_RANGE', 'x', { max: 690_000 });
    expect(outOfRangeMax(err)).toBe(690_000);
    expect(outOfRangeMax(new Error('x'))).toBeNull();
  });
});

describe('no zone bar, blocked runs, goals', () => {
  it('explains a missing bar by its cause', () => {
    const hold = { ...rec, reasons: ['Giữ hàng lợi hơn bán: mỗi sp còn lại đáng 56.725đ, bán chỉ lãi 12.585đ'] };
    expect(noZoneBarText(hold)).toBe(
      'Không vẽ thanh vùng vì giữ hàng lợi hơn bán ở giá sale (mỗi sp còn lại đáng 56.725đ, bán chỉ lãi 12.585đ): về tiền, tạm dừng luôn hơn tiếp tục.',
    );
    expect(noZoneBarText({ ...rec, best: null })).toBe('Chưa có thanh vùng vì chưa so sánh được các hành động.');
    expect(noZoneBarText(rec)).toMatch(/^Chưa có thanh vùng cho đề xuất này/);
  });

  it('says pause is blocked when holding stock wins but pause breaks the commitment', () => {
    const results = [{ action: { kind: 'pause' }, feasible: false, violations: ['campaign_must_run'] }] as never;
    expect(pauseBlockedLine(null, results, true)).toMatch(/không được tắt hẳn/);
    expect(pauseBlockedLine(null, results, false)).toBeNull();
  });

  it('maps 422 BLOCKING_DATA_ISSUE to "missing goal" or "missing data"', () => {
    const goal = new ApiError(422, 'BLOCKING_DATA_ISSUE', 'Chiến dịch CD006 chưa có mục tiêu.', { missing: 'goal' });
    expect(blockingInfo(goal)).toEqual({ kind: 'goal', message: 'Chiến dịch CD006 chưa có mục tiêu.' });
    const data = new ApiError(422, 'BLOCKING_DATA_ISSUE', 'Thiếu giá vốn', { reasons: ['Thiếu giá vốn: THAM-XANH', 7] });
    expect(blockingInfo(data)).toEqual({ kind: 'data', message: 'Thiếu giá vốn', reasons: ['Thiếu giá vốn: THAM-XANH'] });
    expect(blockingInfo(new ApiError(409, 'CONSTRAINT_VIOLATION', 'x'))).toBeNull();
  });

  it('goal label from the campaign (the API includes the deadline)', () => {
    const goal = {
      goal_id: 'g',
      kind: 'profit',
      label: 'Lợi nhuận tới 00:00 12/11',
      deadline: '2026-11-12T00:00:00+07:00',
      target_units: null,
      confirmed_by: 'u-lead-1',
      version: 1,
    };
    expect(goalText(goal, 'g')).toBe('Lợi nhuận tới 00:00 12/11');
    expect(goalText(goal, 'other')).toBe('other');
    expect(goalText(null, 'g')).toBe('g');
  });

  it('default deadline is the next VN midnight after the data clock', () => {
    expect(defaultDeadline('2026-11-11T01:00:00+07:00')).toBe('2026-11-11T17:00:00Z');
    expect(toLocalInput('2026-11-11T17:00:00Z')).toBe('2026-11-12T00:00');
    expect(fromLocalInput('2026-11-12T00:00')).toBe('2026-11-12T00:00:00+07:00');
  });

  it('param_version object → short text', () => {
    expect(paramVersionText({ a: 0, b: 0 })).toBe('2 tham số, đều là mặc định (v0)');
    expect(paramVersionText({ a: 0, leftover_value_per_unit: -1, b: 3 })).toBe('leftover_value_per_unit: giả định của lần chạy · b v3');
  });
});

describe('citations from real Source.href (API paths)', () => {
  it('maps API paths to app routes', () => {
    expect(webHref('/api/v1/skus/NOICHIEN2L/forecast')).toBe('/sku/NOICHIEN2L');
    expect(webHref('/api/v1/skus/NOICHIEN2L')).toBe('/sku/NOICHIEN2L');
    expect(webHref('/api/v1/scenarios/scn-1')).toBe('#tier-3');
    expect(webHref('/api/v1/campaigns/CD002')).toBe('#tier-3');
    expect(webHref('/api/v1/unknown')).toBeNull();
    expect(webHref(null)).toBeNull();
  });

  it('keeps tool and data time of the source', () => {
    const c = citationFromSource({
      id: 'fc-1',
      kind: 'forecast',
      label: 'Dự báo nhu cầu NOICHIEN2L',
      data_as_of: '2026-11-11T00:00:00+07:00',
      tool: 'get_forecast',
      href: '/api/v1/skus/NOICHIEN2L/forecast',
    });
    expect(c).toMatchObject({ kind: 'forecast', tool: 'get_forecast', href: '/sku/NOICHIEN2L', dataAsOf: '2026-11-11T00:00:00+07:00' });
  });
});

describe('pickActive (GET /recommendations?campaign_id=)', () => {
  it('filters by campaign (fixtures ignore the query) and skips superseded ones', () => {
    const list = [
      { ...rec, recommendation_id: 'old', superseded_by: 'new' },
      { ...rec, recommendation_id: 'new' },
      { ...rec, recommendation_id: 'other', campaign_id: 'CD005' },
    ];
    expect(pickActive(list, 'CD002')?.recommendation_id).toBe('new');
    expect(pickActive(list, 'CD009')).toBeUndefined();
  });
});

describe('pause not allowed (05 §7.2, M3-mo-phong Q14)', () => {
  const inputs = {
    units_left: 120,
    hours_left: 4,
    contribution_per_unit_vnd: 45_000,
    spend_per_hour_vnd: 450_000,
    rate_continue_per_hour: 40,
    pause_rate_interval: [18, 24, 31] as [number, number, number],
    leftover_value_per_unit_vnd: 0,
  };
  const blockedBar = {
    axis_label: 'Lượng bán mỗi giờ nếu pause (sp)',
    threshold: 22.5,
    interval: [18, 24, 31] as [number, number, number],
    verdict: 'undetermined' as const,
    alt_thresholds: [],
    inputs,
    pause_feasible: false,
    pause_violations: ['campaign_must_run'],
    verdict_note: 'Tạm dừng bị cấm vì đã cam kết với sàn trong khung flash sale: thanh chỉ để so sánh',
  };

  it('the browser estimate keeps pause_feasible=false and rewrites the note for the new verdict', () => {
    const { bar, estimated } = localZoneBar({ ...rec, zone_bar: blockedBar }, { leftoverValue: 20_000, restockDelayDays: 0 });
    expect(estimated).toBe(true);
    expect(bar?.verdict).toBe('pause_better');
    expect(bar?.pause_feasible).toBe(false);
    expect(bar?.verdict_note).toMatch(/^Tạm dừng bị cấm vì đã cam kết với sàn trong khung flash sale. Về tiền, tạm dừng lợi hơn chạy tiếp/);
  });

  it('no duplicate "pause blocked" line when the API sends verdict_note', () => {
    const results = [{ action: { kind: 'pause' }, feasible: false, violations: ['campaign_must_run'] }] as never;
    expect(pauseBlockedLine({ ...blockedBar, verdict: 'pause_better' }, results)).toBeNull();
  });

  it('headline when only the best action meets the goal (goal_cost_vnd)', () => {
    const r = { ...rec, runner_up_meets_goal: false, goal_cost_vnd: 1_300_000, p_best_beats_runner_up: null, expected_gain_vnd: null };
    expect(headline(r)).toBe('Chỉ chạy 0–2h, 12–14h. Chỉ phương án này đạt mục tiêu; tiếp tục lợi hơn khoảng 1,3 triệu đ nhưng không đạt.');
  });
});
