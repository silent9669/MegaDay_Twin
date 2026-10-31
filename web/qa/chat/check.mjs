import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
const base = process.env.CHAT_QA_URL || 'http://localhost:8797';
const models = ['gpt-4.1', 'gpt-4.1-mini', 'gpt-4o', 'gpt-5', 'gpt-5-mini', 'gpt-5-nano', 'gpt-6-sol', 'gpt-6.1-sol', 'gpt-6-astra', 'gpt-6-luna', 'o3', 'o4-mini'];
const browser = await chromium.launch({ headless: true });
const results = [];
async function device(viewport) {
  const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
  // Only the external model service is simulated. Account storage and sync use the real local Worker.
  await context.route('**/api/pm/models', (route) => route.fulfill({ json: { configured: true, models, defaultModel: 'gpt-4.1-mini' } }));
  await context.route('**/api/pm/chat', async (route) => {
    const body = route.request().postDataJSON();
    const stored = await (await context.request.get(base + '/api/workspace/chats')).json();
    const session = stored.data.sessions.find((s) => s.id === body.sessionId);
    assert.equal(session.messages.at(-1).id, body.lastMessageId);
    session.messages.push({ id: crypto.randomUUID(), role: 'assistant', content: '**Check source freshness** before acting.\n\n| Source | Check |\n| --- | --- |\n| Inventory | Latest timestamp |\n| Campaigns | Reporting delay |' });
    session.updatedAt = new Date().toISOString();
    const saved = await context.request.put(base + '/api/workspace/chats', { headers: { Origin: base }, data: { revision: stored.revision, data: stored.data } });
    assert.equal(saved.status(), 200);
    await route.fulfill({ json: { reply: session.messages.at(-1).content } });
  });
  const login = await context.request.post(base + '/api/auth/login', { headers: { Origin: base }, data: { username: 'admin', password: 'chat-qa-password' } });
  assert.equal(login.status(), 200);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(base + '/city-demo');
  const close = page.getByRole('button', { name: 'Close tutorial' });
  if (await close.isVisible()) await close.click();
  await page.locator('.pm-launcher').click();
  await page.getByRole('combobox', { name: 'OpenAI model' }).waitFor();
  await page.waitForFunction(() => globalThis.document.querySelector('.chat-form select')?.options.length > 8);
  return { context, page, errors };
}
try {
  await mkdir('qa/chat/screenshots', { recursive: true });
  const a = await device({ width: 1440, height: 1000 });
  const b = await device({ width: 390, height: 844 });
  assert.equal(await a.page.getByRole('button', { name: /clear conversation|trash/i }).count(), 0);
  assert.equal(await a.page.locator('.chat-form option').count(), 12);
  await a.page.getByRole('combobox').selectOption('o4-mini');
  await a.page.getByRole('textbox', { name: 'Message Milo' }).fill('Review warehouse stock');
  await a.page.getByRole('button', { name: 'Send message' }).click();
  await a.page.getByText('Check source freshness', { exact: true }).waitFor();
  await a.page.screenshot({ path: 'qa/chat/screenshots/desktop-chat.png' });
  results.push('12 model choices, no trash button, real account save, Markdown reply');
  await b.page.getByRole('button', { name: 'Chat history', exact: true }).click();
  await b.page.getByRole('button', { name: /Review warehouse stock/ }).first().click();
  await b.page.getByText('Check source freshness', { exact: true }).waitFor();
  assert.equal(await b.page.getByRole('combobox').inputValue(), 'o4-mini');
  results.push('Independent signed-in device reopens the saved session and model');
  await b.page.getByRole('textbox', { name: 'Message Milo' }).fill('What source should I check?');
  await b.page.getByRole('button', { name: 'Send message' }).click();
  await a.page.getByText('What source should I check?', { exact: true }).waitFor();
  results.push('Second device messages appear through authenticated WebSocket notifications');
  await b.page.screenshot({ path: 'qa/chat/screenshots/mobile-chat.png' });
  await b.page.getByRole('button', { name: 'Chat history', exact: true }).click();
  await b.page.screenshot({ path: 'qa/chat/screenshots/mobile-history.png' });
  await b.page.reload();
  const tutorial = b.page.getByRole('button', { name: 'Close tutorial' });
  if (await tutorial.isVisible()) await tutorial.click();
  await b.page.locator('.pm-launcher').click();
  await b.page.getByRole('button', { name: 'Chat history', exact: true }).click();
  await b.page.getByRole('button', { name: /Review warehouse stock/ }).first().click();
  await b.page.getByText('What source should I check?', { exact: true }).waitFor();
  results.push('History survives a full reload');
  for (const width of [320, 390, 768, 1440]) {
    await b.page.setViewportSize({ width, height: 900 });
    const bounds = await b.page.locator('.city-chat').boundingBox();
    assert(bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
    const overflow = await b.page.locator('.city-chat').evaluate((el) => el.scrollWidth > el.clientWidth);
    assert.equal(overflow, false);
  }
  results.push('Chat containment at 320, 390, 768 and 1440 pixels');
  const before = await (await a.context.request.get(base + '/api/workspace/chats')).json();
  const current = structuredClone(before.data); current.preferredModel = 'o3';
  assert.equal((await b.context.request.put(base + '/api/workspace/chats', { headers: { Origin: base }, data: { revision: before.revision, data: current } })).status(), 200);
  assert.equal((await a.context.request.put(base + '/api/workspace/chats', { headers: { Origin: base }, data: { revision: before.revision, data: before.data } })).status(), 409);
  const unauth = await browser.newContext();
  assert.equal((await unauth.request.get(base + '/api/workspace/chats')).status(), 401);
  assert.equal((await a.context.request.put(base + '/api/workspace/chats', { headers: { Origin: 'https://other.test' }, data: {} })).status(), 403);
  results.push('Stale writes, unauthenticated reads and cross-origin writes are rejected');
  assert.deepEqual(a.errors, []); assert.deepEqual(b.errors, []);
  results.push('No browser runtime errors');
  await writeFile('qa/chat/results.json', JSON.stringify({ externalModelService: 'simulated', accountStorage: 'local Cloudflare SQLite Durable Object', checks: results }, null, 2));
  console.log(JSON.stringify(results, null, 2));
} finally { await browser.close(); }
