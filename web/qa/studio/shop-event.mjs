/* global document, innerWidth */
// Opt-in: real shop exports are read locally, never committed or printed.
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
const base = process.env.STUDIO_QA_URL || 'http://127.0.0.1:5187';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
assert.ok(process.env.SHOP_PACK_PATH && process.env.LIVE_ARCHIVE_PATH);
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  page.setDefaultTimeout(120000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base + '/mock-data');
  await page.getByLabel('Username', { exact: true }).fill('admin');
  await page.getByLabel('Password', { exact: true }).fill(process.env.STUDIO_QA_PASSWORD || 'studio-local-test');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByText('Saved to account', { exact: true }).waitFor();
  const initial = await (await context.request.get(base + '/api/workspace/sources')).json();
  const reset = await context.request.put(base + '/api/workspace/sources', { headers: { Origin: base }, data: { revision: initial.revision, data: { schema: 'hackathon.sources.v1', files: [] } } });
  assert.ok(reset.ok());
  await page.reload();
  await page.getByText('No tables yet.', { exact: true }).waitFor();
  let started = Date.now();
  const saved = page.waitForResponse(response => response.url().endsWith('/api/workspace/sources') && response.request().method() === 'PUT');
  await page.locator('input[type=file][accept=".csv,.json"]').setInputFiles(process.env.SHOP_PACK_PATH);
  assert.equal((await saved).status(), 200);
  await page.getByRole('button', { name: /Orders.*117,687/ }).waitFor();
  await page.getByText('Saved to account', { exact: true }).waitFor();
  console.log(JSON.stringify({ baselineImportMs: Date.now() - started }));
  const baseline = await (await context.request.get(base + '/api/workspace/sources')).json();
  assert.equal(baseline.data.files.length, 6);
  await page.goto(base + '/city-demo?district=warehouse');
  await page.getByLabel('Search products').waitFor();
  await page.getByLabel('Filter demand labels').waitFor();
  await page.locator('.product-demand-label').first().waitFor();
  await page.locator('.product-demand-label').first().selectOption('Trendy');
  await page.waitForResponse(response => response.url().endsWith('/api/product-labels') && response.request().method() === 'PATCH' && response.status() === 200);
  await page.reload();
  await page.getByLabel('Filter demand labels').selectOption('Trendy');
  await page.locator('.warehouse-table tbody tr').first().waitFor();
  console.log(JSON.stringify({ manualLabelPersisted: true }));
  await page.goto(base + '/mock-data');
  await page.getByText('Saved to account', { exact: true }).waitFor();
  started = Date.now();
  await page.getByLabel('Upload ZIP archive').setInputFiles(process.env.LIVE_ARCHIVE_PATH);
  await page.getByText('3 tables loaded · 12,508 records', { exact: true }).waitFor();
  const clock = await (await context.request.get(base + '/api/workspace/sandbox')).json();
  assert.equal(clock.data.hour, 0);
  assert.ok(clock.data.startedAt);
  await page.getByRole('button', { name: 'Pause playback', exact: true }).click();
  console.log(JSON.stringify({ eventImportMs: Date.now() - started, automaticPlayback: true }));
  for (const width of [1440, 768, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Studio width ${width}`);
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ responsive: true, pageErrors: errors.length }));
} finally { await browser.close(); }
