# Saigon city and Milo change monitoring

The city remains a read-only illustration of the account's imported sources and shared sandbox clock. It does not model geography, forecast traffic, move stock, alter campaigns, or execute decisions. Bitexco is a separate riverfront landmark; MegaDay Twin is a modest signed office. The river, boats and street activity are illustrative.

Keep React 18 and plain Three.js. Animate UI 2.x requires React 19, so this change uses compatible Motion primitives and the official shadcn Base UI Sheet/Tabs source instead of changing the app's React version. Sheets contain keyboard focus on mobile; the district inspector and Milo occupy the same panel area. The existing M favicon supplies the shared brand mark.

The Blender source, packed AO atlas, original model provenance, and PixelLab job provenance live in `tools/assets/`. Only the compressed GLB and sprite images ship to browsers. The content policy permits `wasm-unsafe-eval` specifically for Meshopt and `blob:` connections for embedded glTF images; JavaScript `unsafe-eval` remains disallowed.

Milo monitoring is deterministic, session-local and in-app. The existing source/sandbox revision feed establishes a silent baseline, batches changes for one second, ignores duplicate or regressing revisions, and retains the latest 100 notices. Reconnect compares the last observed snapshot with the current one. Rewinding the sandbox resets comparison and emits a simulation notice. Diagnostics use the existing parser's bounded result, not an exhaustive audit. There are no background AI requests, automatic speech, push notifications or mutations.

“Explain” attaches a bounded change reference and fills an empty draft; an existing draft is preserved. Only Send calls the assistant. The server loads current source values from authenticated account storage and explicitly marks client-provided historical observations as unverified. A revision mismatch is reported as stale evidence. An explanation is advisory and cannot perform a decision.

No production deployment or merge is part of this PR. Validation evidence and its limitations are recorded in `docs/city-demo/saigon-validation.md`.
