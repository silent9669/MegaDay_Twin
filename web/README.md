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

## Nối tool gán nhóm nhu cầu cho Data Studio

Khung đã có, adapter mặc định **chưa cấu hình**. Điểm cần implement là
`getProductLabelProvider(env)` trong `server/product-label-provider.ts`:
trả về `{ id, classify(input, signal) }` khi có cấu hình thật, ngược lại trả `null`.
`classify` nhận `LabelInput` từ `src/studio/product-labels.ts` và trả về:

```json
{
  "labels": [
    { "productId": "<echo exact input.products[i].id>", "group": "mua_vu", "reason": "Evidence supplied by the provider", "confidence": 0.82 }
  ]
}
```

`group` chỉ nhận `moi`, `ban_thua`, `mua_vu`, `trend`, `on_dinh`, hoặc `null`
khi chưa kết luận được. `reason` bắt buộc; `confidence` tùy chọn, trong [0,1].
Có thể trả một phần danh sách; sản phẩm còn thiếu giữ trạng thái chưa phân loại.
Mã lạ, mã trùng, nhãn sai, lý do rỗng hay confidence ngoài khoảng bị từ chối.
Đặt timeout trong adapter, tôn trọng `AbortSignal`, giữ key ở Worker secrets và
không đưa tên/ghi chú sản phẩm thành chỉ dẫn cho mô hình. Không thêm key vào UI.
Khung hiện không giả định URL, hãng cung cấp, payload HTTP hoặc API key nào.

Hai endpoint đã nằm sau session và kiểm tra same-origin:

- `GET /api/product-labels`: candidates của nguồn đã lưu, trạng thái cấu hình,
  lần chạy gần nhất và cờ stale.
- `POST /api/product-labels` với `{ "sourceRevision": 7 }`: chạy adapter cho đúng
  phiên bản đã lưu. Không nhận nhãn hay file từ client trong request này.

Nguồn được lưu trước; import thành công mới kích hoạt một lần gán nhãn khi
adapter đã cấu hình. Mở demo/reload không tự gọi tool. Có nút chạy/thử lại;
tool lỗi không làm thất bại việc lưu file. Kết quả lưu riêng trong Durable Object,
không cho browser ghi trực tiếp. Claim và finish kiểm tra revision/fingerprint,
chống chạy trùng và kết quả trả chậm. Giới hạn hiện tại 2.000 candidates,
30 giây mỗi lần chạy, lease 35 giây; không có hàng đợi background bền vững.

Candidates được rút từ tên sản phẩm trong đơn hàng, tồn kho và giá vốn. Ads,
cấu trúc chiến dịch, chương trình sale không cung cấp tên đủ tin cậy để tự tạo
thêm sản phẩm; cả sáu nguồn vẫn nằm trong fingerprint. ID giữ riêng platform
SKU, mã nội bộ và tên nhập tay; **chưa đối soát SKU xuyên nguồn**. Adapter chỉ
nhận tên, mã, tên khác và số bản ghi, không nhận file gốc, contact hoặc ghi chú.
Để kết luận nhóm nhu cầu theo lịch sử, bên tích hợp phải bổ sung hợp đồng dữ liệu
và bằng chứng phù hợp: tên sản phẩm đơn lẻ không chứng minh được trend/bán thưa.

Nhãn tool hiện là kết quả riêng trong Data Studio, **chưa ghi vào Python forecast**.
Theo lựa chọn người dùng, classifier hiện tại tiếp tục hoạt động. Công việc còn
lại của bên tích hợp: adapter thật, secrets/config, đối soát canonical SKU và
cơ chế áp dụng nhãn vào backend có quyền sửa tay/audit/backtest tương ứng.

Kiểm tra với `npm run check`. Browser QA: chạy Vite cổng 5186 rồi
`node qa/studio/labels.mjs` (có thể đặt `STUDIO_QA_CHANNEL=msedge`). Bài browser
dùng API giả lập; test server dùng SQLite thật với provider giả, không gọi tool
hay ghi production. Ảnh và kết quả nằm trong `qa/runs/product-labels/` ở gốc repo.

## Mục tiêu buổi sale (Task 1)

Mở `/sale-planner` từ sidebar hoặc MegaDay HQ. Nhập mong muốn, phân tích,
chỉnh thông số và chọn chiến dịch, sau đó xác nhận để lưu mục tiêu đang áp dụng
và chạy mô phỏng. Bản đầu hỗ trợ **một chiến dịch, 1–72 giờ tròn**; bốn mục tiêu
là lợi nhuận, số lượng bán, xả hàng và hiệu quả chi. Ngân sách là tổng cho cả kỳ.
Thao tác này không thực thi thay đổi quảng cáo. Sửa thông số hủy xác nhận và
ẩn kết quả cũ. Nếu đã lưu mục tiêu nhưng mô phỏng lỗi, nút thử lại dùng mục tiêu đó.

Gateway dùng `DATA_BACKEND_URL` (gốc API, ví dụ `https://backend.example/api/v1/`),
`DATA_BACKEND_USER`, `DATA_BACKEND_ROLE=lead` như kết nối Data Studio. Backend
phải có `/goal-planner/capabilities` phiên bản 1 và dữ liệu đã ingest. Chỉ dùng
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
[mobile](../docs/images/sale-planner-mobile.png).

## Trợ lý mục tiêu theo SKU và minh bạch con số

Từ HQ chọn **Lập mục tiêu** của một SKU, hoặc mở `/sku-assistant?sku=...`.
Danh sách và ngữ cảnh được đọc từ backend thật. Hội thoại hỏi mục tiêu, hỗ trợ
câu trả lời ngắn và tạo bản nháp để chỉnh/xác nhận qua form có sẵn. Đổi SKU
hủy phản hồi đang chờ và xóa bản nháp/kết quả của SKU trước. Hội thoại chỉ giữ
trong phiên giao diện; mục tiêu đã xác nhận lưu trên backend như luồng planner.

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

Cần backend có migration 5 và capability `focus_sku`, `calculation_trace`, cùng
cấu hình kết nối/lead của planner. Chạy migration theo quy trình backend trước
khi dùng; gateway từ chối chạy theo SKU trên backend cũ. AI diễn giải vẫn dùng
cấu hình Ask your PM và có fallback quy tắc. Không có AI thật trong kiểm thử.

Browser QA: Vite cổng 5186, `node qa/studio/sku-assistant.mjs` (có thể đặt
`STUDIO_QA_CHANNEL=msedge`). Script dùng API giả lập, kiểm tra xác nhận, phạm vi
SKU/chiến dịch, phản hồi muộn sau đổi SKU, số liệu/bằng chứng và desktop/mobile.
Ảnh/kết quả nằm ở `qa/runs/sku-assistant/`.
Ảnh review: [hội thoại desktop](../docs/images/sku-assistant-desktop.png),
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
