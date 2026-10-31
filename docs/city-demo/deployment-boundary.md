> Historical deployment design, superseded by [account workspace](account-workspace.md) and [current deployment](../../web/README.md#cloudflare). On 2026-10-31, the old Workers, hostname and D1 were deleted and replaced by `megadaytwin`, `megadaytwin.dangphuc.me` and `megadaytwin-db`.

# City frontend and backend boundary

The immersive city is maintained independently in [megadaytwin PR #16](https://github.com/AIVIETNAM-AIO-QP09/megadaytwin/pull/16). It is a separate Next.js/Three.js sample demo, deployed as Worker `megaday-hackathon`. This repository retains React/Vite, FastAPI/DuckDB, its generated API types, and the M0–M5 plan. No migration of the backend stack or schema is implied.

Worker `hackathon` continues to own `hackathon.dangphuc.me` and D1 binding `DB` (`hackathon-db`). Its `CITY_UI` service binding forwards the city, login, mock-data, Next assets and sample bundle routes to the city Worker. `/healthz` still checks the existing D1 connection. The original Vite overview is available at `/?workspace=operations`; its other routes keep their existing handlers after the city validates the access session.

The city reads its existing sample engine, not the new FastAPI model or the generated 300-SKU dataset. Visual signal particles, workflow previews and warehouse motion must not be represented as backend execution, inventory transactions or validated iROAS. Integration with `/api/v1`, SSE, freshness/confidence envelopes, governance roles and the six story SKUs remains work for the plan's owning modules. The sample and API fixture workspaces are deliberately separate until those contracts are implemented.

## Access

The user requested a password-protected hosted demo. A salted scrypt password hash and random HMAC session secret are stored only as secrets on `megaday-hackathon`. Login uses same-origin POST, generic errors and Cloudflare rate limiting. Session cookies are HttpOnly, Secure on HTTPS and SameSite=Strict, with a 12-hour expiry. Logout clears the cookie. Changing the secret invalidates all sessions. Static art is public; app, sample datasets and APIs require the signed session.

This access gate does not replace or grant `X-User` / `X-Role` governance rights described in [API](../dev-plan/08-api.md) and [governance](../dev-plan/10-quan-tri-va-bao-mat.md). Those backend contracts stay intact. No passwords or keys are committed in either repository.

## Deployment

1. Verify and deploy the city Worker from its repository using `npm run deploy`; provision `ADMIN_PASSWORD_HASH` and `SESSION_SECRET` through Wrangler secrets. Secrets persist across ordinary deployments.
2. Build this repository with `cd web && npm run build:cloudflare`, then deploy the verified gateway using `npm run deploy:cloudflare`.
3. Check `/healthz`, signed-out redirect to `/login`, invalid and valid login, `/city-demo`, `/mock-data`, the original operations workspace and logout.

Existing CI builds and verifies this repository before deploying its gateway on main. Its existing `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` secrets remain the only deployment credentials needed here. The city repository deploys its service independently. Neither deploy deletes or migrates `hackathon-db`.

## 11.11 data adapter revision

See [read-only 11.11 bootstrap](live11-import.md). The optional city `/api/data-backend` adapter is forwarded to CITY_UI, while `/api/v1` remains owned by this stack. The warehouse can now display team-API stock and product names, with a dated pipeline-generated checkpoint when no Python origin is configured. Decision execution is not migrated to the city. Neither Worker is redundant: `hackathon` still owns the domain/D1 and `megaday-hackathon` serves Next UI, access and PM.
