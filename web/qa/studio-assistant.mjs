import { chromium, webkit } from 'playwright';
import { readFile, mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';

const base = process.env.QA_BASE_URL || 'http://127.0.0.1:5179';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Local QA only');
const out = new URL('../../qa/runs/studio-assistant/', import.meta.url);
await mkdir(out, { recursive: true });

for (const [name, engine] of Object.entries({ chromium, webkit })) {
  const browser = await engine.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: 'no-preference' });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    const login = await page.request.post(base + '/api/auth/login', {
      headers: { Origin: base },
      data: { username: 'admin', password: (await readFile('/tmp/megaday-qa-password', 'utf8')).trim() },
    });
    assert(login.ok(), 'Local login failed');
    await page.addInitScript(() => localStorage.setItem('megaday.city.tour.v4', 'complete'));
    await page.goto(base + '/mock-data');
    await page.getByRole('button', { name: 'Import shop folder', exact: true }).waitFor();
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal(await page.locator('.pm-launcher').count(), 0, 'Studio must not have a duplicate floating launcher');
      const trigger = page.getByRole('button', { name: 'Open PM assistant', exact: true });
      assert.equal(await trigger.count(), 1);
      await trigger.focus();
      await page.keyboard.press('Enter');
      const chat = page.getByRole('dialog', { name: 'Milo', exact: true });
      await chat.waitFor();
      assert((await chat.boundingBox()).width <= width);
      await page.getByRole('button', { name: 'Close chat', exact: true }).click();
      await chat.waitFor({ state: 'hidden' });
      await page.getByRole('button', { name: 'Import shop folder', exact: true }).scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.innerWidth + 1));
      await page.screenshot({ path: new URL(`${name}-studio-${width}.png`, out).pathname });
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.waitForLoadState('networkidle');
    await page.goto(base + '/city-demo?district=hq');
    const launcher = page.locator('.pm-global-launcher .pm-launcher');
    await launcher.hover();
    const sprite = page.locator('.pm-global-launcher .pm-preview .milo-sprite');
    const styles = await sprite.evaluate(e => {
      const c = globalThis.getComputedStyle(e), image = globalThis.getComputedStyle(e.querySelector('.milo-motion'));
      return { overflow: c.overflow, width: c.width, height: c.height, sheetWidth: image.width, position: image.position, duration: image.animationDuration, timing: image.animationTimingFunction };
    });
    assert.deepEqual(styles, { overflow: 'hidden', width: '64px', height: '96px', sheetWidth: '320px', position: 'absolute', duration: '1.8s', timing: 'steps(5)' });
    await page.screenshot({ path: new URL(`${name}-hq-hover.png`, out).pathname });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await launcher.focus();
    await sprite.locator('.milo-still').waitFor({ state: 'visible' });
    assert.equal(await sprite.locator('.milo-motion').isVisible(), false);
    assert.equal(await sprite.locator('.milo-still').isVisible(), true);
    await page.screenshot({ path: new URL(`${name}-hq-reduced-motion.png`, out).pathname });
    assert.deepEqual(errors, []);
    console.log(`${name}: Studio actions, keyboard chat access, four widths, clipped sprite animation and reduced motion passed`);
  } finally { await browser.close(); }
}
