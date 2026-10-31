/* global document, innerWidth */
// Local Worker storage and ZIP integration; synthetic inventory and mocked model output.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
import { strToU8, zipSync } from 'fflate';
const base = process.env.STUDIO_QA_URL || 'http://127.0.0.1:5187';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Synthetic data must stay local');
const out = new URL('../../../qa/runs/studio-import/', import.meta.url);
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: process.env.STUDIO_QA_CHANNEL });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(`${base}/mock-data`);
  await page.getByLabel('Username', { exact: true }).fill('admin');
  await page.getByLabel('Password', { exact: true }).fill(process.env.STUDIO_QA_PASSWORD || 'studio-local-test');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByText('Saved to account', { exact: true }).waitFor();
  const csv = 'Mã nội bộ,Tên hàng,Tồn thực tế,Thời điểm cập nhật\nQA-CUP,Ceramic cup,17,2026-10-31 18:00\nQA-TET,Đèn trang trí Tết,24,2026-10-31 18:00';
  const zip = zipSync({ 'shop/inventory.csv': strToU8(csv), 'readme.txt': strToU8('Synthetic test fixture') });
  await page.getByLabel('Upload ZIP archive').setInputFiles({ name: 'inventory.zip', mimeType: 'application/zip', buffer: Buffer.from(zip) });
  await page.getByRole('button', { name: 'Load 1 table to workspace' }).waitFor();
  await page.getByText('Preview recognized table').click();
  assert.equal(await page.locator('.archive-preview').getByText('17', { exact: true }).count(), 1);
  await page.screenshot({ path: new URL('archive-review.png', out).pathname });
  await page.getByRole('button', { name: 'Load 1 table to workspace' }).click();
  await page.getByText('Saved to account', { exact: true }).waitFor();
  await page.reload();
  await page.getByRole('button', { name: 'Inventory 2 records Uploaded / edited', exact: true }).click();
  await page.getByRole('button', { name: 'Ceramic cup', exact: true }).waitFor();
  const saved = await (await context.request.get(`${base}/api/workspace/sources`)).json();
  assert.ok(saved.data.files.find(f => f.kind === 'inventory').text.includes('QA-CUP'));
  await page.getByRole('button', { name: 'Warehouse', exact: true }).click();
  await page.getByRole('button', { name: 'Close tutorial', exact: true }).click();
  await page.getByRole('button', { name: 'Open workspace', exact: true }).click();
  await page.locator('.warehouse-table').getByText('Ceramic cup', { exact: true }).waitFor();
  assert.equal(await page.locator('.warehouse-table').getByText('17', { exact: true }).count(), 1);
  await page.getByText('Decision model is not connected.', { exact: true }).waitFor();
  assert.ok(await page.getByRole('button', { name: 'Label inventory' }).isDisabled());
  let run = null, posts = 0;
  const original = await (await context.request.get(`${base}/api/product-labels`)).json();
  await page.route('**/api/product-labels', route => {
    if (route.request().method() === 'POST') {
      posts++;
      const body = route.request().postDataJSON();
      assert.equal(body.context.market, 'Vietnam');
      assert.equal(body.context.evidence[0].productCode, 'QA-TET');
      run = { id: '11111111-1111-4111-8111-111111111111', provider: 'QA structured output', context: body.context,
        sourceRevision: saved.revision, sourceFingerprint: original.sourceFingerprint, status: 'partial',
        startedAt: new Date().toISOString(), completedAt: new Date().toISOString(), error: null,
        results: original.products.map(p => ({ productId: p.id, group: p.reference === 'QA-TET' ? 'Seasonal' : null,
          reason: p.reference === 'QA-TET' ? 'Designed for Tet; inference from product use.' : 'Need dated product demand evidence.' })) };
    }
    return route.fulfill({ json: { ...original, configured: true, provider: 'QA structured output', run } });
  });
  await page.getByRole('button', { name: 'Refresh product labels' }).click();
  await page.getByText('Evidence (0)', { exact: true }).click();
  await page.getByLabel('Assessment date', { exact: true }).fill('2026-10-31');
  await page.getByLabel('Product code', { exact: true }).selectOption('QA-TET');
  await page.getByLabel('Observation date', { exact: true }).fill('2026-10-30');
  await page.getByLabel('Evidence source', { exact: true }).fill('Product specification');
  await page.getByLabel('Observation', { exact: true }).fill('Lamp designed exclusively for Tet decorations.');
  await page.getByRole('button', { name: 'Add evidence', exact: true }).click();
  await page.getByRole('button', { name: 'Label inventory', exact: true }).click();
  await page.getByText('Seasonal', { exact: true }).waitFor();
  await page.getByText('Need dated product demand evidence.', { exact: true }).waitFor();
  assert.equal(posts, 1);
  await page.getByText('Evidence (1)', { exact: true }).click();
  for (const width of [1440, 1024, 768, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `No page overflow at ${width}`);
    await page.screenshot({ path: new URL(`warehouse-${width}.png`, out).pathname });
  }
  await page.getByLabel('Market', { exact: true }).fill('Thailand');
  assert.equal(await page.getByText('Seasonal', { exact: true }).count(), 0, 'Changed market invalidates labels');
  assert.deepEqual(errors, []);
  await writeFile(new URL('result.json', out), JSON.stringify({ realWorker: ['ZIP extraction', 'table preview', 'durable save and reload', 'map inventory'], mockedModel: ['explicit run', 'evidence', 'labels', 'context invalidation'], widths: [1440, 1024, 768, 320], errors }, null, 2));
  console.log('Passed: local Worker ZIP → persisted tables → warehouse; model UI contract; four viewport widths.');
} finally { await browser.close(); }
