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

Trước khi push: `npm run lint && npm run typecheck && npm test && npm run build`. CI (`.github/workflows/ci.yml`) chạy chuỗi này và kiểm bản build Cloudflare khi PR đụng `web/`.

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

GitHub Actions runs the backend, web and mock-data checks and exposes the required
`ci` status for `main`. After that gate passes on a push to `main`, a promotion job
copies the exact commit to the automation-only `cloudflare-production` branch.
Cloudflare Workers Builds watches that branch and automatically pulls, builds and
deploys `web/`. GitHub Actions does not hold Cloudflare deployment credentials.
The Cloudflare Build configuration uses `npm run build:cloudflare` followed by
`npm run deploy:cloudflare`. PRs and pushes check only changed components;
frontend CI runs one typechecked Cloudflare build. Web/config or bundled-data
changes trigger promotion; docs-only and backend-only changes do not redeploy
the frontend. A manual workflow run checks every component.

Teammates: open a PR to `main`, wait for the `ci` check, then merge. Do not merge
or push to `cloudflare-production` directly. Watch GitHub Actions for CI and the
promotion job; watch Worker `megadaytwin` → Builds in Cloudflare for deployment.
Runtime secrets and the D1/AccountWorkspace bindings remain on the Worker.

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
