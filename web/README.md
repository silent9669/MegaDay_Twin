# web · MegaDay Twin frontend

React 18 + Vite + TypeScript (strict) + Tailwind 4 + TanStack Query + React Router 7. Code tiếng Anh, mọi chữ trên UI tiếng Việt (nằm ở `src/lib/i18n.ts`). Thiết kế màn hình theo [docs/dev-plan/09-giao-dien.md](../docs/dev-plan/09-giao-dien.md), hợp đồng API theo [08-api.md](../docs/dev-plan/08-api.md).

## Cài và chạy

Yêu cầu Node 22 trở lên (team dùng v24), npm 10 trở lên.

```bash
cd web
npm ci                  # cài đúng phiên bản trong package-lock.json
cp .env.example .env.local   # Windows: copy .env.example .env.local
npm run dev             # http://localhost:5173
```

Khi `npm run dev`, mọi request `/api/*` được proxy sang `VITE_PROXY_TARGET` (mặc định `http://localhost:8000`), nên backend chạy ở cổng 8000 là dùng được ngay, không cần CORS.

## Chạy với API thật

`VITE_USE_FIXTURES=false`, backend ở `VITE_PROXY_TARGET`. Tổng quan ca gọi `/me`, `/freshness`, `/skus`, `/recommendations`; So sánh gọi `/skus/{sku}`, `/campaigns/{id}`, `/recommendations?campaign_id=`, `/recommendations/{id}`, `/scenarios/{id}`, `POST /scenarios/run`, `POST /goals` (lead). Muốn chạy song song với một dev server fixture mà không sửa `.env.local`: tạo `.env.e2e` (`VITE_USE_FIXTURES=false`, `VITE_PROXY_TARGET=http://localhost:8001`) rồi `npx vite --mode e2e --port 5174 --strictPort`. Chi tiết và kết quả chạy thử: [docs/quyet-dinh/M3-web.md](../docs/quyet-dinh/M3-web.md) §11–13.

## Chế độ fixture (chưa có backend)

```bash
# .env.local
VITE_USE_FIXTURES=true
```

Hoặc chạy một lần: `VITE_USE_FIXTURES=true npm run dev` (PowerShell: `$env:VITE_USE_FIXTURES='true'; npm run dev`).

Client không gọi mạng mà đọc JSON trong `src/api/fixtures/`, lấy từ các ví dụ ở 08-api. Thanh trên cùng hiện nhãn "Dữ liệu mẫu" để không ai nhầm với số thật.

| Path | Fixture | Ghi chú |
|---|---|---|
| `/health` | `health.json` | `{"status":"ok","db":"ok","ai":"disabled"}` (01 §Tiêu chí) |
| `/me`, `/freshness`, `/live/status` | `me.json`, `freshness.json`, `live-status.json` | 08 §3.1, §3.13 |
| `/skus` | `skus.json` | dòng NOICHIEN2L đúng ví dụ 08 §3.4; các dòng khác là minh họa theo 6 nhân vật demo |
| `/recommendations`, `/recommendations/rec-551` | `recommendations.json`, `recommendation-rec-551.json` | ca CD002 ngày 11.11: `schedule_hours` thắng |
| `/scenarios/scn-2291` | `scenario-scn-2291.json` | 5 hành động; `pause` và `pause_until_restock` bị `campaign_must_run` |
| `/recommendations/rec-540`, `/scenarios/scn-2280` | `recommendation-rec-540.json`, `scenario-scn-2280.json` | minh họa: CD002 ngày thường, `continue` thắng |
| `/recommendations/rec-510`, `/scenarios/scn-2210` | `recommendation-rec-510.json`, `scenario-scn-2210.json` | minh họa: ví dụ trang 10 (ngưỡng 22,5 → 16,5 khi hàng dư 20.000đ) |
| `/alerts` | `alerts.json` | 08 §3.11 + 2 cảnh báo minh họa |

Màn So sánh: `/sku/NOICHIEN2L/compare`, `/campaign/CD002/compare`, `/campaign/CD002/compare?rec=rec-540`, `/campaign/VIDU-T10/compare?rec=rec-510`; `/compare` liệt kê cả ba. Ở chế độ fixture, thanh trượt chỉ ước tính bằng `src/lib/breakeven.ts`, không gọi `POST /scenarios/run`. Quyết định liên quan: [docs/quyet-dinh/M3-web.md](../docs/quyet-dinh/M3-web.md).

`GET /skus/{sku}` lấy dòng tương ứng trong `skus.json`; `/campaigns/CD002` lấy từ `campaign-CD002.json` (phản hồi thật). Fixture theo đúng hình dạng phản hồi thật (M3-api §5).

Thêm fixture: tạo file JSON trong `src/api/fixtures/` và thêm một dòng vào `FIXTURE_ROUTES` trong `src/api/client.ts`. Query string bị bỏ qua khi tra fixture. Test (`npm test`) luôn chạy ở chế độ fixture.

## Sinh type từ OpenAPI

```bash
npm run gen:api                                   # thử localhost:8000/api/v1/openapi.json rồi /openapi.json
OPENAPI_URL=http://host:8000/api/v1/openapi.json npm run gen:api
```

Kết quả ghi đè `src/api/schema.d.ts`. Khi backend chưa chạy, lệnh báo lỗi rõ ràng, thoát mã 1 và **không đụng** file hiện tại. Bản đang commit sinh từ backend thật (M3). Endpoint M4 chưa có (decisions, alerts, live) dùng kiểu viết tay ở `src/api/pending.ts`.

Code trong app chỉ import type qua `src/api/types.ts`. Sau khi sinh từ backend thật, chạy `npm run typecheck`; nếu tên schema Pydantic khác bản tạm, chỉ cần sửa alias trong `types.ts`.

## Script

| Lệnh | Việc |
|---|---|
| `npm run dev` | Dev server có HMR |
| `npm run build` | Typecheck rồi build ra `dist/` |
| `npm run preview` | Xem bản build |
| `npm test` | Vitest + Testing Library (jsdom), chạy một lần |
| `npm run test:watch` | Vitest chế độ theo dõi |
| `npm run lint` | ESLint, không cho phép cảnh báo |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run gen:api` | Sinh `src/api/schema.d.ts` |

Trước khi push: `npm run check` (deployment regression tests, eslint, vitest, typechecked Cloudflare build, deployment dry run). Full CI chỉ chạy khi gọi thủ công.

## Cloudflare

Website: <https://megadaytwin.dangphuc.me>. This repository deploys Worker
`megadaytwin` with D1 `megadaytwin-db` through binding `DB`.

```bash
cd web
npm ci
npm run prepare:sources
npm run lint && npm run typecheck && npm test
npm run build:cloudflare
npm run deploy:cloudflare -- --dry-run
npm run deploy:cloudflare
npx playwright install chromium
npm run test:smoke -- https://megadaytwin.dangphuc.me
```

The React/Vite city, Data Studio, account storage and Milo chat run in this
Worker. Chat history, settings, sandbox and sanitized source files persist in
its SQLite-backed `AccountWorkspace` Durable Object. Devices reconcile saved
changes through authenticated WebSockets. See
[account workspace](../docs/city-demo/account-workspace.md) for contracts and
verification. Sessions use the `megaday_session` cookie; scrypt and PBKDF2
password hashes are supported. The fresh deployment requires a new login.

Required Worker secrets are `ADMIN_PASSWORD_HASH`, `JUDGES_PASSWORD_HASH`,
`SESSION_SECRET` and `OPENAI_API_KEY`. The `admin` and `judges` logins share the
demo workspace. These secrets are configured on the Worker and survive ordinary
deployments. Do not copy empty local values over production secrets. No secret
is required to build or validate a dry run. FastAPI/DuckDB remains a separate
service; absent `DATA_BACKEND_URL`, existing operations use clearly labeled
fixtures and Milo does not execute campaigns or stock mutations.

Run `node scripts/check.mjs` from the repository root before merging. Select
`web`, `backend`, or `mock-data` for focused feedback; omit targets for all checks.
The web checks prepare sources once and typecheck once as part of the Cloudflare
build. Fixture validation generates into a temporary directory and compares the
file list and contents without modifying the checkout.

Full GitHub CI is manual (`ci.yml`, **Run workflow**) and runs the same checks.
It does not deploy. Remove the old required `ci` status from the `main` branch
protection when adopting this workflow; a manual workflow cannot satisfy an
automatic merge gate. Keep the other branch protection settings.

The separate `deploy.yml` workflow checks every push to `main` for web, bundled-data,
or deployment-script changes since `cloudflare-production`; it can also be run
manually on `main`. Comparing against production catches pending web changes even
if a docs-only merge overtakes their queued deployment.
It runs dependency-free deployment regression tests, promotes the exact current
commit to `cloudflare-production`, and lets Cloudflare Workers Builds install,
build and deploy `web/`. GitHub no longer repeats the dependency install, full
test suites, and Cloudflare build before deployment. Backend-only and docs-only
pushes do not deploy unless relevant changes are still pending. GitHub holds no Cloudflare deployment credentials.
Cloudflare still uses `npm run build:cloudflare` and `npm run deploy:cloudflare`.

The production job waits for the matching successful Cloudflare build and verifies
live D1 health, login and private-route access. Failed, cancelled or missing builds
fail the job; promotion alone is not deployment success. Production updates are
serialized, and an older queued/manual run cannot promote over newer `main`.
Manual dispatch rechecks the matching build and live health; it does not force a
fresh Cloudflare build when that commit has already been promoted. Retry a failed
build in Cloudflare Workers Builds before rechecking.
Do not push to `cloudflare-production` directly. Runtime secrets and the
D1/AccountWorkspace bindings remain on the Worker.

Rollback: redeploy a known-good `megadaytwin` version and keep the
`megadaytwin.dangphuc.me` custom domain attached to it. The old Workers,
custom domain and D1 were retired during the migration.

## Biến môi trường

Xem `.env.example`. Chỉ biến có tiền tố `VITE_` vào bundle, nên **không bao giờ** đặt bí mật vào đây.

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `VITE_API_BASE_URL` | `/api/v1` | Gốc API |
| `VITE_PROXY_TARGET` | `http://localhost:8000` | Backend cho proxy dev |
| `VITE_USE_FIXTURES` | `false` | `true` = đọc fixture JSON |
| `VITE_DEV_USER`, `VITE_DEV_USER_NAME`, `VITE_DEV_ROLE` | `u-ops-1`, `Lan`, `operator` | Gửi qua header `X-User`/`X-Role` (08 §1) |
| `OPENAPI_URL` | trống | URL cho `gen:api` |

## ZIP import and warehouse demand labels

Run `npm run dev:cloudflare` for Data Studio and account storage; ordinary Vite
proxies to the separate Python backend. Set Worker values in ignored `.dev.vars`
for local development. The API key is a Cloudflare secret; model IDs are text
bindings in `cloudflare.config.ts` (never use `VITE_*` for credentials):

- `OPENAI_API_KEY`: existing OpenAI credential.
- `OPENAI_MODEL_IMPORT`: defaults to `gpt-6-luna`; requires Responses structured output.
  Falls back to `OPENAI_MODEL` if supplied. Recognized tables work without AI.
- `OPENAI_MODEL_DECISION`: defaults to `gpt-6-luna`, configured independently. No automatic chat-model fallback for product classification.

New accounts start empty. A one-time migration removes untouched repository demo tables
from older accounts by exact filename/hash and demo provenance; uploaded/edited
tables are preserved. **Delete table** and **Delete all data** remove saved tables
and their SQLite records after confirmation. Removing inventory also removes its label run.

Drop a ZIP anywhere in Data Studio (or use **Import ZIP**) to extract UTF-8
CSV/JSON and immediately save recognized and general tables. If several files
match the same business table, choose one before saving. Import does not wait
for AI. **Review & fix with AI** reviews the current saved tables and applies
validated column mappings with revision checks; failed review leaves them unchanged.
Repair also standardizes recognized headers, trims surrounding whitespace and
normalizes readable, nonnegative VND amounts using the existing money contract.
It never invents missing values, resolves ambiguous SKU identities, drops rows or
assumes timezones. The result shows applied repairs and remaining diagnostics.
An unchanged review does not rewrite sources or clear demand labels.
Cell edits retain revision-checked autosave. SQLite
`studio_tables` stores the schema/catalog and `studio_rows` stores records, atomically
with the source document revision. Unchanged table rows are not rewritten; no
user or model SQL is executed. The
map uses saved inventory. HQ shows imported table, order, ad and stocked-product
counts; Neighborhood shows searchable, paginated imported orders. The demo clock
only filters repository demo data, not uploaded inventory or orders.

The authenticated gateway streams import and labeling requests to the existing
AccountWorkspace Durable Object. Extraction, parsing, model calls and persistence
run there, avoiding the gateway CPU limit that caused HTTP 503 responses for
the 11.11 archive. The object receives the same `OPENAI_API_KEY` binding as Milo;
no second key or public internal route is introduced.
Initialized workspaces can read existing documents without schema writes when
Cloudflare's free-tier row-write quota is exhausted. Saves report
`WORKSPACE_WRITE_QUOTA` and retain the last committed data until writes are allowed
again. Table saves update only changed records and remove only deleted records,
reducing row writes during repeated labeling and edits.

`POST /api/studio/import` accepts raw ZIP bytes and returns `{ tables, skipped,
model }`. It does not save anything. Limits: 20 MiB upload and extracted data,
100 archive entries, 24 CSV/JSON files. Paths with traversal are rejected;
answer-key/hidden files, `_replay.json` playback metadata and unsupported types
are excluded. Known contracts are parsed deterministically. With `?review=1`,
one model call reviews every table using its schema,
diagnostic codes and at most two sample rows (12 columns, 80 characters per value,
contact fields removed). The same call maps unknown flat CSV/JSON headers.
Reviewed tables replace the saved set, and review findings appear in Import details. Missing model configuration is explicit.
A sample review does not certify all records. Every mapping must use existing columns and satisfy a
supported table contract; row values are never model-generated. Unmapped tables remain general tables. JSON objects and arrays of records are
supported (nested values remain JSON); spreadsheets are not supported. Existing direct CSV/JSON and folder import remain available.

Map → **Warehouse** → **Open workspace** → **Label data** classifies saved
products with positive latest stock only. The toolbar contains search by name/code
and **Label data**. New runs default to Vietnam and today; retries retain their
saved assessment context. The API continues to accept dated product evidence. Each model batch gets
only evidence for its products. No web search is performed; metadata and manual
evidence do not imply measured demand. New model runs always choose the best-fit `Trendy`, `Seasonal` or `Normal` label,
with no confidence cutoff. When evidence is weak, the model may infer likely use
from product attributes and must identify the result as an estimate. It never
claims invented sales history or trend evidence. Older saved `null` results remain
readable; prompt version `demand-v3` marks them stale for reclassification. The Vietnamese prompt lives
in `server/demand-prompt.ts`; these labels do not change forecast groups or ads.

- `GET /api/product-labels`: inventory candidates, configuration, latest run, stale flag.
- `POST /api/product-labels`: `{ sourceRevision, context: { market, asOf,
  evidence: [{ productCode, observedAt, source, observation }] } }`.

Both routes require authentication and same-origin writes. Classification runs
only on button click, uses batches of 50 (up to three concurrent calls), with
a 30-second timeout per batch and a 120-second overall timeout. At most 2,000 candidates and 100 evidence records are
accepted. Long jobs can time out; there is no background queue. Results retain
assessment context, model/prompt version and source fingerprint in account
storage. Invalid/duplicate/unknown results are rejected. Concurrent calls are
leased, changed sources reject late results, and completed/partial identical requests
without batch failures are reused without another model call or source write.
Successful batches survive individual batch failures; retry requests only missing
products when source, provider and context still match. Safe errors distinguish
model access, unsupported requests, rate limits and service failures. Changed assessment context can start a new run. Legacy five-group
results are stale and must be regenerated. Failures never erase source data.

Successful/partial runs atomically add a read-only `Demand label` column to the
inventory CSV and SQLite records. Blank values are unclassified; missing-evidence
reasons remain in the run. The column appears in Data Studio, Warehouse and CSV
exports. Editing/reimporting sources clears derived labels; browser uploads cannot
forge them. The existing Python bridge forwards only its six supported business
formats, without the derived label column; general tables stay in the workspace.

Checks: `npm run check`; run `node qa/studio/serve.mjs` to serve the built
Worker locally on port 5187, then `node qa/studio/labels.mjs` for the browser
flow. The local QA login is `admin` / `studio-local-test` (or set
`STUDIO_QA_PASSWORD` for both scripts). Credentials and data stay in ignored
`.cloudflare/studio-qa/`; screenshots and results go to `qa/runs/studio-import/`.
The script resets and writes synthetic tables only on localhost. Provider
contract tests stub the OpenAI response; they do not prove a live model call.
For an actual archive, set `LIVE_ARCHIVE_PATH` and run
`npx vitest run server/live-archive.test.ts`, then
`node qa/studio/live-zip.mjs`. The latter verifies ZIP drop, saved records, HQ,
Neighborhood, Warehouse and responsive layouts against the local Worker.
Archive contents stay local and are never committed.

## Lập kế hoạch sale: mô phỏng thử và áp dụng

Mở **Lập kế hoạch sale** (`/sale-planner`) từ sidebar hoặc MegaDay HQ, chọn cả
chiến dịch hoặc một SKU. Cả hai phạm vi có thể trao đổi với Milo rồi kiểm tra
bản nháp. Giao diện hiển thị mục tiêu đang áp dụng và phiên bản của nó.
**Mô phỏng thử** chỉ lưu bản nháp không hoạt động và kết quả what-if; không thay
mục tiêu hoặc đề xuất hiện tại. Sau khi xem kết quả và bảng thay đổi, người dùng
xác nhận riêng **Áp dụng mục tiêu**. Bản đầu hỗ trợ một chiến dịch, một SKU trọng
tâm mỗi lần và 1–72 giờ tròn. Đây không phải lệnh thực thi quảng cáo.

SKU kế thừa giới hạn ngân sách/lỗ chặt hơn từ mục tiêu chiến dịch. Thay đổi ưu
tiên không tương thích được nêu rõ và chặn áp dụng; cần sửa kế hoạch chung trước.
Chưa cộng gộp hay tối ưu đồng thời nhiều SKU. Sửa bản nháp/phạm vi hủy kết quả
thử và xác nhận cũ. Nếu mục tiêu hoặc dữ liệu/mô hình thay đổi sau lúc tính,
backend trả 409, yêu cầu đối chiếu và mô phỏng lại. Lỗi mạng lúc áp dụng giữ mã
bản thử để thử lại an toàn; không kích hoạt hai lần. Kiểm tra dữ liệu hiện dùng
dấu vết toàn warehouse, nên thay đổi ngoài SKU đang xét cũng có thể yêu cầu tính lại.

Gateway dùng `DATA_BACKEND_URL` (gốc API, ví dụ `https://backend.example/api/v1/`),
`DATA_BACKEND_USER`, `DATA_BACKEND_ROLE` như kết nối Data Studio. Operator có thể
mô phỏng; lead mới được áp dụng. Backend cần migration 6, capability `preview_apply`
trong `/goal-planner/capabilities` phiên bản 1 và dữ liệu đã ingest. Chỉ dùng
backend trên mạng tin cậy theo mô hình xác thực hiện có. Frontend gọi trực tiếp
gateway cùng origin; không dùng fixture để thay kết quả thật. Backend chưa nối,
chưa nâng cấp hoặc không đủ quyền sẽ có thông báo và không cho chạy.

Diễn giải dùng cấu hình `OPENAI_API_KEY` và bộ chọn model sẵn có của Ask your PM.
Khi chưa cấu hình/lỗi, chuyển sang quy tắc có thông báo hoặc nhập thủ công.
AI chỉ trích thông số mục tiêu; các con số dự báo và đề xuất do simulator tính.
Yêu cầu chưa hỗ trợ phải được người dùng kiểm tra và đồng ý loại khỏi lần tính.
Mức lỗ là giới hạn **kỳ vọng J**, không phải bảo đảm không lỗ; J có giá trị tồn
cuối kỳ nên không đồng nhất với lợi nhuận kế toán.

Kiểm chứng: `npm.cmd run check`; chạy Vite cổng 5186 rồi
`node qa/studio/sale-planner.mjs` (Windows có thể đặt `STUDIO_QA_CHANNEL=msedge`).
Browser QA dùng API giả lập; ảnh/kết quả ở `qa/runs/sale-planner/`.
Ảnh review (số minh họa từ API giả lập): [desktop](../docs/images/sale-planner-desktop.png),
[mobile](../docs/images/sale-planner-mobile.png),
[đối chiếu trước khi áp dụng](../docs/images/sale-plan-apply-desktop.png),
[đối chiếu trên mobile](../docs/images/sale-plan-apply-mobile.png).

## Trợ lý mục tiêu theo SKU và minh bạch con số

Từ HQ chọn **Lập kế hoạch sale → Một sản phẩm**. Liên kết cũ
`/sku-assistant?sku=...` chuyển đến luồng chung và giữ SKU đã chọn.
Danh sách và ngữ cảnh được đọc từ backend thật. SKU được chuyển vào **Milo**,
dùng chung hội thoại và lịch sử lưu trong tài khoản. Milo nhận dữ liệu SKU,
lịch sử bán gần đây, nguồn/mốc dữ liệu và chiến dịch liên quan. Mục tiêu được
diễn giải từ nhiều lượt chat, bao gồm việc sửa yêu cầu; giá trị đã biết không
cần hỏi lại. **Kiểm tra mục tiêu** mở form chỉnh và xác nhận có sẵn.
Đổi SKU tách phạm vi hội thoại và bỏ phản hồi muộn của sản phẩm trước.
Bản nháp hiển thị trong phiên không phải mục tiêu đã lưu hoặc lệnh thực thi.

Nếu một SKU thuộc nhiều chiến dịch, người dùng chọn chiến dịch. Mục tiêu bán
đủ/xả hàng xét lượng bán trực tiếp của SKU đó; J, ngân sách và giới hạn lỗ vẫn
tính cho **toàn chiến dịch**. Chưa có phân bổ ngân sách quảng cáo hoặc lợi nhuận
riêng SKU. Không tự tạo chiến dịch hoặc thực thi thay đổi quảng cáo.

Kết quả mới có bản truy vết được lưu cùng mô phỏng: bấm chỉ số để mở phân vị,
mở **Cách tính và đối chiếu con số** để xem số thay vào công thức J; mở **Dữ liệu
và công thức đã dùng trong lần tính** để xem file/batch, dự báo, tham số, giá
vốn/phí và giả định từng SKU. Snapshot cũ không bị thay bằng dữ liệu mới. Kết
quả trước khi có trace hiển thị thiếu bằng chứng; không dựng lời giải thích giả.
Nguồn chưa truy vết được tới từng dòng CSV được nêu rõ trong giới hạn.

Cần backend có migration 6 và capability `focus_sku`, `calculation_trace`, `preview_apply`, cùng
cấu hình kết nối/lead của planner. Chạy migration theo quy trình backend trước
khi dùng; gateway từ chối chạy theo SKU trên backend cũ. AI diễn giải vẫn dùng
cấu hình Milo (`OPENAI_API_KEY`, model đã chọn trong tài khoản) và có fallback
quy tắc khi bước diễn giải gặp lỗi. Chưa cấu hình AI thì hội thoại báo rõ,
không trả lời giả làm AI. Bản nháp vẫn phải kiểm tra trước khi xác nhận.
Kiểm thử model dùng phản hồi giả lập, không đánh giá chất lượng AI trực tiếp.

Browser QA: Vite cổng 5186, `node qa/studio/sku-assistant.mjs` (có thể đặt
`STUDIO_QA_CHANNEL=msedge`). Script dùng API giả lập, kiểm tra xác nhận, phạm vi
SKU/chiến dịch, phản hồi muộn sau đổi SKU, số liệu/bằng chứng và desktop/mobile.
Ảnh/kết quả nằm ở `qa/runs/sku-assistant/`.
Ảnh review: [Milo desktop](../docs/images/milo-conversation-desktop.png),
[Milo mobile](../docs/images/milo-conversation-mobile.png),
[đối chiếu số trên mobile](../docs/images/sku-evidence-mobile.png) dùng API giả lập;
[phân rã giá vốn/phí](../docs/images/sku-evidence-real.png) dùng kết quả Python
thật trên DB thử `nho` riêng, phát lại qua HTTP giả lập trong browser.

## Cấu trúc

```
src/
├─ main.tsx, routes.tsx, index.css   # điểm vào, bảng route (09 §1), token theme sáng/tối
├─ api/      client.ts (fetch + ApiError + fixture), queries.ts (hook TanStack Query),
│            types.ts (alias type), schema.d.ts (sinh), fixtures/*.json
├─ components/  AppShell (TopBar, SideNav, panel Trợ lý), badges, states (PageHeader, EmptyState, ErrorState, Skeleton, Banner, UpdatingRegion),
│               CitationChip, numbers (MoneyVND, Range), ZoneBar (SVG)
├─ pages/    ShiftOverviewPage, compare/ (So sánh 3 tầng, form quyết định), placeholders cho 6 màn còn lại
└─ lib/      i18n.ts, format.ts (tiền VND, giờ Asia/Ho_Chi_Minh, số lượng), breakeven.ts (hòa vốn 05 §7, cùng công thức backend),
            zoneBarText.ts, storage.ts, theme.ts, queryKeys.ts, config.ts
```

Theme: token CSS trên `:root` và `[data-theme='dark']`, map vào Tailwind bằng `@theme inline` (dùng `bg-surface`, `text-muted`, `border-border`...). Nút trên thanh trên cùng đổi Theo hệ thống / Sáng / Tối, lưu ở `localStorage` (bọc try/catch). `Ctrl+K` mở panel Trợ lý, `Esc` đóng.
