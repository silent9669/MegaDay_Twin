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
  const attempts = [], applies = [];
  const draft = { text: 'Bán 100 sản phẩm, ngân sách tối đa 2 triệu đồng.', kind: 'sell_target', targetUnits: 100, maxSpendVnd: 2000000, maxLossVnd: null, sellTargetProbability: 0.8, minMarginalReturn: null, start: '2026-11-11T08:00:00+07:00', deadline: '2026-11-11T20:00:00+07:00', unresolved: ['Tự tạo voucher chưa được hỗ trợ'] };
  const dist = (mean) => ({ mean, p10: mean * 0.8, p50: mean, p90: mean * 1.2 });
  let activeGoal = { goal_id: 'active-qa', scope: 'campaign', scope_id: 'CD002', kind: 'profit', target_units: null, deadline: draft.deadline, params: { max_spend_vnd: 5000000, max_loss_vnd: 0 }, active: true, version: 4 };
  const baseGoal = structuredClone(activeGoal);
  const effectiveGoal = { ...activeGoal, goal_id: 'preview-goal', kind: draft.kind, target_units: 100, params: { max_spend_vnd: 1500000, max_loss_vnd: 0 }, active: false, version: 1 };
  const trace = {
    version: 1, input_hash: 'qa-campaign-input-snapshot', campaign_id: 'CD002', focus_sku: null,
    quantity_scope: 'campaign', economic_scope: 'campaign', start: draft.start,
    horizon_hours: 12, samples: 1000, seed: 123, data_as_of: '2026-11-11T00:00:00Z', panel_version: 'qa-panel',
    goal: { kind: effectiveGoal.kind, target_units: effectiveGoal.target_units, params: effectiveGoal.params },
    equations: [{ id: 'objective', formula: 'J = contribution - spend + leftover_value - stockout_penalty', explanation: 'Tính từ cùng các mẫu mô phỏng.' }],
    sources: {
      batches: [{ batch_id: 'batch-qa', source_type: 'orders', file_name: 'qa-orders.csv', file_sha256: 'qa-sha256', as_of: '2026-11-11T00:00:00Z' }],
      forecasts: [], parameters: [{ key: 'platform_fee_rate', value: 0.12, version: 1, scope: 'global', scope_id: '*', set_by: 'QA lead' }],
      iroas: { source: 'prior', estimate_id: null, confidence: 'scenario', delta: 0.5, spend_scale: 100000 },
    },
    skus: [{ sku_id: 'SKU-QA', in_campaign: true, is_bundle: false, stock_start: 200, stock_unlimited: false,
      unit_cost_vnd: 60000, price_hourly: Array(12).fill(100000), contribution_per_unit_hourly: Array(12).fill(25000),
      forecast_mean_hourly: Array(12).fill(8), forecast_id: null, forecast_label: 'scenario', dispersion_k: 3, sigma: 0.2,
      leftover_value_per_unit: 0, restock: [], economics_by_day: [{ sku_id: 'SKU-QA', day: '2026-11-11', eff_price: 100000,
        price_source: 'list_price', unit_cost: 60000, packaging_cost: 3000, fee_rate: 0.12, m: 25000, is_bundle: false }] }],
    actions: [{ key: 'continue', action: { kind: 'continue' }, metrics: {
      objective: dist(1400000), contribution: dist(2600000), spend: dist(1200000), leftover_value: dist(0),
      stockout_penalty: dist(0), units_sold: dist(104), focus_units_sold: null,
    }, reconciliation: { sum_components_mean: 1400000, objective_mean: 1400000, residual_vnd: 0 },
    spend_hourly: Array(12).fill(100000), per_sku: [{ sku_id: 'SKU-QA', units_sold: dist(104), contribution: dist(2600000) }] }],
    limitations: ['Dữ liệu giả lập cho kiểm tra giao diện; không phải kết quả kinh doanh.', 'Nguồn batch không phải truy vết từng dòng CSV.'],
  };
  await context.route('**/api/sale-planner/*', (route) => {
    const endpoint = new URL(route.request().url()).pathname.split('/').pop();
    if (endpoint === 'context') return route.fulfill({ json: { backendConfigured: configured, canCreateGoals: configured, canPreview: configured, canApply: configured, referenceTime: '2026-11-11T00:00:00Z', dataAsOf: '2026-11-11T00:00:00Z', campaigns: configured ? [{ id: 'CD002', name: 'Chiến dịch QA' }] : [], notice: null } });
    if (endpoint === 'active') return route.fulfill({ json: { campaignId: 'CD002', activeGoal, baseGoal: { goal_id: activeGoal.goal_id, version: activeGoal.version } } });
    if (endpoint === 'apply') { applies.push(route.request().postDataJSON()); activeGoal = { ...effectiveGoal, active: true, version: 5 }; return route.fulfill({ json: { previewId: 'preview-qa', goal: activeGoal, alreadyApplied: false } }); }
    if (endpoint === 'interpret') return route.fulfill({ json: { draft, method: 'rules', notices: [] } });
    attempts.push(route.request().postDataJSON());
    if (attempts.length === 1) return route.fulfill({ status: 422, json: { goalId: null, stage: 'preview', error: { message: 'Thiếu dữ liệu tồn kho', code: 'BLOCKING_DATA_ISSUE' } } });
    return route.fulfill({ json: { previewId: 'preview-qa', goalId: 'preview-goal', effectiveGoal, baseGoal, changes: [{ field: 'max_spend_vnd', before: 5000000, after: 1500000 }], notices: ['Giữ giới hạn lỗ của chiến dịch.'], canApply: true, appliedGoalId: null, scenario: { data: { scenario_id: 'scenario-qa', blocked: false, cache_hit: false, input_hash: trace.input_hash, calculation_trace: trace, request: { campaign_id: 'CD002', goal_id: 'preview-goal', start: draft.start, horizon_hours: 12, samples: 1000, seed: 123 }, recommendation: null, results: [{ key: 'continue', action: { kind: 'continue' }, feasible: true, goal_eligible: true, violations: [], objective: dist(1400000), contribution: dist(2600000), spend: dist(1200000), units_sold: dist(104), leftover_units: dist(96), leftover_value: dist(0), p_target: 0.87 }] } } } });
  });
  const page = await context.newPage();
  await page.routeWebSocket('**/api/workspace/events', (socket) => socket.onMessage((message) => { if (message === 'ping') socket.send('pong'); }));
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`${base}/sale-planner`);
  await page.getByText('Chưa kết nối backend tính toán', { exact: true }).waitFor();
  await page.getByLabel('Mô tả mục tiêu', { exact: true }).fill(draft.text);
  await page.getByRole('button', { name: 'Phân tích mục tiêu', exact: true }).click();
  const confirm = page.getByRole('checkbox', { name: /Tôi xác nhận các thông số để mô phỏng thử/ });
  const acknowledge = page.getByRole('checkbox', { name: /Tôi đã kiểm tra và đồng ý/ });
  const run = page.getByRole('button', { name: 'Mô phỏng thử', exact: true });
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
  await run.click();
  await page.getByRole('heading', { name: 'Phương án cho Chiến dịch QA' }).waitFor();
  assert.equal(attempts[1].goalId, undefined);
  assert.deepEqual(attempts[1].expectedGoal, { goal_id: 'active-qa', version: 4 });
  assert.equal(applies.length, 0);
  assert.equal(activeGoal.goal_id, 'active-qa');
  assert.equal(await page.getByRole('button', { name: 'Áp dụng mục tiêu', exact: true }).isDisabled(), true);
  await page.locator('.planner-metric > summary').first().click();
  await page.getByText('results[0].objective', { exact: true }).waitFor();
  await page.locator('.planner-calculation > summary').click();
  await page.getByText(/Sai lệch đối chiếu: 0 ₫/).waitFor();
  await page.locator('#calculation-evidence > summary').click();
  await page.getByText('File nguồn và lần nhập dữ liệu', { exact: true }).click();
  await page.getByText('qa-orders.csv', { exact: true }).waitFor();
  assert.equal(await page.getByText(/Lần tính này chưa có bản truy vết/).count(), 0);
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.getByRole('heading', { name: 'Lập kế hoạch sale', exact: true }).scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.innerWidth + 1), true, `Overflow at ${width}px`);
    const changeNumbers = await page.locator('.planner-change-number').evaluateAll((values) => values.map((value) => {
      const range = globalThis.document.createRange(); range.selectNodeContents(value);
      return { lines: range.getClientRects().length, fits: value.getBoundingClientRect().width <= value.parentElement.getBoundingClientRect().width };
    }));
    assert.ok(changeNumbers.every(({ lines, fits }) => lines === 1 && fits), `Split or overflowing comparison amount at ${width}px`);
    assert.equal(await page.locator('.planner-change-mobile-label').first().isVisible(), width <= 650);
    await page.screenshot({ path: fileURLToPath(new URL(`planner-${width}.png`, out)), fullPage: true });
    await page.locator('#planner-review-title').scrollIntoViewIfNeeded();
    await page.screenshot({ path: fileURLToPath(new URL(`review-${width}.png`, out)) });
    await page.locator('#planner-results-title').scrollIntoViewIfNeeded();
    await page.screenshot({ path: fileURLToPath(new URL(`results-${width}.png`, out)) });
    await page.locator('#planner-apply-title').scrollIntoViewIfNeeded();
    if (width <= 390) {
      await page.getByRole('button', { name: 'Áp dụng mục tiêu', exact: true }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: fileURLToPath(new URL(`apply-${width}.png`, out)) });
    } else {
      await page.locator('.planner-apply').screenshot({ path: fileURLToPath(new URL(`apply-${width}.png`, out)) });
    }
  }
  await page.getByRole('checkbox', { name: /Tôi đã đối chiếu/ }).check();
  await page.getByRole('button', { name: 'Áp dụng mục tiêu', exact: true }).click();
  await page.getByText(/Đã áp dụng mục tiêu preview-goal/).waitFor();
  assert.deepEqual(applies, [{ previewId: 'preview-qa', confirmed: true, expectedGoal: { goal_id: 'active-qa', version: 4 } }]);
  await page.getByLabel('Ngân sách quảng cáo tối đa (₫)', { exact: true }).fill('1000000');
  assert.equal(await page.getByRole('heading', { name: 'Phương án cho Chiến dịch QA' }).count(), 0);
  assert.deepEqual(errors, []);
  const results = ['Unconfigured backend cannot simulate', 'Interpretation never saves or simulates', 'Explicit review and confirmation; edits invalidate approval', 'Preview failure and retry leave active goal unchanged', 'Separate apply confirmation carries immutable preview and base version', 'Returned metrics expand to show provenance; edits clear stale results', 'No horizontal overflow at 1440/768/390/320px; no page errors'];
  await writeFile(new URL('results.json', out), JSON.stringify({ mode: 'real browser with stubbed APIs', results }, null, 2));
  console.log(results.join('\n'));
} finally { await browser.close(); }
