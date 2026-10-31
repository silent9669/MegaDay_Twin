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
- Data Studio has six source slots, records, diagnostics, import/export and a
  Live sandbox clock. The clock filters source observations; it never executes
  campaigns, changes source inventory or fabricates economic conclusions.
- Chat has one compact identity/header, readable Markdown, contained table/code
  scrolling, prompt chips and one composer focus ring. The assistant is advisory.

## Six sources

Default observations are the three files under `mock-data/out/demo/live_11_11`;
costs, campaign structure and sale rules are their three parent reference files.
Sources are fetched server-side from this GitHub repository at one resolved main
commit. Paths are fixed, not user-controlled. A sanitized build-time pack from
the same repository is the offline GitHub fallback. Initial load creates the
account workspace only if it does not exist. Refreshing repository sources is
explicit and uses the current revision, never overwrites a saved upload silently.

CSV headers/JSON shape determine the source type. Uploads replace one matching
slot; duplicate source types in one batch are rejected. Shop-folder imports prefer
the live observation files and parent references, excluding history, ground truth
and replay metadata. Canonical six-file packs round-trip. Contact columns/keys
are stripped and contacts in notes masked before cloud storage. Original files
are read-only. Diagnostics expose row, column and code; unknown costs remain
unknown, negative stock is flagged and notes never become instructions.

## Account persistence and sync

The `admin` and `judges` logins use one shared demo workspace stored in a SQLite-backed Cloudflare Durable Object
owned by Worker `megadaytwin`. Sources, settings and sandbox clock are independent
revisioned documents. Writes require a signed HttpOnly session and same-origin
request, validate input server-side and use compare-and-swap revisions. A stale
write returns 409. Sources are chunked below SQLite's row limit and committed in
one transaction. Maximum source transport is 20 MiB.

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

Data Studio's Diagnostics tab describes source-data checks, not application
failures. It separates errors, warnings and information, explains intentional
demo anomalies, and links row-level checks to the corresponding record (including
pagination). CSV positions include the header; JSON positions start at record 1.
Advertising timestamps with explicit timezone offsets do not trigger the missing
timezone warning. These checks preserve original values and do not certify
forecast or profit accuracy.

For the focused browser regression check, run `npm run prepare:sources`, start
Vite on port 5186, then run `node qa/studio/check.mjs` from `web/`.
`STUDIO_QA_URL` overrides the target URL and `STUDIO_QA_CHANNEL=msedge` selects
an installed Edge browser. This check uses mocked account responses and a real
browser; it covers upload, retained invalid values, record navigation, filtering
and 320/390/768/1440px layouts. Results and screenshots are written to the ignored
repository directory `qa/runs/studio/`. It does not verify production persistence.

Run frontend lint, typecheck, unit tests and both builds. Browser checks cover
desktop/mobile compact navigation, six-source bootstrap/import/export/diagnostics,
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
