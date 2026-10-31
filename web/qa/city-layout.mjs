/* global document, innerWidth, innerHeight, scrollX, scrollY */
import { chromium, webkit } from 'playwright';
import { readFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';
const base = process.env.QA_BASE_URL || 'http://127.0.0.1:5179';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const password = await readFile(process.env.QA_PASSWORD_FILE || '/tmp/megaday-qa-password', 'utf8');
const out = new URL('../../qa/runs/city-layout/', import.meta.url).pathname;
await mkdir(out, { recursive: true });
for (const [name, engine] of Object.entries({ webkit, chromium })) {
  const browser = await engine.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 830 }, deviceScaleFactor: 2, reducedMotion: 'reduce' });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    await page.goto(base + '/login');
    await page.getByLabel('Username', { exact: true }).fill('admin');
    await page.getByLabel('Password', { exact: true }).fill(password);
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.waitForURL('**/city-demo');
    await page.getByRole('button', { name: 'Close tutorial' }).click();
    await page.waitForFunction(() => document.querySelector('.city-canvas')?.dataset.assets === 'ready');
    const assertLayout = async () => {
      const result = await page.evaluate(() => ({
        width: innerWidth, height: innerHeight, scroll: [scrollX, scrollY],
        document: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
        elements: ['.city-app', '.city-topbar', '.city-sidebar', '.city-footer', '.city-camera', '.pm-launcher'].map(selector => {
          const el = document.querySelector(selector), r = el.getBoundingClientRect();
          return { selector, x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
        }),
      }));
      assert.deepEqual(result.scroll, [0, 0], `${name}: document moved`);
      assert(result.document[0] <= result.width && result.document[1] <= result.height, `${name}: document overflow`);
      for (const r of result.elements) assert(r.width > 0 && r.height > 0 && r.x >= -1 && r.y >= -1 && r.right <= result.width + 1 && r.bottom <= result.height + 1, `${name}: clipped ${JSON.stringify(r)}`);
    };
    for (const [width, height] of [[1440,830], [1024,768], [768,1024], [390,844], [844,390]]) {
      await page.setViewportSize({ width, height });
      await page.getByRole('combobox', { name: 'Explore landmarks' }).selectOption('benthanh');
      const market = page.getByRole('button', { name: 'Visit Ben Thanh Market' });
      await market.waitFor();
      await page.waitForFunction(() => document.querySelector('[data-place="benthanh"]')?.getAttribute('aria-current') === 'true');
      assert.equal(await market.getAttribute('aria-current'), 'true');
      await market.focus();
      await page.keyboard.press('Enter');
      await assertLayout();
      await page.mouse.move(width / 2, height / 2);
      await page.mouse.wheel(300, 300);
      await assertLayout();
      await page.screenshot({ path: `${out}${name}-market-${width}.png` });
      await page.getByRole('button', { name: 'Open PM assistant', exact: true }).first().click();
      await page.getByRole('textbox', { name: 'Message Milo' }).fill('Local layout check');
      await page.getByRole('button', { name: 'Close chat' }).click();
      await assertLayout();
      await page.getByRole('button', { name: 'Warehouse', exact: true }).click();
      await page.getByRole('button', { name: 'Open workspace', exact: true }).click();
      await page.getByLabel('Search products', { exact: true }).fill('GD0001');
      await page.getByRole('button', { name: 'Close workspace' }).click();
      await page.getByRole('link', { name: 'View city', exact: true }).click();
      await assertLayout();
      console.log(`${name}: ${width}×${height} layout, market navigation, wheel, Milo and warehouse passed`);
    }
    await page.getByRole('link', { name: 'Data Studio', exact: true }).click();
    await page.getByRole('link', { name: 'View city', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('.city-canvas')?.dataset.assets === 'ready');
    await assertLayout();
    await page.reload();
    await page.waitForFunction(() => document.querySelector('.city-canvas')?.dataset.assets === 'ready');
    await assertLayout();
    assert.deepEqual(errors, []);
    console.log(`${name}: navigation/reload and clean console passed`);
  } finally { await browser.close(); }
}
