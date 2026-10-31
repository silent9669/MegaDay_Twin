# Workspace response performance — 2026-10-31

The City, assistant and HQ mounted independent readers for the same account documents. A production browser observation showed four source downloads of about 5 MB each, with individual requests taking 2.3–4.2 seconds. Identical revisions also replaced client objects and repeated derived computations.

## Changes

- Share each mounted workspace document, its in-flight read and its change subscription. Release it after the last consumer unmounts. Preserve object identity for unchanged revisions and reject stale responses after newer saves.
- Reconcile notifications and explicit refreshes that arrive during a read. Save conflicts still reach the caller; websocket, focus and reconnect reconciliation remain enabled.
- Revalidate with revision ETags. Unchanged GETs return an empty 304 after reading only metadata. Access gates and `Cache-Control: no-store` remain in force; no public/shared account-data cache is introduced.
- Check baseline-snapshot existence through metadata instead of parsing the entire saved shop on every source read.
- Classify note columns once per table, retaining privacy masking and all validation passes.
- Skip order filtering in HQ, return all orders directly for an empty neighborhood search, and resolve searchable columns once per query.

## Measurements

Local production build, bundled baseline shop, Chromium, desktop viewport 1440×1000. Three loads per revision, same local Worker storage and dataset. `web/qa/performance-local.mjs` records source transfer sizes, product availability, long tasks and page exceptions.

| Measure | Before | After |
| --- | --- | --- |
| Source response body bytes per load | 19,645,792 | 4,911,448 |
| Source reads on initial load | Four full responses | One full response + one empty 304 |
| Source response body bytes on unchanged focus refresh | Up to 9,822,896 | 0 |
| Source inspection, three runs with Node CPU profiler | 3,035 / 2,922 / 2,978 ms | 1,418 / 1,421 / 1,433 ms |
| Warm HQ component render, three runs | 128 / 261 / 145 ms | 17 / 13 / 7 ms |
| Warm neighborhood component render, four runs | 125 / 107 / 114 / 101 ms | 11 / 9 / 10 / 9 ms |

The transfer reduction is 75%. Isolated source inspection improved about 52%. Whole-page product readiness ranged from 8.2–8.9 seconds before to 4.9–9.3 seconds after; shared-machine load and software rendering made wall-clock timing noisy, so this is not a production latency guarantee. Page-load benchmarks reported no JavaScript exceptions. The local Python/AI backend is not configured in this harness; backend timing is not measured.

## Reproduce

From `web/`, build with `npm run build:cloudflare`, then run `node qa/studio/serve.mjs` (default local port 5187). Use an unused port if another worktree already owns it. In a separate terminal:

```sh
QA_BASE_URL=http://127.0.0.1:5187 QA_SEED=1 QA_LABEL=after node qa/performance-local.mjs
```

The harness refuses non-localhost origins. `QA_SEED=1` imports the bundled shop into the isolated local account; omit it to reuse the dataset. Results go to ignored `qa/runs/performance/`. Use the same dataset and environment for before/after comparisons, and avoid other CPU-heavy jobs while measuring.

## Verification

- `npm run check`: deployment contract tests, lint, frontend tests, TypeScript checks, Cloudflare production build and both deployment dry runs passed.
- Final frontend suite: 492 passed; one existing optional live-archive test skipped because its external archive path was absent.
- Regression coverage includes duplicate readers, StrictMode/remount cleanup, unchanged responses, mid-request notifications/refreshes, stale reads after saves, save conflicts, failure recovery, metadata-only 304s, order searches, and note-column masking/validation.
- Chromium HQ regression passed: product tracking, comparison inputs, saved assumptions, image-bearing mocked chat replies with persistent history, campaigns, notes, warehouse/ads navigation, goal deep links, night mode, and responsive layouts down to 320 px. The note assertion now targets the saved activity log instead of also matching the editor during save completion.
- Chrome DevTools inspection confirmed one full source response plus an empty 304, working Data Studio search, and no horizontal overflow in the inspected desktop/narrow layouts. The standalone local app reports the expected 503 from its unconfigured Python backend; the HQ regression mocks backend/AI responses and finished with no console errors.
- Login controls, authentication gate, private chat storage and local D1 health smoke checks passed.
- A real local Data Studio cell edit autosaved, synchronized over websocket into a second independent browser context, and persisted after reloading that second context.

Changes are local; no production deployment was performed.
