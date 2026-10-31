# Python backend on Cloudflare

The existing `megadaytwin` web Worker calls the private `megaday-decision-backend`
Worker through `DECISION_BACKEND`. The latter routes all requests to one Container
named `admin`. Both Workers have `workers_dev=false`; the Python service has no
public domain or route. The web session gate supplies identity/role headers.
The existing admin/judges accounts share a demo workspace and lead privileges;
this is not tenant isolation or a production multi-shop authorization design.

## One-time account setup (before merging the deployment change)

1. Enable Workers Paid / Containers and R2 on Cloudflare account
   `1177b93da7312e51a226b6ecffb1e251`. Usage is billable; the base plan is not a cost cap.
2. Authenticate Wrangler locally (`cd web; npx wrangler login`) or provision a
   scoped Cloudflare API token in the GitHub `production` environment as
   `CLOUDFLARE_API_TOKEN`. Never put the token in source or chat. The token needs
   deployment access to Workers, Containers/images, Durable Objects and R2 in
   this account; use the current Cloudflare deployment token permissions.
3. Create the private bucket once:
   `npx wrangler r2 bucket create megaday-decision-state`.
   Do not enable public bucket access. Do not add automatic deletion to its state object.
4. Run `.github/workflows/deploy-backend.yml` on this branch to provision the
   backend before publishing the web service binding. GitHub's Linux runner has Docker.
   Local deployment requires a running Docker engine (`npm run deploy:backend`).
5. After backend deployment succeeds, merge the reviewed change. `deploy.yml`
   deploys and smoke-tests the backend before promoting the web build. Future
   changes to Python also trigger deployment.

Optional: set `OPENAI_API_KEY` on **megaday-decision-backend** if Python AI tools
are needed (`npx wrangler secret put OPENAI_API_KEY --config decision-backend/wrangler.jsonc`).
The web Worker's existing AI key does not automatically copy to this service.
Without this optional key, deterministic calculations work and Python AI is disabled.
The web campaign-explanation feature continues to use its existing web AI key.

## State and replay semantics

* Docker builds an initial demo database from exactly the six historical source
  files. The image excludes `live_11_11`, evaluator data, secrets and developer DBs.
  No goal is silently applied. Upload the replay ZIP through Data Studio as usual.
* The private virtual host `http://state.internal/state` is intercepted by
  `ContainerProxy` and accesses R2. It is not an Internet endpoint. No R2 credentials
  are passed to Python.
* On boot, Python restores `admin/state-v1.zip` before starting FastAPI. Only an
  actual 404 initializes from the seed. Corrupt/unavailable state blocks startup.
* HTTP mutations and automatic replay hours share a gate. Background recompute
  settles, DuckDB checkpoints, and DB + uploaded replay + live files are uploaded
  before the mutation response is acknowledged. Upload failure returns 503 and
  blocks subsequent operations until persistence succeeds. Refresh before retrying
  a mutation whose response was lost; it may already have committed.
* R2 ETags prevent an obsolete container from overwriting a newer snapshot.
  A revision conflict requires restarting the stale container to restore current
  state, rather than retrying with unconditional overwrite.
* After restart, replay is **paused at the last saved hour**. The user resumes it.
  Automatic resume is deliberately not inferred from wall-clock downtime.
* The hosted process disables independent filesystem polling. Replay controls
  still ingest each hour and trigger the normal recompute pipeline.
* Snapshots are capped at 96 MiB compressed, 2 GiB expanded. This intentionally
  favors the 300-SKU demo and correctness over high write throughput. Larger data
  or multiple shops require a different persistence design. R2 snapshots are not
  a substitute for a separately retained backup/recovery policy.

The newer City workspace replay and Python decision replay remain separate
pipelines. Hosting Python does not automatically synchronize their clocks. Verify
the HQ Python `dataAsOf` when demonstrating refresh and recommendations.

## Verification

`npm run check` validates the web and backend Worker bundle. `uv run pytest` covers
snapshot restoration, failed persistence and the existing decision/replay behavior.
The `container` CI job builds the real Linux image and runs
`python scripts/hosted-smoke.py --image megaday-backend-test`: Preview → Apply → ZIP →
hour 0 → calculation → SIGKILL → fresh container → same goal/clock/trace → hour 1.
It uses a local HTTP storage double, not a Cloudflare account or paid AI calls.

After deployment, sign into the real site and verify:

1. `/api/sale-planner/context` (check the current web route) reports a usable backend;
   `/api/v1/health` returns Python `db=ok`. Public `/healthz` only checks the web/D1.
2. CD002 shows its historical data and no auto-applied goal on first boot.
3. Preview and Apply a goal explicitly, then calculate in HQ.
4. Import the replay ZIP in Data Studio, advance the Python replay hour in HQ,
   wait for Milo's update and refresh the calculation.
5. Restart the container and verify goal, uploaded replay and prior trace survive.

Until those authenticated checks pass, a successful image upload is not proof that
the production decision workflow is operational.

For local web/Python development, keep `MEGADAY_LOCAL_BACKEND=1` and the existing
ignored `.dev.vars` URL/user/role. This omits the production service binding.
