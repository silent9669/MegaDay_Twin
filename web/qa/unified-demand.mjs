// Browser check of the real components against explicit stubbed label APIs.
// Run with a local Vite server; no production or model-provider requests.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const base = process.env.STUDIO_QA_URL || 'http://127.0.0.1:5179';
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const out = new URL('../../qa/runs/unified-demand/', import.meta.url);
await mkdir(out, { recursive: true });
const selection = {
  primary_label: 'Seasonal', model_id: 'analog@1.0.0', from_backtest: false,
  policy_version: 'demand-selection-v1', forecast_origin: '2026-10-31T07:00:00Z',
  reasons: ['Short history: use comparable products. The demand label does not validate the forecast.'],
  data_status: { new: true, sparse: true, insufficient: false, history_days: 7 },
};
const demand = { primary_label: 'Seasonal', source: 'warehouse_ai', label_evidence: null,
  history_profile: 'moi', model_selection: selection };
const browser = await chromium.launch({ channel: process.env.STUDIO_QA_CHANNEL });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => { errors.push(error.message); console.error(error.message); });
  page.on('response', response => { if (response.status() >= 400) console.error(response.status(), response.url()); });
  let posts = 0;
  await page.route('**/api/product-labels/sync', route => {
    posts++;
    return route.fulfill({ json: { status: posts === 1 ? 'pending' : 'synced', message: posts === 1
      ? '0/1 labels synchronized. Confirm pending SKU mappings in the backend and retry.'
      : '1/1 labels synchronized. Forecasts will use the shared labels with data-quality safeguards.' } });
  });
  await page.route(/\/api\/product-labels$/, route => route.fulfill({ json: {
    sourceRevision: 1, sourceFingerprint: 'a'.repeat(64), configured: true, stale: false,
    products: [{ id: 'sku-qa', name: 'Seasonal product', reference: 'SKU-QA' }],
    run: { status: 'completed', results: [{ productId: 'sku-qa', group: 'Seasonal', reason: 'Supplied product evidence.' }] },
  } }));
  await page.route('**/__demand-qa', route => route.fulfill({ contentType: 'text/html', body: `
    <!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Demand classification QA</title><div id="root"></div><script type="module">
    import RefreshRuntime from '/@react-refresh';
    RefreshRuntime.injectIntoGlobalHook(window);
    window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>type=>type;
    window.__vite_plugin_react_preamble_installed__=true;
    const React=(await import('/node_modules/.vite/deps/react.js')).default;
    const {createRoot}=(await import('/node_modules/.vite/deps/react-dom_client.js')).default;
    await import('/src/index.css'); await import('/src/studio/studio.css'); await import('/src/hq/hq.css');
    const {ProductLabels}=await import('/src/studio/ProductLabels.tsx');
    const {DemandDetails}=await import('/src/planner/DemandDetails.tsx');
    createRoot(document.getElementById('root')).render(React.createElement('main',{style:{maxWidth:1050,margin:'auto',padding:16}},
      React.createElement(ProductLabels,{sourceRevision:1,blocked:false,inventory:[{code:'SKU-QA',name:'Seasonal product',stock:100,asOf:'2026-10-31'}]}),
      React.createElement('div',{className:'hq-details'},React.createElement(DemandDetails,{demand:${JSON.stringify(demand)}}))));
    </script></html>` }));
  await page.goto(`${base}/__demand-qa`);
  const sync = page.getByRole('button', { name: 'Sync labels to forecasting' });
  await sync.click();
  await page.getByText(/0\/1 labels synchronized/).waitFor();
  await sync.click();
  await page.getByText(/1\/1 labels synchronized/).waitFor();
  await page.getByText('Why this forecast model?', { exact: true }).click();
  await page.getByText(/analog@1.0.0/).waitFor();
  await page.getByText(/New product.*Sparse sales/).waitFor();
  for (const width of [1440, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    assert(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.innerWidth + 1), `Overflow at ${width}px`);
    await page.screenshot({ path: fileURLToPath(new URL(`labels-${width}.png`, out)), fullPage: true });
  }
  assert.equal(posts, 2);
  assert.deepEqual(errors, []);
  console.log('PASS: pending/retry/synced, primary label with independent history flags, model explanation, 1440/768/390/320px, no page errors.');
} finally { await browser.close(); }
