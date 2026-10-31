# Account workspace and Milo chat release

The city and its account state are now owned by this repository. Keep the
FastAPI/DuckDB pipeline and `/api/v1` governance contracts unchanged. The city
reads sanitized repository sources and clearly labeled fixtures until that
backend is configured; Milo is advisory and cannot execute business actions.

Use one SQLite Durable Object per authenticated account. The current access
model has one shared admin account. Sources, settings, playback clock and chat
history are independently revisioned documents. Same-origin authenticated writes
use compare-and-swap; WebSocket notifications cause other devices to refetch.
Chat messages save before generation, and assistant replies save server-side.
A stale session tail is rejected rather than overwritten. Browser drafts remain
local until submitted.

Deploy this repository's build to the existing `megaday-hackathon` Worker to
retain its write-only OpenAI and authentication secrets. Keep the deployed
scrypt password format and `megaday_session` cookie valid; local PBKDF2 hashes
also work. Point `hackathon.dangphuc.me` directly at this Worker. Keep the prior
`hackathon` gateway and D1 database for rollback. Rollback versions and steps are
in `web/README.md`; no database or secret is deleted during release.

CI now generates the ignored source fallback before typechecking and uses
Chromium to test the rendered login, private chat access and D1 health. Static
HTML text cannot verify controls rendered by the Vite app. Release verification
includes unit tests, both builds, browser tests on two independent sessions and
signed-in checks after deployment. Local browser model responses are simulated;
production model discovery/generation is verified separately with the preserved
Worker secret.
