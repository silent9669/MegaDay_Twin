# Saigon city validation

Validated locally against the Cloudflare production build, including its content policy. The existing Vite development preamble is blocked by that policy, so browser QA uses a local Wrangler preview of the built Worker. No production secrets or production data were used.

## Checks

- `NODE_OPTIONS=--no-experimental-webstorage node scripts/check.mjs web`: deployment regression tests, lint, 227 Vitest tests, typechecked Cloudflare build, deployment dry run.
- `node web/qa/saigon.mjs`: 1440×900, 1024×768, 768×1024, 390×844, and 844×390; bounded panels, keyboard containment on mobile, retained drafts, login layout, seven-step tutorial with river/lighting actions, quiet two-tab Milo speech bubbles, source-row navigation, and persistence across city/Data Studio navigation.
- Supplemental checks: all four ad brands, three city/Data Studio navigation cycles with one canvas remaining, failed-GLB fallback, and district navigation with WebGL disabled.
- Morning/Night survives reload, themes portaled panels, leaves the source clock unchanged and reuses the canvas. Reduced motion: scene screenshots taken apart have identical hashes in both modes.
- Floating map labels navigate to HQ and Landmark 81; the Explore menu retains landmark access on narrow screens.
- No console errors in the production-preview browser checks. The final mobile selected-label adjustment was additionally verified with a focused narrow-screen landmark check. Live model replies are covered by mocked request/storage integration tests; no paid OpenAI call was made in this QA run.
- glTF validation: no errors or warnings. The validator reports that it cannot inspect `EXT_meshopt_compression`; actual decoding and rendering were verified in Chrome.

## Render measurements

| Profile | Result |
| --- | --- |
| Expanded architecture GLB | 698,140 bytes, shared 2048px AO atlas, Meshopt + WebP |
| All Milo PNGs | 24,103 bytes |
| Default desktop scene | 124 draw calls, about 187k rendered triangles |
| All four ad platforms, overview | 157 draw calls, 188,604 rendered triangles |
| Chrome, Apple M3 Pro, 1440×900 | 120 FPS; 9.4 ms p95 frame interval |
| Chrome, 390×844 @2×, CPU slowed 4× | 120 FPS; 9.8 ms p95 frame interval |
| Headless software rendering | 9 FPS, 150 ms p95; functional checks pass |

CPU throttling retains the host GPU. This is not a measurement on a physical low-end phone. The app retains district navigation when WebGL is unavailable and a low-detail scene while architecture loads. Asset download and geometry counts meet the planned budgets; performance on other hardware still varies.

## Review images

![Login](saigon-preview/login.jpg)
![Expanded Saigon city](saigon-preview/city.jpg)
![Night view](saigon-preview/night.jpg)
![Landmark 81](saigon-preview/landmark81.jpg)
![Milo notification](saigon-preview/milo-notification.jpg)
![Branded ad buildings](saigon-preview/ads.jpg)
![Milo on mobile](saigon-preview/milo-mobile.jpg)
![Inventory inspector on mobile](saigon-preview/warehouse-mobile.jpg)

## Reproduce

Build with `npm --prefix web run build:cloudflare`. Run the generated Worker/assets locally with the account Durable Object and D1 bindings, using local-only auth credentials. Then run `QA_BASE_URL=http://127.0.0.1:5179 QA_PASSWORD_FILE=/private/path/to/local-password node web/qa/saigon.mjs`. The script rejects non-local hosts, temporarily replaces only the local inventory fixture, restores it, and writes results/screenshots under ignored `qa/runs/saigon-city-milo/`.

The default GitHub workflow is manual, as configured on main. These are local results, not a claim that hosted CI ran or that production was deployed.
