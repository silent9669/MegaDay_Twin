// Real browser interaction with stubbed account/backend APIs. No production writes.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const base = process.env.STUDIO_QA_URL || 'http://127.0.0.1:5186';
const out = new URL('../../../qa/runs/sku-assistant/', import.meta.url);
await mkdir(out, { recursive: true });
const sources = JSON.parse(await readFile(new URL('../../server/repository-sources.json', import.meta.url), 'utf8'));
const timestamp = '2026-11-11T00:00:00Z';
const campaigns = [{ id: 'CD1', name: 'Chiến dịch QA', skus: ['SKU1', 'SKU2'] }, { id: 'CD2', name: 'Chiến dịch khác', skus: ['SKU1'] }];
const contextFor = (sku) => ({
  sku: { sku, name: sku === 'SKU1' ? 'Bình giữ nhiệt QA' : 'Cốc QA', variant: '', stockAvailable: 120, soldTodayUnits: 12, priceEffectiveVnd: 100000, contributionPerUnitVnd: 25000, demandGroup: 'on_dinh' },
  campaigns: sku === 'SKU1' ? campaigns : [campaigns[0]], dataAsOf: timestamp,
  source: { id: sku, kind: 'sku', label: 'Snapshot SKU từ backend QA', data_as_of: timestamp },
  canPlan: true, notices: ['Ngân sách và chỉ số J thuộc toàn chiến dịch.'], opening: `Bạn muốn đạt mục tiêu gì với ${sku}?`,
});
const draft = { text: 'Bán 100 sản phẩm, ngân sách 2 triệu.', kind: 'sell_target', targetUnits: 100, start: '2026-11-11T08:00:00+07:00', deadline: '2026-11-11T20:00:00+07:00', maxSpendVnd: 2000000, maxLossVnd: null, sellTargetProbability: 0.8, minMarginalReturn: null, unresolved: [] };
const dist = (value) => ({ mean: value, p10: value, p50: value, p90: value });
const trace = {
  version: 1, input_hash: 'qa-input-snapshot', campaign_id: 'CD1', focus_sku: 'SKU1', quantity_scope: 'sku', economic_scope: 'campaign', start: draft.start, horizon_hours: 12, samples: 1000, seed: 123, data_as_of: timestamp, panel_version: 'qa-panel', goal: { kind: draft.kind, target_units: 100, params: { focus_sku: 'SKU1' } },
  equations: [{ id: 'objective', formula: 'J = contribution - spend + leftover_value - stockout_penalty', explanation: 'Tính từ cùng các mẫu mô phỏng.' }],
  sources: { batches: [{ batch_id: 'batch-qa', source_type: 'orders', file_name: 'qa-orders.csv', file_sha256: 'qa-sha256', as_of: timestamp }], forecasts: [], parameters: [{ key: 'platform_fee_rate', value: 0.12, version: 1, scope: 'global', scope_id: '*', set_by: 'QA lead' }], iroas: { source: 'prior', estimate_id: null, confidence: 'scenario', delta: 0.5, spend_scale: 100000 } },
  skus: [{ sku_id: 'SKU1', in_campaign: true, is_bundle: false, stock_start: 120, stock_unlimited: false, unit_cost_vnd: 60000, price_hourly: [100000], contribution_per_unit_hourly: [25000], forecast_mean_hourly: [2], forecast_id: null, forecast_label: 'scenario', dispersion_k: 3, sigma: 0.2, leftover_value_per_unit: 0, restock: [], economics_by_day: [{ sku_id: 'SKU1', day: '2026-11-11', eff_price: 100000, price_source: 'list_price', unit_cost: 60000, packaging_cost: 3000, fee_rate: 0.12, m: 25000, is_bundle: false }] }],
  actions: [{ key: 'continue', action: { kind: 'continue' }, metrics: { objective: dist(20000), contribution: dist(50000), spend: dist(30000), leftover_value: dist(0), stockout_penalty: dist(0), units_sold: dist(103), focus_units_sold: dist(3) }, reconciliation: { sum_components_mean: 20000, objective_mean: 20000, residual_vnd: 0 }, spend_hourly: [30000], per_sku: [{ sku_id: 'SKU1', units_sold: dist(3), contribution: dist(25000) }, { sku_id: 'SKU2', units_sold: dist(100), contribution: dist(25000) }] }],
  limitations: ['Dữ liệu giả lập cho kiểm tra giao diện; không phải kết quả kinh doanh.', 'Nguồn batch không phải truy vết từng dòng CSV.'],
};
const browser = await chromium.launch({ headless: true, channel: process.env.STUDIO_QA_CHANNEL });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const docs = { sources, settings: { signals: false, motion: false, quality: 'balanced', platforms: ['shopee'] }, sandbox: { hour: 1, event: 'observe' }, chats: { preferredModel: '', sessions: [] } };
  await context.route('**/api/workspace/*', (route) => {
    const key = new URL(route.request().url()).pathname.split('/').pop();
    if (route.request().method() === 'PUT') docs[key] = route.request().postDataJSON().data;
    return route.fulfill({ json: { revision: 1, updatedAt: timestamp, data: docs[key] } });
  });
  const turns = [], runs = [];
  let delayedTurn, delayNext = false, backendMissing = false;
  await context.route('**/api/pm/models', (route) => route.fulfill({ json: { configured: true, models: ['qa-model'], defaultModel: 'qa-model' } }));
  await context.route('**/api/pm/chat', async (route) => {
    const body = route.request().postDataJSON(); turns.push(body);
    const session = docs.chats.sessions.find((s) => s.id === body.sessionId);
    if (delayNext) { delayNext = false; await new Promise((resolve) => { delayedTurn = resolve; }); }
    const reply = `Đã ghi nhận mục tiêu cho ${session.context.sku}. Hãy kiểm tra thời gian và ngân sách.`;
    session.messages.push({ id: crypto.randomUUID(), role: 'assistant', content: reply });
    await route.fulfill({ json: { reply, model: 'qa-model', plan: { context: contextFor(session.context.sku), interpretation: { draft, method: 'ai', notices: [] } } } }).catch(() => {});
  });
  await context.route('**/api/sale-assistant/**', async (route) => {
    if (backendMissing) return route.fulfill({ status: 503, json: { error: { message: 'Backend chưa kết nối.' } } });
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/skus')) return route.fulfill({ json: { items: ['SKU1', 'SKU2'].map((sku) => contextFor(sku).sku), nextCursor: null, dataAsOf: timestamp } });
    if (url.pathname.endsWith('/context')) return route.fulfill({ json: contextFor(url.searchParams.get('sku')) });
    throw Error(`Unexpected legacy assistant mutation: ${url}`);
  });
  await context.route('**/api/sale-planner/**', (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/active')) return route.fulfill({ json: { campaignId: url.searchParams.get('campaignId'), activeGoal: null, baseGoal: null } });
    if (route.request().method() === 'GET') return route.fulfill({ json: { backendConfigured: true, canCreateGoals: true, canPreview: true, canApply: true, campaigns, dataAsOf: timestamp, referenceTime: timestamp, notice: null } });
    const body = route.request().postDataJSON(); runs.push(body);
    return route.fulfill({ json: { previewId: 'preview-qa', goalId: 'goal-qa', baseGoal: null,
      effectiveGoal: { goal_id: 'goal-qa', scope: 'campaign', scope_id: body.campaignId, kind: draft.kind, deadline: draft.deadline, target_units: 100, params: { focus_sku: 'SKU1', max_spend_vnd: 2000000 }, active: false, version: 1 },
      changes: [], notices: ['Chưa đạt mục tiêu SKU, chưa thể áp dụng.'], canApply: false, appliedGoalId: null, scenario: { data: {
      scenario_id: 'scenario-qa', blocked: false, cache_hit: false, input_hash: trace.input_hash,
      request: { campaign_id: body.campaignId, goal_id: 'goal-qa', start: draft.start, horizon_hours: 12, samples: 1000, seed: 123 }, recommendation: null,
      results: [{ key: 'continue', action: { kind: 'continue' }, feasible: true, goal_eligible: false, violations: [], objective: dist(20000), contribution: dist(50000), spend: dist(30000), units_sold: dist(103), goal_units_sold: dist(3), goal_sku: 'SKU1', leftover_units: dist(0), leftover_value: dist(0), p_target: 0 }], calculation_trace: trace,
    } } } });
  });
  const page = await context.newPage();
  await page.routeWebSocket('**/api/workspace/events', (socket) => socket.onMessage((message) => { if (message === 'ping') socket.send('pong'); }));
  const errors = []; page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`${base}/sku-assistant?sku=SKU1`);
  await page.getByRole('heading', { name: 'Bình giữ nhiệt QA', exact: true }).waitFor();
  assert.equal(new URL(page.url()).pathname, '/sale-planner');
  assert.equal(new URL(page.url()).searchParams.get('sku'), 'SKU1');
  assert.equal(runs.length, 0);
  await page.getByLabel('Message Milo', { exact: true }).fill('Bán 100 sản phẩm, ngân sách 2 triệu.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByRole('button', { name: 'Kiểm tra mục tiêu', exact: true }).waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.screenshot({ path: fileURLToPath(new URL(`milo-conversation-${width}.png`, out)) });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('button', { name: 'Kiểm tra mục tiêu', exact: true }).click();
  const campaign = page.getByLabel('Chiến dịch', { exact: true });
  await campaign.waitFor();
  assert.equal(await campaign.inputValue(), '', 'Multiple campaign memberships must never auto-select');
  assert.equal(runs.length, 0);
  await campaign.selectOption('CD1');
  await page.getByRole('checkbox', { name: /Tôi xác nhận các thông số để mô phỏng thử/ }).check();
  await page.getByRole('button', { name: 'Mô phỏng thử', exact: true }).click();
  await page.getByRole('heading', { name: 'Phương án cho Chiến dịch QA' }).waitFor();
  assert.equal(runs[0].focusSku, 'SKU1');
  assert.equal(runs[0].confirmed, true);
  assert.equal(runs[0].expectedGoal, null);
  assert.equal(await page.getByRole('button', { name: 'Áp dụng mục tiêu', exact: true }).isDisabled(), true);
  await page.getByText('Sản phẩm bán · SKU1 (kỳ vọng)', { exact: true }).waitFor();
  await page.getByText('Sản phẩm bán · toàn chiến dịch (kỳ vọng)', { exact: true }).waitFor();
  await page.getByText('Cách tính và đối chiếu con số', { exact: true }).click();
  await page.getByText(/Sai lệch đối chiếu: 0 ₫/).waitFor();
  await page.getByText('Dữ liệu và công thức đã dùng trong lần tính', { exact: true }).click();
  await page.getByText('File nguồn và lần nhập dữ liệu', { exact: true }).click();
  await page.getByText('qa-orders.csv', { exact: true }).waitFor();
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.locator('.milo-context').scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.innerWidth + 1), true, `Overflow at ${width}`);
    await page.screenshot({ path: fileURLToPath(new URL(`assistant-${width}.png`, out)) });
    await page.locator('.planner-calculation').scrollIntoViewIfNeeded();
    await page.screenshot({ path: fileURLToPath(new URL(`evidence-${width}.png`, out)) });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  delayNext = true;
  await page.getByLabel('Message Milo', { exact: true }).fill('Đổi ngân sách thành 3 triệu.');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByRole('button', { name: 'Stop reply', exact: true }).waitFor();
  await page.waitForFunction(() => globalThis.document.querySelector('.milo-context')?.textContent?.includes('SKU1'));
  await page.evaluate(() => globalThis.window.dispatchEvent(new CustomEvent('workspace:milo-sku', { detail: { screen: 'hq', sku: 'SKU2' } })));
  await page.getByText('Bạn muốn đạt điều gì với SKU2?', { exact: true }).waitFor();
  const deadline = Date.now() + 5000;
  while (!delayedTurn && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 20));
  assert.ok(delayedTurn, 'A saved Milo request should be pending before switching SKU');
  delayedTurn();
  assert.equal(await page.getByRole('heading', { name: 'Phương án cho Chiến dịch QA' }).count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Kiểm tra mục tiêu', exact: true }).count(), 0);
  backendMissing = true;
  await page.reload(); await page.getByText('Backend chưa kết nối.', { exact: true }).first().waitFor();
  assert.equal(await page.getByLabel('Message Milo', { exact: true }).count(), 0);
  await page.goto(`${base}/city-demo?district=hq`);
  const hq = page.getByRole('dialog', { name: 'MegaDay HQ', exact: true });
  await hq.waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    const goalLink = hq.getByRole('link', { name: 'Lập kế hoạch sale', exact: true });
    await goalLink.scrollIntoViewIfNeeded();
    const box = await goalLink.boundingBox();
    assert.ok(box && box.height >= 44 && box.x >= 0 && box.x + box.width <= width, `HQ goal link touch target at ${width}`);
    assert.equal(await hq.getByRole('link', { name: 'Lập kế hoạch sale', exact: true }).count(), 1);
    await page.screenshot({ path: fileURLToPath(new URL(`hq-goals-${width}.png`, out)) });
  }
  assert.deepEqual(errors, []);
  const results = ['SKU-specific context; no auto-mutation', 'Multiple campaigns require explicit choice', 'Reviewed goal sends focusSku and confirmed=true', 'Selected-SKU units distinct from campaign aggregate', 'J numeric substitution and source evidence expand', 'Switching SKU cancels pending replies and clears old plan', 'No fake backend fallback, page errors or horizontal overflow at 1440/768/390/320px'];
  await writeFile(new URL('results.json', out), JSON.stringify({ mode: 'real browser with stub APIs', results }, null, 2));
  console.log(results.join('\n'));
} finally { await browser.close(); }
