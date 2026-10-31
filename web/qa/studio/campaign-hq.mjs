// Browser integration fixture. No calls to production, Python or paid AI.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { chromium, webkit } from 'playwright';
const base = process.env.STUDIO_QA_URL || 'http://127.0.0.1:5186';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Local QA only');
const out = new URL('../../../qa/runs/campaign-hq/', import.meta.url);
await mkdir(out, { recursive: true });
const d = mean => ({ mean, p10: mean * .8, p50: mean, p90: mean * 1.2 });
const start = '2026-11-11T15:00:00+07:00';
const end = '2026-11-12T00:00:00+07:00';
const goal = { goal_id: 'goal-demo', version: 1, kind: 'profit', deadline: end, target_units: null, confirmed_by: 'lead', params: { planner_start: start } };
const skus = ['NOICHIEN2L', 'GIAYLOT50', 'COMBO-NOICHIEN'].map((sku_id, i) => ({
  sku_id, in_campaign: true, is_bundle: i === 2, stock_start: i === 2 ? null : 83, stock_unlimited: false,
  unit_cost_vnd: 480000, price_hourly: [600000], contribution_per_unit_hourly: [45000], forecast_mean_hourly: [8],
  forecast_id: 'fc-demo', forecast_label: 'scenario', dispersion_k: 3, sigma: .2, leftover_value_per_unit: 0, restock: [],
  economics_by_day: [{ sku_id, day: '2026-11-11', eff_price: 600000, price_source: 'qa', unit_cost: 480000, packaging_cost: 3000, fee_rate: .12, m: 45000, is_bundle: i === 2 }],
}));
const actions = ['continue', 'pause'].map((key, i) => ({ key, action: { kind: key },
  metrics: { objective: d(i ? 2835000 : 2535000), contribution: d(i ? 2835000 : 3735000), spend: d(i ? 0 : 1200000),
    revenue: d(i ? 37800000 : 49800000), selling_cost: d(i ? 34965000 : 46065000), after_ads: d(i ? 2835000 : 2535000),
    leftover_value: d(0), stockout_penalty: d(0), units_sold: d(i ? 63 : 83), focus_units_sold: null },
  reconciliation: { sum_components_mean: i ? 2835000 : 2535000, objective_mean: i ? 2835000 : 2535000, residual_vnd: 0 }, spend_hourly: [i ? 0 : 1200000],
  per_sku: skus.map(s => ({ sku_id: s.sku_id, units_sold: d(i ? 21 : 83 / 3), contribution: d(i ? 945000 : 1245000), leftover_units: s.is_bundle ? null : d(i ? 20 : 0) })),
}));
const scenario = { scenario_id: 'qa-scenario', input_hash: 'qa', blocked: false,
  request: { campaign_id: 'CD002', goal_id: goal.goal_id, start, horizon_hours: 9, samples: 1000, seed: 42 },
  calculation_trace: { version: 1, input_hash: 'qa', campaign_id: 'CD002', focus_sku: null, quantity_scope: 'campaign', economic_scope: 'campaign', start, horizon_hours: 9, samples: 1000, seed: 42, data_as_of: start, goal, panel_version: 'qa', skus, actions, equations: [], limitations: ['Dữ liệu QA minh họa, không phải dự báo CD002.'], sources: { batches: [], forecasts: [], parameters: [], iroas: { source: 'prior', estimate_id: null, confidence: 'scenario', delta: .6, spend_scale: 100000 } } },
  results: actions.map(a => ({ key: a.key, action: a.action, feasible: true, goal_eligible: true, violations: [], spend: a.metrics.spend, p_target: null })),
  recommendation: { status: 'recommend', best: { kind: 'pause' }, reasons: ['Trong ví dụ QA, phần đóng góp tăng thêm chưa bù chi ads.'], flip_conditions: [], confidence: 'scenario' },
};
let rev = 'r1';
const ctx = () => ({ campaign: { campaign_id: 'CD002', name: 'Nồi chiên mini + giấy lót', daily_budget_vnd: 1864000, spend_today_vnd: 500000, sku_detail: skus.map(s => ({ sku: s.sku_id, name: s.sku_id, is_bundle: s.is_bundle, price_effective_vnd: 600000, stock_available: 83 })) }, goal, revision: rev, dataAsOf: start, sources: { orders: start, inventory: start, ads: '2026-11-11T13:00:00+07:00' }, canRun: true, history: [] });
const engine = process.env.QA_ENGINE === 'webkit' ? webkit : chromium;
const browser = await engine.launch({ headless: true, ...(engine === chromium ? { channel: process.env.STUDIO_QA_CHANNEL } : {}) });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const login = await context.request.post(base + '/api/auth/login', { headers: { Origin: base }, data: { username: 'admin', password: (await readFile('/tmp/megaday-qa-password', 'utf8')).trim() } });
  assert(login.ok(), 'Local login failed');
  await context.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    if (path === '/api/v1/campaign-analysis/CD002') return route.fulfill({ json: ctx() });
    if (path.endsWith('/refresh')) return route.fulfill({ json: { revision: rev, scenario, goal, end, progress: null } });
    if (path.endsWith('/explain')) return route.fulfill({ json: { scenarioId: scenario.scenario_id, method: 'rules', reasons: scenario.recommendation.reasons, cautions: [] } });
    if (path === '/api/pm/models') return route.fulfill({ json: { models: [], selected: null } });
    const docs = { hq: { tracked: [], active: null, worksheets: {}, activity: [] }, sources: { schema: 'megaday.sources.v1', files: [] }, settings: { signals: false, motion: false, quality: 'balanced', platforms: ['shopee'] }, sandbox: { hour: 15, event: 'observe' }, chats: { sessions: [], preferredModel: '' } };
    return route.fulfill({ json: { revision: 1, updatedAt: start, data: docs[path.split('/').pop()] ?? {} } });
  });
  const page = await context.newPage();
  await page.routeWebSocket('**/api/workspace/events', socket => socket.onMessage(m => { if (m === 'ping') socket.send('pong'); }));
  const errors = []; page.on('pageerror', e => { errors.push(e.message); console.error(e.message); });
  await page.goto(`${base}/campaign-hq`);

  await page.getByRole('button', { name: 'Cập nhật & đề xuất lại', exact: true }).click();
  await page.getByRole('heading', { name: 'Tiếp tục hay tạm dừng quảng cáo?' }).waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1100 });
    await page.getByRole('heading', { name: 'Kinh tế từng sản phẩm' }).scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.innerWidth + 1), `Overflow ${width}`);
    await page.screenshot({ path: new URL(`tables-${width}.png`, out).pathname.replace(/^\/(\w:)/, '$1'), fullPage: true });
    await page.locator('.campaign-card').nth(1).evaluate(el => el.scrollIntoView({block: 'start'}));
    await page.screenshot({ path: new URL(`comparison-${width}.png`, out).pathname.replace(/^\/(\w:)/, '$1') });
  }
  rev = 'r2';
  await page.getByText('Có dữ liệu hoặc mục tiêu mới chưa được tính vào bản đang xem.', { exact: true }).waitFor({ timeout: 20000 });
  await page.getByRole('button', { name: 'Milo notifications' }).click();
  await page.getByRole('link', { name: 'Mở campaign để cập nhật & đề xuất lại' }).waitFor();
  assert.deepEqual(errors, []);
  await writeFile(new URL('results.json', out), JSON.stringify({ mode: 'mock APIs, real browser', checks: ['two tables', 'desktop/mobile', 'backend revision -> stale analysis + Milo notification'], errors }, null, 2));
  console.log('Campaign HQ browser checks passed.');
} finally { await browser.close(); }
