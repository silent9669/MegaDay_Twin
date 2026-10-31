/* global document, innerWidth, DataTransfer */
import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
const path = process.env.LIVE_ARCHIVE_PATH;
if (!path) throw Error('Set LIVE_ARCHIVE_PATH to a local live_11_11.zip export.');
const base = 'http://127.0.0.1:5187';
const out = new URL('../../../qa/runs/live-zip/', import.meta.url);
await mkdir(out, { recursive: true });
const bytes = await readFile(path);
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto(base + '/mock-data');
  await page.getByLabel('Username', { exact: true }).fill('admin');
  await page.getByLabel('Password', { exact: true }).fill('studio-local-test');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByText('Saved to account', { exact: true }).waitFor();
  const initial = await (await context.request.get(base + '/api/workspace/sources')).json();
  await context.request.put(base + '/api/workspace/sources', { headers: { Origin: base }, data: { revision: initial.revision, data: { schema: 'hackathon.sources.v1', files: [] } } });
  await page.reload();
  await page.getByText('No tables yet.', { exact: true }).waitFor();
  const transfer = await page.evaluateHandle(data => {
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array(data)], 'live_11_11.zip', { type: 'application/zip' }));
    return dt;
  }, [...bytes]);
  const started = Date.now();
  await page.locator('.data-studio').dispatchEvent('dragover', { dataTransfer: transfer });
  await page.locator('.data-studio').dispatchEvent('drop', { dataTransfer: transfer });
  await page.getByText('3 tables loaded · 12,508 records', { exact: true }).waitFor();
  const importMs = Date.now() - started;
  assert.equal(await page.getByRole('button', { name: 'Load selected tables', exact: true }).count(), 0);
  const saved = await (await context.request.get(base + '/api/workspace/sources')).json();
  assert.equal(saved.data.files.length, 3);
  assert.ok(saved.data.files.every(file => !file.commit));
  await page.getByRole('button', { name: 'Inventory 7,176 records Uploaded / edited', exact: true }).click();
  for (const width of [1440, 1024, 768, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await page.screenshot({ path: new URL(`studio-${width}.png`, out).pathname });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.reload();
  await page.getByRole('button', { name: 'MegaDay HQ', exact: true }).click();
  await page.getByRole('heading', { name: 'Workspace overview' }).waitFor();
  assert.equal(await page.locator('.source-metrics').getByText('4,156', { exact: true }).count(), 1);
  assert.equal(await page.locator('.source-metrics').getByText('262', { exact: true }).count(), 1);
  await page.screenshot({ path: new URL('hq.png', out).pathname });
  await page.getByRole('button', { name: 'Close workspace', exact: true }).click();
  await page.getByRole('button', { name: 'Residential district', exact: true }).click();
  await page.getByRole('button', { name: 'Open workspace', exact: true }).click();
  await page.getByRole('region', { name: 'Imported orders', exact: true }).waitFor();
  await page.getByText('4,156 records · 1 / 139', { exact: true }).waitFor();
  await page.screenshot({ path: new URL('neighborhood.png', out).pathname });
  await page.getByRole('button', { name: 'Close workspace', exact: true }).click();
  await page.getByRole('button', { name: 'Warehouse', exact: true }).click();
  await page.getByRole('region', { name: 'Inventory table', exact: true }).waitFor();
  const labels = await (await context.request.get(base + '/api/product-labels')).json();
  assert.equal(labels.products.length, 262);
  await page.screenshot({ path: new URL('warehouse.png', out).pathname });
  assert.deepEqual(errors, []);
  await writeFile(new URL('result.json', out), JSON.stringify({ importMs, tables: 3, records: 12508, stockedProducts: 262, errors }, null, 2));
  console.log(JSON.stringify({ importMs, tables: 3, records: 12508, stockedProducts: 262, errors }));
} finally { await browser.close(); }
