/* global document, innerWidth */
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
const base = process.env.STUDIO_QA_URL || 'http://127.0.0.1:5187';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.setDefaultTimeout(120000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base + '/mock-data');
  await page.getByLabel('Username', { exact: true }).fill('admin');
  await page.getByLabel('Password', { exact: true }).fill(process.env.STUDIO_QA_PASSWORD || 'studio-local-test');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByText('Saved to account', { exact: true }).waitFor();
  // Give the monitor a baseline before deliberately changing the inventory.
  await page.waitForTimeout(1500);
  const response = page.waitForResponse(r => r.url().endsWith('/api/studio/restock') && r.request().method() === 'POST');
  const started = Date.now();
  await page.getByRole('button', { name: 'Restock GD0003 +10', exact: true }).click();
  const saved = await response;
  assert.equal(saved.status(), 200);
  const result = await saved.json();
  assert.equal(result.change.after - result.change.before, 10);
  await page.getByText(/GD0003 restocked:/).waitFor();
  await page.getByRole('button', { name: 'Milo notifications', exact: true }).click();
  await page.getByRole('heading', { name: 'GD0003 restocked', exact: true }).waitFor();
  await page.getByRole('link', { name: 'Reload inventory', exact: true }).click();
  const row = page.locator('.warehouse-table tbody tr').filter({ hasText: 'GD0003' });
  await row.waitFor();
  assert.ok((await row.innerText()).includes(String(result.change.after)));
  await page.reload();
  await row.waitFor();
  assert.ok((await row.innerText()).includes(String(result.change.after)));
  await page.goto(base + '/mock-data');
  await page.getByRole('button', { name: 'Restock GD0003 +10', exact: true }).waitFor();
  for (const width of [1440, 768, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ restockMs: Date.now() - started, before: result.change.before, after: result.change.after, notification: true, persisted: true, responsive: true, pageErrors: 0 }));
} finally { await browser.close(); }
