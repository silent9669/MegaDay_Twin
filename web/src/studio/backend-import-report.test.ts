import { expect, it } from 'vitest';
import { backendImportReport } from './backend-import-report';

const batch = { file_name: 'inventory.csv', status: 'loaded', batch_status: 'ok', rows_held: 0, issues: {} };
const report = (batches: unknown[], pending_aliases = 0) => ({ data: { batches, pending_aliases } });

it('confirms loaded and already ingested files', () => {
  expect(backendImportReport(report([batch, { ...batch, status: 'skipped' }]))).toEqual({
    canSyncLabels: true, message: 'Import confirmed. 1 file(s) loaded; 1 already imported.',
  });
});

it.each(['failed', 'unsupported', 'needs_sku_review'])('does not claim success for HTTP success with %s', status => {
  expect(backendImportReport(report([batch, { ...batch, status }])).canSyncLabels).toBe(false);
});

it('reports held rows and unresolved identities rather than silently syncing labels', () => {
  const result = backendImportReport(report([{ ...batch, rows_held: 7 }], 2));
  expect(result.canSyncLabels).toBe(false);
  expect(result.message).toContain('7 row(s) held');
  expect(result.message).toContain('2 SKU mapping(s) need confirmation');
});

it('does not hide data issues on previously imported files', () => {
  expect(backendImportReport(report([{ ...batch, status: 'skipped', batch_status: 'ok_with_issues' }])).message).toContain('needs attention');
  expect(backendImportReport(report([{ ...batch, issues: { ROW_INVALID: 3 } }])).canSyncLabels).toBe(false);
});

it('keeps normalization warnings visible without blocking synchronization of valid labels', () => {
  const result = backendImportReport(report([{ ...batch, batch_status: 'ok_with_issues', issues: { MONEY_FORMAT: 2 } }]));
  expect(result.message).toContain('needs attention');
  expect(result.canSyncLabels).toBe(true);
});

it.each([undefined, {}, { accepted: true }, report([]), report([{ status: 'loaded' }])])('refuses unconfirmed response shapes', value => {
  expect(() => backendImportReport(value)).toThrow('Cannot confirm');
});
