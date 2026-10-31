// Run with: SHOP_PACK_PATH=... node --max-old-space-size=96 --expose-gc --import tsx qa/studio/milo-memory.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { workspaceReader } from '../../server/analysis-tools.ts';
assert(process.env.SHOP_PACK_PATH && global.gc, 'Provide a sanitized source pack and enable --expose-gc.');
const data = JSON.parse(await readFile(process.env.SHOP_PACK_PATH, 'utf8'));
const document = JSON.stringify({ revision: 1, data, updatedAt: '2026-10-31' });
const read = workspaceReader({ fetch: async request => new Response(request.url.endsWith('/sources')
  ? document : '{"revision":1,"data":{"hour":24,"startedAt":null}}') }, 'https://test');
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
