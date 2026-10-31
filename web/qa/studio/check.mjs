// UI regression check with in-memory account API responses; no production writes.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const base = process.env.STUDIO_QA_URL || 'http://127.0.0.1:5186';
const pack = JSON.parse(await readFile(new URL('../../server/repository-sources.json', import.meta.url), 'utf8'));
const out = new URL('../../../qa/runs/studio/', import.meta.url);
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: process.env.STUDIO_QA_CHANNEL });
const results = [];
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const docs = Object.fromEntries(Object.entries({
    sources: pack,
    settings: { signals: false, motion: false, quality: 'balanced', platforms: ['shopee'] },
    sandbox: { hour: 1, event: 'observe' },
    chats: { preferredModel: '', sessions: [] },
  }).map(([name, data]) => [name, { revision: 1, updatedAt: new Date().toISOString(), data }]));
  await context.route('**/api/workspace/*', async (route) => {
    const name = new URL(route.request().url()).pathname.split('/').pop();
    if (!docs[name]) return route.fulfill({ status: 404, json: { error: { message: 'Unknown document' } } });
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      if (body.revision !== docs[name].revision)
        return route.fulfill({ status: 409, json: { error: { message: 'Revision conflict' } } });
      docs[name] = { ...docs[name], revision: docs[name].revision + 1, data: body.data };
    }
    return route.fulfill({ json: docs[name] });
  });
  const page = await context.newPage();
  await page.routeWebSocket('**/api/workspace/events', (socket) => {
    socket.onMessage((message) => { if (message === 'ping') socket.send('pong'); });
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`${base}/mock-data`);
  await page.getByText('Saved to account', { exact: true }).waitFor();
  await page.getByText('All 6 sources are repository demo data.', { exact: false }).waitFor();
  await page.locator('.source-list').getByRole('button', { name: /Product costs/ }).click();
  await page.getByRole('tab', { name: /Diagnostics/ }).click();
  await page.getByRole('heading', { name: 'Money formats ready for normalization' }).waitFor();
  assert.ok(await page.locator('.source-diagnostics article').count() < 10, 'Repeated money checks should be grouped');
  const moneySummary = page.locator('.source-diagnostics article').filter({ hasText: 'Money formats ready for normalization' }).locator('summary');
  await moneySummary.click();
  assert.ok(await page.locator('.diagnostic-value').count() > 100);
  for (const width of [1440, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await moneySummary.scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.innerWidth + 1), true);
    await page.screenshot({ path: fileURLToPath(new URL(`money-preview-${width}.png`, out)) });
  }
  await moneySummary.click();
  await page.setViewportSize({ width: 1440, height: 1000 });
  results.push('Demo currency formats are grouped as informational previews with original values, at desktop and mobile widths');

  const beforeRejected = JSON.stringify(docs.sources);
  await page.getByLabel('Import source files').setInputFiles(Array.from({ length: 7 }, (_, i) => ({
    name: `extra-${i}.csv`, mimeType: 'text/csv', buffer: Buffer.from('a,b\n1,2'),
  })));
  await page.getByText(/Selected 7 files; choose at most 6/).waitFor();
  assert.equal(JSON.stringify(docs.sources), beforeRejected);
  await page.getByText(/Nothing was imported; existing sources are unchanged/).waitFor();
  results.push('Rejected seven-file import keeps the existing demo workspace and reports the specific limit');

  await page.locator('.source-list').getByRole('button', { name: /Inventory/ }).click();
  await page.getByRole('tab', { name: /Diagnostics/ }).click();
  await page.getByText(/Repository demo sources include intentional data issues/).waitFor();
  results.push('Demo checks are explained without hiding actual data problems');
  await page.getByRole('textbox', { name: 'Search source' }).fill('old filter must not hide new import');

  const csv = 'Mã nội bộ,Tồn thực tế,Tên hàng,Thời điểm cập nhật\n' +
    Array.from({ length: 40 }, (_, i) => `SKU${i + 1},${i === 31 ? -2 : 5},Item ${i + 1},2026-11-11 01:00`).join('\n');
  await page.getByLabel('Import source files').setInputFiles({ name: 'inventory.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
  await page.getByRole('heading', { name: 'Negative inventory' }).waitFor();
  await page.getByText('Saved to account', { exact: true }).waitFor();
  await page.getByText(/5 demo sources · 1 uploaded or edited sources/).waitFor();
  assert.ok(docs.sources.data.files.find((f) => f.kind === 'inventory').text.includes('SKU32,-2'));
  assert.equal(await page.getByText(/Repository demo sources include intentional data issues/).count(), 0);
  results.push('Upload retains negative inventory for review and autosaves the actual source');

  await page.getByRole('button', { name: 'View record 32', exact: true }).click();
  await page.locator('#source-record-inventory-31').waitFor();
  assert.equal(await page.locator('#source-record-inventory-31').evaluate((row) => row === globalThis.document.activeElement), true);
  assert.ok(await page.locator('#source-record-inventory-31').innerText().then((text) => text.includes('SKU32')));
  results.push('View record opens page two and focuses the correct uploaded row');

  await page.getByRole('tab', { name: /Diagnostics/ }).click();
  await page.getByRole('button', { name: 'Review (0)', exact: true }).click();
  await page.getByText(/No checks match these filters/).waitFor();
  await page.getByRole('button', { name: 'All (1)', exact: true }).click();
  await page.getByRole('textbox', { name: 'Search source' }).fill('nothing matches');
  await page.getByText(/No checks match these filters/).waitFor();
  await page.getByRole('textbox', { name: 'Search source' }).fill('negative inventory');
  await page.getByRole('heading', { name: 'Negative inventory' }).waitFor();
  results.push('Severity and readable-title search show honest empty states');

  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.getByRole('heading', { name: 'Data checks', exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: fileURLToPath(new URL(`diagnostics-${width}.png`, out)), fullPage: true });
    assert.equal(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.innerWidth + 1), true, `Page overflows at ${width}px`);
    for (const button of await page.locator('.studio-actions button').all()) {
      const bounds = await button.boundingBox();
      assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= width + 1, `Upload/export controls clipped at ${width}px`);
    }
    const buttons = page.locator('.diagnostics-filters button');
    for (const button of await buttons.all()) {
      const bounds = await button.boundingBox();
      assert.ok(bounds && bounds.width > 0 && bounds.height >= 44 && bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
    }
    await page.getByRole('button', { name: 'View record 32', exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: fileURLToPath(new URL(`record-check-${width}.png`, out)), fullPage: true });
  }
  assert.deepEqual(errors, []);
  results.push('Desktop/tablet/mobile widths 1440/768/390/320 fit without page overflow or runtime errors');
  await writeFile(new URL('results.json', out), JSON.stringify({ mode: 'mocked account API, real browser UI', results }, null, 2));
  console.log(results.join('\n'));
} finally {
  await browser.close();
}
