// Local-only benchmark of workspace loading and unchanged-data refreshes.
import { chromium } from 'playwright';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const base = process.env.QA_BASE_URL || 'http://127.0.0.1:5194';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const browser = await chromium.launch();
try {
  const context = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 1000 } });
  assert((await context.request.post(base + '/api/auth/login', { headers: { Origin: base }, data: { username: 'admin', password: process.env.STUDIO_QA_PASSWORD || 'studio-local-test' } })).ok());
  if (process.env.QA_SEED === '1') {
    const pack = JSON.parse(await readFile(new URL('../public/shop-baseline.json', import.meta.url), 'utf8'));
    const old = await (await context.request.get(base + '/api/workspace/sources')).json();
    const saved = await context.request.put(base + '/api/workspace/sources', { headers: { Origin: base }, data: { data: pack, revision: old.revision, scope: 'shop' }, timeout: 120000 });
    assert(saved.ok(), 'Baseline shop import failed');
  }
  const results = [];
  for (let trial = 0; trial < 3; trial++) {
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {
      localStorage.setItem('megaday.city.tour.v4', 'complete');
      globalThis.longTasks = [];
      new PerformanceObserver(list => globalThis.longTasks.push(...list.getEntries().map(e => e.duration))).observe({ type: 'longtask', buffered: true });
    });
    await page.goto(base + '/city-demo?district=hq');
    await page.getByRole('region', { name: 'HQ product workspace' }).waitFor();
    await page.getByRole('button', { name: 'Track product', exact: true }).nth(1).waitFor({ timeout: 60000 });
    const readyMs = await page.evaluate(() => performance.now());
    await page.waitForTimeout(3000);
    const initial = await page.evaluate(() => {
      const sources = performance.getEntriesByType('resource').filter(r => r.name.includes('/api/workspace/sources'));
      return { sourceRequests: sources.length, sourceBytes: sources.reduce((sum, r) => sum + r.encodedBodySize, 0), longTaskMs: globalThis.longTasks.reduce((a, b) => a + b, 0), maxTaskMs: Math.max(0, ...globalThis.longTasks) };
    });
    await page.evaluate(() => globalThis.dispatchEvent(new Event('focus')));
    await page.waitForTimeout(2000);
    const refreshBytes = await page.evaluate(() => performance.getEntriesByType('resource').filter(r => r.name.includes('/api/workspace/sources')).reduce((sum, r) => sum + r.encodedBodySize, 0));
    results.push({ trial, readyMs, ...initial, refreshBytes: refreshBytes - initial.sourceBytes, errors });
    await page.close();
  }
  const out = new URL('../../qa/runs/performance/', import.meta.url);
  await mkdir(out, { recursive: true });
  await writeFile(new URL(`${process.env.QA_LABEL || 'measurement'}.json`, out), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results, null, 2));
} finally { await browser.close(); }
