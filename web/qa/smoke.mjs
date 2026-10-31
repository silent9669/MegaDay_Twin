import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const base = process.argv[2] || 'https://megadaytwin.dangphuc.me';
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const login = await page.goto(new URL('/login', base).href);
  assert.equal(login.status(), 200);
  await page.getByRole('textbox', { name: 'Username', exact: true }).waitFor();
  await page.getByRole('textbox', { name: 'Password', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Sign in', exact: true }).waitFor();
  assert.equal((await context.request.get(new URL('/city-demo', base).href, { maxRedirects: 0 })).status(), 307);
  assert.equal((await context.request.get(new URL('/api/workspace/chats', base).href)).status(), 401);
  const health = await context.request.get(new URL('/healthz', base).href);
  assert.equal(health.status(), 200);
  assert.deepEqual(await health.json(), { status: 'ok', db: 'ok', mode: 'fixtures' });
  assert.deepEqual(errors, []);
  console.log('Login controls, access gate, private chat storage and D1 health passed.');
} finally {
  await browser.close();
}
