// Run with: SHOP_PACK_PATH=... node --max-old-space-size=96 --expose-gc --import tsx qa/studio/milo-memory.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { workspaceReader } from '../../server/analysis-tools.ts';
import { buildMiloContext } from '../../server/milo-context.ts';
import { backendImport } from '../../server/backend-import.ts';
assert(process.env.SHOP_PACK_PATH && global.gc, 'Provide a sanitized source pack and enable --expose-gc.');
const data = JSON.parse(await readFile(process.env.SHOP_PACK_PATH, 'utf8'));
const document = JSON.stringify({ revision: 1, data, updatedAt: '2026-10-31' });
const storage = { fetch: async request => new Response(request.url.endsWith('/sources')
  ? document : '{"revision":1,"data":{"hour":24,"startedAt":null}}') };
const context = await buildMiloContext({ sku: 'GD0003' }, {}, storage, 'https://test');
assert.equal(context.workspace.sources.length, data.files.length);
assert(context.workspace.orderRowsAtSandboxHour > 100000);
const transfer = backendImport(data.files).body.getReader();
let transferredBytes = 0;
for (;;) { const {done,value} = await transfer.read(); if (done) break; transferredBytes += value.byteLength; }
assert(transferredBytes > 20000000);
const read = workspaceReader(storage, 'https://test');
global.gc();
const before = process.memoryUsage().heapUsed;
const catalog = JSON.parse(await read('workspace_catalog', '{}'));
assert.equal(catalog.files.length, data.files.length);
global.gc();
const retainedMiB = (process.memoryUsage().heapUsed - before) / 1048576;
assert(retainedMiB < 32, `Reader retained ${retainedMiB.toFixed(1)} MiB after catalog.`);
for (const file of catalog.files) {
  const page = JSON.parse(await read('read_source_rows', JSON.stringify({ file: file.name, query: '', offset: 0 })));
  assert.equal(page.totalRecords, file.records);
  assert(page.rows.length <= 30);
}
console.log(JSON.stringify({ tables: catalog.files.length, records: catalog.files.reduce((n, f) => n + f.records, 0), retainedMiB }));
