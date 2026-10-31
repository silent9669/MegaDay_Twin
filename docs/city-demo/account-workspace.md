# Account workspace and repository-owned city

This revision supersedes the external CITY_UI deployment boundary. All frontend,
access, source-workspace and deployment code lives in this repository's `web/`.
The existing FastAPI/DuckDB packages and `/api/v1` contracts remain the owners of
normalization, SKU matching, forecasting, simulation, permissions and economics.

## Experience

- The city is the entry surface. Compact navigation always exposes View city,
  View settings and Data Studio, with keyboard labels and tooltips.
- View settings contains Signal core and motion/render controls. No manual
  Accessible map option. A WebGL failure still leaves district controls usable.
- No 11.11 monitor or Engine workspace in the product navigation or Data Studio.
- Data Studio starts empty and supports up to 24 business/general tables, records,
  diagnostics and ZIP/CSV/JSON imports. Import shop folder loads the six baseline
  tables in one click and saves them to the account. A sandbox clock appears for
  replay sources. The clock filters event observations; it never executes
  campaigns, changes source inventory or fabricates economic conclusions.
- Chat has one compact identity/header, readable Markdown, contained table/code
  scrolling, prompt chips and one composer focus ring. The assistant is advisory.

## Imported tables and six business formats

Import shop folder loads the six root files under `mock-data/out/demo`, excluding
`live_11_11` and ground truth. The sanitized, compressed pack is generated during
the build and served by authenticated `GET /api/studio/shop`; no folder picker,
GitHub fetch or local filesystem access is needed. It replaces matching business
tables and preserves unrelated tables and loaded event rows. It does not restart
an active event clock. Initial load creates an empty
account workspace only if it does not exist. Older accounts remove exact repository
defaults once (including only-derived labels); uploads and edits are preserved.
The shop baseline is stored in a separate `shop-sources` database document alongside
the combined `sources` view used by the city. Existing account data is snapshotted
once without replacing it with demo files. Event imports do not overwrite that
snapshot. It persists across refreshes, sessions and Worker deployments.

Data Studio has separate confirmed **Delete shop data** and **Delete event data**
actions. `DELETE /api/workspace/sources` accepts `{scope: "shop" | "event", revision}`.
Deleting an event restores the saved shop snapshot and stops playback atomically.
Deleting the shop retains event rows and the clock; later event deletion cannot
resurrect the deleted shop. Table deletion is available only for unmixed tables.
Scoped baseline imports use `PUT /api/workspace/sources` with `scope: "shop"`.
All saves/deletions retain revision checks and account authentication.

CSV headers/JSON shape determine the source type. Uploads replace one matching
business table; general tables are keyed by filename. ZIP analysis retains general
CSV/JSON tables and uses one configured model call to map unambiguous headers
and review each schema with bounded samples. Sample review results are advisory;
deterministic format checks still inspect all records.
Duplicate business source types in one batch require selection. Import the separate
11.11 ZIP after loading the baseline to start event playback. Canonical six-file
packs round-trip. Contact columns/keys
are stripped and contacts in notes masked before cloud storage. Original files
are read-only. Diagnostics expose row, column and code; unknown costs remain
unknown, negative stock is flagged and notes never become instructions.

## Account persistence and sync

The `admin` and `judges` logins use one shared demo workspace stored in a SQLite-backed Cloudflare Durable Object
owned by Worker `megadaytwin`. Sources, settings and sandbox clock are independent
revisioned documents. Writes require a signed HttpOnly session and same-origin
request, validate input server-side and use compare-and-swap revisions. A stale
write returns 409. Sources are chunked below SQLite's row limit and committed in
one transaction with the `studio_tables` catalog and `studio_rows` records.
Unchanged table rows are retained on saves. ZIP loading saves immediately; cell
edits keep autosave. Maximum source transport is 20 MiB.

Authenticated WebSockets broadcast revision notifications immediately. Other
devices refetch the changed document. An open editor with local changes keeps its
draft and shows a conflict with explicit reload/export choices. Reconnect and
window focus reconcile revisions. Failed saves retain the draft; no "synced"
status until server acknowledgement. Sandbox playback is local to the open Data
Studio, publishing its clock to the account; remote browsers observe and do not
start a second playback timer. Changing login session secret invalidates access.

Chat history and the preferred model are stored in a separate `chats` account
document. The compact Milo panel has New chat, searchable History, expand and
close controls, with the model picker beside the composer. There is no trash
button. Devices select conversations independently; saved messages and model
preferences reconcile through the existing account notifications. Unsent text
stays in the current browser until submitted. A failed save retains that draft.

The user message is saved before contacting OpenAI. `/api/pm/chat` accepts a
session ID and last-message ID; it reads context from account storage and saves
the assistant reply server-side before returning. Revision checks preserve edits
to other sessions and reject stale edits to the same conversation. Unanswered
saved messages can be retried after reloading. The archive is bounded at 100
sessions and 200 messages per session; older sessions are never silently evicted.
The existing access model is one shared admin account, not separate user tenants.

Model discovery uses OpenAI's `/v1/models` for the configured server key. Text
model families are no longer restricted to eight entries or numeric-only model
names. Audio, realtime, image, transcription, search and coding-only models are
excluded. Model discovery does not guarantee a successful generation: provider
permissions, quota or endpoint compatibility may still reject a request, which
keeps the saved user message available for retry. `OPENAI_API_KEY` must be set on
the Worker; the local value is currently empty.

The hosted source workspace does not imply a hosted Python pipeline. Optional
`DATA_BACKEND_URL` delegates uploads and reads to the existing FastAPI origin
with a configured server identity. When absent, the UI labels source previews
and existing API fixtures honestly; no live ingestion or causal iROAS is claimed.

## Verification

Warehouse's **Label data** action uses the existing OpenAI secret and a separately
configured Luna decision model. Warehouse and the decision model include only
products with positive latest stock. Search and Label data remain visible;
assessment/evidence live in collapsed Label settings. Identical completed or
partial runs are reused; new evidence starts a new run. It classifies inventory into Trendy,
Seasonal or Normal using market, date and supplied evidence. Insufficient evidence
leaves the item unclassified and displays the missing evidence. Successful/partial
runs atomically persist a read-only `Demand label` column into the inventory table
and source CSV, plus the run's evidence/reasons. Data Studio and exports show the
same column. Editing/reimporting sources clears derived labels. Revision checks,
leases and fingerprints reject stale/concurrent results. Only the six business
formats, without derived labels, are forwarded to the existing Python backend;
its SKU matching, forecasting and economics remain unchanged.

Data Studio's Diagnostics tab describes source-data checks, not application
failures. It separates errors, warnings and information, explains intentional
demo anomalies, and links row-level checks to the corresponding record (including
pagination). CSV positions include the header; JSON positions start at record 1.
Advertising timestamps with explicit timezone offsets do not trigger the missing
timezone warning. These checks preserve original values and do not certify
forecast or profit accuracy.

Repeated checks are grouped by severity and code, with expandable record details.
Recognized nonnegative VND formats (for example `45k` -> `45.000 ₫`) are information
notices with display-only previews, not hundreds of manual correction tasks.
Unreadable/negative amounts still require review; missing cost remains an error.
Previews never mutate source text or certify ingestion. The bounded check list
prioritizes errors and warnings after scanning all rows so repeated format notices
cannot hide an error later in a file.

The workspace shows demo versus uploaded/edited provenance per source. The six
source formats are four CSV files and two JSON files, not six arbitrary CSVs.
Rejected imports report file-count or size limits separately and retain existing
sources. Partial imports replace only the matching source kinds.

For the focused browser regression check, run `npm run prepare:sources`, start
Vite on port 5186, then run `node qa/studio/check.mjs` from `web/`.
`STUDIO_QA_URL` overrides the target URL and `STUDIO_QA_CHANNEL=msedge` selects
an installed Edge browser. This check uses mocked account responses and a real
browser; it covers upload, retained invalid values, record navigation, filtering
and 320/390/768/1440px layouts. Results and screenshots are written to the ignored
repository directory `qa/runs/studio/`. It does not verify production persistence.

Run frontend lint, typecheck, unit tests and both builds. Browser checks cover
desktop/mobile compact navigation, empty/partial/general-table import/export/diagnostics,
two-session realtime updates, optimistic conflicts, reload persistence, chat
Markdown and responsive containment. Hash all six original inputs before/after.
Deploy this repo to Worker `megadaytwin`; the custom domain points directly to it. No external repository or CITY_UI binding is needed by the deployment.

Chat verification: `NODE_OPTIONS=--no-experimental-webstorage npm test` passes
123 tests on local Node 26 (the option avoids Node's experimental storage global
interfering with jsdom). Lint, typecheck and both builds pass; builds report
existing dependency annotation and bundle-size warnings. `web/qa/chat/check.mjs`
tests two independent browser sessions against a local production Worker with
real SQLite storage and WebSocket updates. Only model discovery/generation are
simulated. It covers history after reload, model preferences, Markdown, stale
writes, auth/CSRF, and containment at 320/390/768/1440px. Results and screenshots
are under `web/qa/chat/`. Live OpenAI and hosted deployment are verified separately during release; local browser checks use a simulated model service.

## Saigon city and in-app updates

The header and login share the existing M favicon. The login uses original District 1 vector artwork. The expanded city contains a separate Bitexco landmark, Saigon River vessels, Ben Thanh Market, the Post Office, shophouses and cafés, and a modest signed MegaDay Twin office. Ad buildings retain their platform logos and colors.

Milo has Chat and Updates tabs, PixelLab sprite states, and static reduced-motion imagery. Changes remain in this open app session (latest 100), with an unread badge and quiet important-change toast. Source imports, source checks, stock zero/unknown/recovery, observed counts, and the simulation clock are monitored without automatic model calls. Rewinds reset comparison; reconnects report aggregate differences. Explain attaches context for a user-sent request and preserves an existing draft. See [validation](saigon-validation.md) and [decisions](../quyet-dinh/saigon-city-milo.md).
