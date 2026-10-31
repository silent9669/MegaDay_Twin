# Campaign HQ — CD002

Built on main `13d298a`; the goal planner and contextual Milo are reused. Open
`/campaign-hq`, or MegaDay HQ → **CD002 · Phân tích và đề xuất**.

## Flow

1. Ingest seller sources into Python and confirm a campaign goal through the existing
   sale planner. HQ never creates or edits that goal.
2. Open CD002. Overview shows campaign SKUs, reported ad spend, active goal and separate
   source timestamps. Sources saved only in Data Studio are not yet analytical inputs.
3. **Cập nhật & đề xuất lại** reads the latest backend revision, verifies the same active
   goal, simulates the remaining whole hours (1–72), persists the scenario and shows the
   two agreed tables. The first table uses saved daily unit economics; the second uses
   actual per-hour simulated sales/prices. It does not multiply sales by a guessed price.
4. Milo selects relevant reasons and flip conditions from the simulator's evidence.
   AI cannot generate new amounts, choose a different action, or execute ads. Without AI,
   the UI explicitly shows the simulator's original reasons. Contextual chat remains
   available for the saved scenario.
5. Python replay/ingest changes the backend revision. A watcher polls every 15 seconds
   while City is open and adds one coalesced Milo notification. The old table remains
   visible and marked outdated. The bell also stays accessible on full-page workspaces.
6. Refresh computes a new snapshot. The last five HQ runs are available from persisted
   backend history; the UI compares the prior and new recommendation without subtracting
   financial results across different time windows.

## Data contract and limits

### Replay ZIP demo

Upload **live_11_11.zip in Data Studio → Import ZIP**. Studio recognizes `_replay.json`
and stages the original archive through `POST /api/v1/live/replay-archive`; it never
imports the companion whole-day CSVs as current observations or sends them to AI.
Existing Studio documents, analytical timestamps and confirmed goals remain unchanged
by upload. `GET /live/replay-archive` returns only day, lag, counts and archive hash.

The supplied ZIP matches the repository demo: 2026-11-11, two-hour ads delay, 4,156 order
rows, 1,176 ads rows and 7,176 inventory rows. History/campaign/cost must already be ingested.
ZIP is capped at 20 MiB compressed/expanded, paths/file names and timestamp buckets are
validated; ground truth and arbitrary targets are rejected. Only `_replay.json` is saved
under a database-specific private directory, with fixed server-owned paths.

After upload, follow the Studio link to CD002 → **Replay demo**. Start/stop, one-hour
steps and explicitly confirmed reset reuse `/live/control`. Each step completes ingestion
before the next interval; stop waits for the in-flight step. The uploaded source and last
ingested hour survive a backend restart; replay does not automatically resume. For local
development use a private `LIVE_DIR` under ignored `backend/data/`, separate from repo data.

At hour 0, only orders/inventory through 00:59 are loaded (displayed exclusive mark 01:00).
Ads for hour 0 arrive at replay hour 2. Milo's watcher then notices the changed revision;
**Cập nhật & đề xuất lại** recalculates from that snapshot. The existing orchestrator can
update forecasts in the background, but HQ's saved result stays unchanged until refresh.
A hard budget/loss target may wait for missing ads observations; no missing ads become zero.

Local browser smoke: `REPLAY_ZIP` points to the user's ZIP, `LOCAL_DEMO_PASSWORD` is the
local password; run `node qa/studio/replay-local.mjs` with the backend and built preview
running. It verifies Studio upload, unchanged source documents/goal, one-hour ingestion,
and Milo notification. It advances a fresh disposable local demo by one hour; no AI calls.

- GET `/api/v1/campaign-analysis/CD002`: live campaign/goal, source timestamps, revision,
  permissions and saved HQ runs. No fixture fallback. Revision conservatively includes
  all ingest batches and parameters, so an unrelated import can also request review.
- POST `/api/v1/campaign-analysis/CD002/refresh`: `{revision, goal_id}`; operator or lead.
  Under the shared live/simulation locks, mismatched inputs return 409. The original
  goal is preserved; run-specific progress is stored in the trace. Results are what-if
  snapshots and do not overwrite the active automated recommendation.
- The existing goal must contain a valid timezone-aware `params.planner_start`, as
  supplied by the merged goal planner. Expired goals or horizons beyond 72 hours block.
- Whole-period quantity targets credit observed non-cancelled/non-returned order lines;
  open orders remain provisional. Whole-period spending caps credit already recorded
  spend. Missing hourly ads block refresh when a spending/loss constraint depends on
  them. No missing hour is silently treated as zero for those constraints.
- Loss constraints include recorded contribution minus recorded ads before the refreshed
  horizon. Historical costs/fees follow the existing economics loader; this is a model
  calculation, not audited accounting. J also includes terminal stock value/penalties;
  the table's after-ads contribution excludes both.
- Bundles hold no independent stock. Ending physical inventory already subtracts bundle
  component use. Unknown stock stays unknown in the table. Infeasible pause columns show
  the commitment restriction instead of actionable financial totals.
- New trace fields: action `revenue`, `selling_cost`, `after_ads`; per-SKU `revenue` and
  `leftover_units`. These are additive to trace version 1. Old traces show missing evidence
  rather than fabricated values.
- Replay still uses the existing Python controls/file watcher. City sandbox time does
  not trigger Python ingestion. Watching a page does not call paid AI; clicking refresh
  may call the configured decision model after simulation succeeds.

## Deployment and verification

For a local browser preview with the real Worker authentication, run
`npm run build:cloudflare`, then
`npx vite preview --mode cloudflare --host 127.0.0.1 --port 5173 --strictPort`.
Use the local credentials/backend URL in the ignored `web/.dev.vars` file and run
Python separately. This serves compiled public assets; Vite's development modules
are blocked by the production login gate and its CSP, which otherwise leaves the
login page blank. Keep the production authentication policy unchanged.

Deploy both Python and web. Existing backend migration 5 is sufficient; no new migration.
Configure the same `DATA_BACKEND_URL` (including `/api/v1/`), `DATA_BACKEND_USER` and
`DATA_BACKEND_ROLE` used by the planner. AI uses `OPENAI_MODEL_DECISION` or `OPENAI_MODEL`
and the existing API key. No secrets are required to inspect the UI or run the tests.

Tests cover hourly prices, combo stock reconciliation, completed target credit,
spent-budget credit, missing ads, flash-sale restrictions, active-goal invalidation,
real Python scenario persistence, retained results after refresh errors and AI evidence
selection. Browser QA: start Vite on 5186, run `node qa/studio/campaign-hq.mjs`
(`STUDIO_QA_CHANNEL=msedge` on Windows). Browser QA uses mocked APIs, not live AI or
production data; screenshots under `qa/runs/campaign-hq/` are illustrative.
