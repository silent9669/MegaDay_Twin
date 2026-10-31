// Real browser, stubbed account and planner APIs; never writes production data.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const base = process.env.STUDIO_QA_URL || 'http://127.0.0.1:5186';
const out = new URL('../../../qa/runs/sale-planner/', import.meta.url);
await mkdir(out, { recursive: true });
const sources = JSON.parse(await readFile(new URL('../../server/repository-sources.json', import.meta.url), 'utf8'));
const browser = await chromium.launch({ headless: true, channel: process.env.STUDIO_QA_CHANNEL });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const docs = { sources, settings: { signals: false, motion: false, quality: 'balanced', platforms: ['shopee'] }, sandbox: { hour: 1, event: 'observe' }, chats: { preferredModel: '', sessions: [] } };
  await context.route('**/api/workspace/*', (route) => route.fulfill({ json: { revision: 1, updatedAt: new Date().toISOString(), data: docs[new URL(route.request().url()).pathname.split('/').pop()] } }));
  let configured = false;
  const attempts = [];
  const draft = { text: 'Bán 100 sản phẩm, ngân sách tối đa 2 triệu đồng.', kind: 'sell_target', targetUnits: 100, maxSpendVnd: 2000000, maxLossVnd: null, sellTargetProbability: 0.8, minMarginalReturn: null, start: '2026-11-11T08:00:00+07:00', deadline: '2026-11-11T20:00:00+07:00', unresolved: ['Tự tạo voucher chưa được hỗ trợ'] };
  const dist = { mean: 12345, p10: 1000, p50: 10000, p90: 30000 };
  await context.route('**/api/sale-planner/*', (route) => {
    const endpoint = new URL(route.request().url()).pathname.split('/').pop();
    if (endpoint === 'context') return route.fulfill({ json: { backendConfigured: configured, canCreateGoals: configured, referenceTime: '2026-11-11T00:00:00Z', dataAsOf: '2026-11-11T00:00:00Z', campaigns: configured ? [{ id: 'CD002', name: 'Chiến dịch QA' }] : [], notice: null } });
    if (endpoint === 'interpret') return route.fulfill({ json: { draft, method: 'rules', notices: [] } });
    attempts.push(route.request().postDataJSON());
    if (attempts.length === 1) return route.fulfill({ status: 422, json: { goalId: 'goal-qa', stage: 'simulation', error: { message: 'Thiếu dữ liệu tồn kho', code: 'BLOCKING_DATA_ISSUE' } } });
    return route.fulfill({ json: { goalId: 'goal-qa', scenario: { data: { scenario_id: 'scenario-qa', blocked: false, cache_hit: false, request: { campaign_id: 'CD002', goal_id: 'goal-qa', start: draft.start, horizon_hours: 12, samples: 1000, seed: 123 }, recommendation: null, results: [{ key: 'continue', action: { kind: 'continue' }, feasible: true, goal_eligible: true, violations: [], objective: dist, contribution: dist, spend: dist, units_sold: { ...dist, mean: 104 }, leftover_units: dist, leftover_value: dist, p_target: 0.87 }] } } } });
  });
  const page = await context.newPage();
  await page.routeWebSocket('**/api/workspace/events', (socket) => socket.onMessage((message) => { if (message === 'ping') socket.send('pong'); }));
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`${base}/sale-planner`);
  await page.getByText('Chưa kết nối backend tính toán', { exact: true }).waitFor();
  await page.getByLabel('Mô tả mục tiêu', { exact: true }).fill(draft.text);
  await page.getByRole('button', { name: 'Phân tích mục tiêu', exact: true }).click();
  const confirm = page.getByRole('checkbox', { name: /Tôi xác nhận các thông số trên/ });
  const acknowledge = page.getByRole('checkbox', { name: /Tôi đã kiểm tra và đồng ý/ });
  const run = page.getByRole('button', { name: 'Lưu mục tiêu và chạy mô phỏng', exact: true });
  await acknowledge.waitFor();
  assert.equal(await confirm.isDisabled(), true);
  await acknowledge.check(); await confirm.check();
  assert.equal(await run.isDisabled(), true);
  assert.equal(attempts.length, 0);
  configured = true;
  await page.getByRole('button', { name: 'Kiểm tra lại', exact: true }).click();
  await page.getByText('Chiến dịch từ backend', { exact: true }).waitFor();
  await page.getByLabel('Chiến dịch', { exact: true }).selectOption('CD002');
  await confirm.check();
  await page.getByLabel('Ngân sách quảng cáo tối đa (₫)', { exact: true }).fill('1500000');
  assert.equal(await confirm.isChecked(), false);
  assert.equal(await acknowledge.isChecked(), false);
  await acknowledge.check(); await confirm.check(); await run.click();
  await page.getByText('Thiếu dữ liệu tồn kho', { exact: true }).waitFor();
  assert.equal(attempts[0].draft.maxSpendVnd, 1500000);
  assert.equal(attempts[0].confirmed, true);
  await page.getByRole('button', { name: 'Thử lại mô phỏng', exact: true }).click();
  await page.getByRole('heading', { name: 'Phương án cho Chiến dịch QA' }).waitFor();
  assert.equal(attempts[1].goalId, 'goal-qa');
  await page.locator('.planner-metric > summary').first().click();
  await page.getByText('results[0].objective', { exact: true }).waitFor();
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.getByRole('heading', { name: 'Mục tiêu buổi sale', exact: true }).scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.innerWidth + 1), true, `Overflow at ${width}px`);
    await page.screenshot({ path: fileURLToPath(new URL(`planner-${width}.png`, out)), fullPage: true });
    await page.locator('#planner-review-title').scrollIntoViewIfNeeded();
    await page.screenshot({ path: fileURLToPath(new URL(`review-${width}.png`, out)) });
    await page.locator('#planner-results-title').scrollIntoViewIfNeeded();
    await page.screenshot({ path: fileURLToPath(new URL(`results-${width}.png`, out)) });
  }
  await page.getByLabel('Ngân sách quảng cáo tối đa (₫)', { exact: true }).fill('1000000');
  assert.equal(await page.getByRole('heading', { name: 'Phương án cho Chiến dịch QA' }).count(), 0);
  assert.deepEqual(errors, []);
  const results = ['Unconfigured backend cannot simulate', 'Interpretation never saves or simulates', 'Explicit review, exclusion acknowledgement and confirmation; edits invalidate approval', 'Simulation failure retains goal; retry reuses saved goal', 'Returned metrics expand to show provenance; edits clear stale results', 'No horizontal overflow at 1440/768/390/320px; no page errors'];
  await writeFile(new URL('results.json', out), JSON.stringify({ mode: 'real browser with stubbed APIs', results }, null, 2));
  console.log(results.join('\n'));
} finally { await browser.close(); }
