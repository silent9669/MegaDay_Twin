// Real browser UI, mocked workspace and label provider API; no production writes.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const base = process.env.STUDIO_QA_URL || 'http://127.0.0.1:5186';
const pack = JSON.parse(await readFile(new URL('../../server/repository-sources.json', import.meta.url), 'utf8'));
const out = new URL('../../../qa/runs/product-labels/', import.meta.url);
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: process.env.STUDIO_QA_CHANNEL });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const docs = Object.fromEntries(Object.entries({
    sources: pack, settings: { signals: false, motion: false, quality: 'balanced', platforms: ['shopee'] },
    sandbox: { hour: 1, event: 'observe' }, chats: { preferredModel: '', sessions: [] },
  }).map(([name, data]) => [name, { revision: 1, updatedAt: new Date().toISOString(), data }]));
  const products = Array.from({ length: 10 }, (_, i) => ({
    id: `orders:SKU${i}`, reference: `SKU${i}`, name: `Bình nước ${i}`, alternateNames: [],
    source: 'orders', identity: 'platform_sku', rowCount: 3,
  }));
  let configured = false, run = null, posts = 0, nextFailure = false;
  const response = () => ({
    configured, provider: configured ? 'QA provider' : null,
    sourceRevision: docs.sources.revision, sourceFingerprint: String(docs.sources.revision).padStart(64, '0'),
    products, run, stale: !!run && run.sourceRevision !== docs.sources.revision,
  });
  await context.route('**/api/workspace/*', (route) => {
    const name = new URL(route.request().url()).pathname.split('/').pop();
    if (!docs[name]) return route.fulfill({ status: 404, json: { error: { message: 'Unknown document' } } });
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      if (body.revision !== docs[name].revision) return route.fulfill({ status: 409, json: { error: { message: 'Conflict' } } });
      docs[name] = { ...docs[name], revision: docs[name].revision + 1, data: body.data };
    }
    return route.fulfill({ json: docs[name] });
  });
  await context.route('**/api/product-labels', async (route) => {
    if (route.request().method() === 'POST') {
      posts++;
      assert.equal(route.request().postDataJSON().sourceRevision, docs.sources.revision, 'Only the saved revision can be classified');
      run = {
        id: '11111111-1111-4111-8111-111111111111', sourceRevision: docs.sources.revision,
        sourceFingerprint: response().sourceFingerprint, provider: 'QA provider',
        startedAt: new Date().toISOString(), completedAt: new Date().toISOString(),
        status: nextFailure ? 'failed' : 'partial', error: nextFailure ? 'Provider temporarily unavailable.' : null,
        results: nextFailure ? [] : [{ productId: products[0].id, group: 'mua_vu', reason: 'Seasonality reported by the test provider.', confidence: 0.82 }],
      };
      nextFailure = false;
    }
    await route.fulfill({ json: response() });
  });
  const page = await context.newPage();
  await page.routeWebSocket('**/api/workspace/events', (socket) => socket.onMessage((message) => { if (message === 'ping') socket.send('pong'); }));
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`${base}/mock-data`);
  const panel = page.locator('.product-label-panel');
  await panel.getByText('External labeling tool is not connected').waitFor();
  assert.equal(posts, 0);
  assert.equal(await panel.getByRole('button', { name: 'Run labeling', exact: true }).isDisabled(), true);
  await panel.getByRole('button', { name: /Show products/ }).click();
  assert.equal(await panel.getByText('Chưa phân loại', { exact: true }).count(), 8);
  configured = true;
  await panel.getByRole('button', { name: 'Refresh product labels' }).click();
  await panel.getByText('Ready to request product labels').waitFor();
  assert.equal(posts, 0, 'Enabling provider on initial demo must not label automatically');
  nextFailure = true;
  const csv = 'Mã nội bộ,Tồn thực tế,Tên hàng\nQA,5,Imported bottle';
  await page.getByLabel('Import source files').setInputFiles({ name: 'inventory.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
  await page.getByText('Saved to account', { exact: true }).waitFor();
  await panel.getByText('Labeling failed — retry is available').waitFor();
  assert.equal(posts, 1, 'One labeling attempt after source save');
  assert.ok(docs.sources.data.files.find((file) => file.kind === 'inventory').text.includes('Imported bottle'));
  await panel.getByRole('button', { name: 'Retry labeling' }).click();
  await panel.getByText('Mùa vụ', { exact: true }).waitFor();
  assert.equal(posts, 2);
  await panel.getByText('Tool confidence: 82%').waitFor();
  await page.reload();
  await panel.getByText('Some products still need a label').waitFor();
  await panel.getByRole('button', { name: /Show products/ }).click();
  await panel.getByText('Mùa vụ', { exact: true }).waitFor();
  assert.equal(posts, 2, 'Reload must not rerun a saved labeling result');
  await panel.getByLabel('Search products').fill('SKU9');
  await panel.getByRole('heading', { name: 'Bình nước 9', exact: true }).waitFor();
  assert.equal(await panel.locator('li').count(), 1);
  await panel.getByLabel('Search products').fill('');
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await panel.getByRole('heading', { name: 'Product labels', exact: true }).scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.innerWidth + 1), true, `Overflow at ${width}px`);
    for (const button of await panel.locator('button').all()) {
      const bounds = await button.boundingBox();
      assert.ok(bounds && bounds.height >= 44 && bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
    }
    await page.screenshot({ path: fileURLToPath(new URL(`labels-${width}.png`, out)) });
  }
  docs.sources.revision++;
  await panel.getByRole('button', { name: 'Refresh product labels' }).click();
  await panel.getByText(/Previous results belong to older source data/).waitFor();
  assert.equal(await panel.getByText('Mùa vụ', { exact: true }).count(), 0);
  assert.deepEqual(errors, []);
  const results = ['Unconfigured tool shows candidates without fake labels or POSTs', 'Saved import auto-triggers exactly once; provider failure preserves source save', 'Retry renders partial labels, reason, confidence and unknown products', 'Reload restores labels without rerunning; search and mobile layouts work', 'Source revision change hides old results'];
  await writeFile(new URL('results.json', out), JSON.stringify({ mode: 'mocked account/provider APIs, real browser UI', results }, null, 2));
  console.log(results.join('\n'));
} finally { await browser.close(); }
