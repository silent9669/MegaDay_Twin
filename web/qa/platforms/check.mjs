// Real WebGL/UI with in-memory workspace APIs; never writes production settings.
import assert from 'node:assert/strict';
import { mkdir, readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const base = process.env.PLATFORM_QA_URL || 'http://127.0.0.1:5173';
const out = process.env.PLATFORM_QA_OUT || '../../output/megaday-platforms';
await mkdir(out, { recursive: true });
const pack = JSON.parse(await readFile(new URL('../../server/repository-sources.json', import.meta.url), 'utf8'));
const browser = await chromium.launch();
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const docs = Object.fromEntries(Object.entries({
    sources: pack,
    settings: { signals: false, motion: false, quality: 'balanced', platforms: ['shopee'] },
    sandbox: { hour: 1, event: 'observe' },
    chats: { preferredModel: '', sessions: [] },
  }).map(([name, data]) => [name, { revision: 1, updatedAt: new Date().toISOString(), data }]));
  await context.addInitScript(() => localStorage.setItem('hackathon.city.tour', 'complete'));
  await context.route('**/api/workspace/*', async route => {
    const name = new URL(route.request().url()).pathname.split('/').pop();
    if (!docs[name]) return route.fulfill({ status: 404, json: {} });
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON();
      assert.equal(body.revision, docs[name].revision);
      docs[name] = { ...docs[name], revision: docs[name].revision + 1, data: body.data };
    }
    await route.fulfill({ json: docs[name] });
  });
  const page = await context.newPage();
  await page.routeWebSocket('**/api/workspace/events', socket => {
    socket.onMessage(message => { if (message === 'ping') socket.send('pong'); });
  });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${base}/city-demo?district=ads`);
  await page.getByRole('button', { name: 'Open workspace', exact: true }).click();
  await page.locator('.platform-grid').waitFor();
  for (const name of ['TikTok Ads', 'Meta Ads', 'Google Ads']) {
    await page.getByRole('button', { name: `Add ${name} building`, exact: true }).click();
    await page.getByRole('button', { name: `Remove ${name} building`, exact: true }).waitFor();
  }
  assert.deepEqual(docs.settings.data.platforms, ['shopee', 'tiktok', 'meta', 'google']);
  await page.waitForFunction(() => [...globalThis.document.querySelectorAll('.platform-logo img')].every(img => img.complete && img.naturalWidth > 0));
  await page.screenshot({ path: `${out}/platform-cards.png` });
  await page.getByRole('button', { name: 'Remove TikTok Ads building', exact: true }).click();
  await page.getByRole('button', { name: 'Add TikTok Ads building', exact: true }).waitFor();
  assert.deepEqual(docs.settings.data.platforms, ['shopee', 'meta', 'google']);
  await page.getByRole('button', { name: 'Add TikTok Ads building', exact: true }).click();
  await page.getByRole('button', { name: 'Remove TikTok Ads building', exact: true }).waitFor();
  await page.reload();
  await page.getByRole('button', { name: 'Open workspace', exact: true }).waitFor();
  await page.waitForTimeout(700); // Let the WebGL renderer upload sign textures.
  await page.screenshot({ path: `${out}/platform-buildings.png` });
  await page.getByRole('button', { name: 'Open workspace', exact: true }).click();
  for (const name of ['TikTok Ads', 'Meta Ads', 'Google Ads'])
    await page.getByRole('button', { name: `Remove ${name} building`, exact: true }).waitFor();
  for (const width of [768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.equal(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.innerWidth + 1), true);
    const logos = await page.locator('.platform-logo img').count();
    assert.equal(logos, 4);
    await page.screenshot({ path: `${out}/platform-cards-${width}.png` });
  }
  assert.deepEqual(errors, []);
  console.log('PASS: four logos load; add/remove persists through reload; desktop/tablet/mobile fit; no runtime errors.');
} finally {
  await browser.close();
}
