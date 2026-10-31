# Quyết định · INF-05 + WEB-01 (khung `web/`)

Ghi các quyết định tự đưa ra khi dựng khung frontend ở mốc M0. Mỗi mục: vấn đề, lựa chọn, lý do, ảnh hưởng.

## 1. Phiên bản thư viện

- **Vấn đề:** Bản mới nhất trên npm lúc làm (10/2026) là React 19, TypeScript 7, Vite 8, ESLint 10, jsdom 30. Plan ghi React 18.
- **Lựa chọn:** React 18.3.1 theo plan. TypeScript 5.9.3 (typescript-eslint 8 chỉ hỗ trợ `<6.1`, openapi-typescript 7 cần `^5`). Vite 7.3.7, Vitest 4.1.12, Tailwind 4.3.3 (`@tailwindcss/vite`), TanStack Query 5.105.0, React Router 7.18.5 (gói `react-router`, chế độ data router). ESLint 10.13.0 (bản 9 đã hết hỗ trợ). `@testing-library/jest-dom` 6.9.1 (6.10.0 bị npm đánh dấu deprecated). Mọi phiên bản ghim chính xác, không dùng `^`.
- **Lý do:** tránh tổ hợp chưa ổn định (TS 7 + typescript-eslint), giữ đúng React 18 của plan.
- **Ảnh hưởng:** nâng cấp sau làm có chủ đích, từng gói một. `engines.node >= 22`; CI dùng Node 24.

## 2. Đường dẫn OpenAPI cho `gen:api`

- **Vấn đề:** 08-api ghi FastAPI phơi `/api/v1/openapi.json`; yêu cầu task ghi `http://localhost:8000/openapi.json`. Backend chưa có nên chưa biết chốt cái nào.
- **Lựa chọn:** script thử lần lượt `/api/v1/openapi.json` rồi `/openapi.json`; `OPENAPI_URL` (hoặc tham số dòng lệnh) ghi đè.
- **Lý do:** chạy được với cả hai cấu hình FastAPI mặc định và theo plan, không cần sửa khi backend chốt.
- **Ảnh hưởng:** không có. Khi D3 chốt, có thể bỏ ứng viên thừa.

## 3. `gen:api` khi backend chưa chạy

- **Vấn đề:** không được làm hỏng build khi chưa có backend.
- **Lựa chọn:** commit `src/api/schema.d.ts` viết tay theo 08 (cùng hình dạng `components['schemas']` mà openapi-typescript sinh). Script lỗi kết nối thì in hướng dẫn tiếng Việt, thoát mã 1, không đụng file. Khi lấy được spec thì ghi ra file tạm rồi đổi tên (không để file dở dang). Code app chỉ import type qua `src/api/types.ts` (alias).
- **Lý do:** typecheck/CI chạy được ngay; khi backend có OpenAPI thật, đổi tên schema chỉ phải sửa ở một chỗ.
- **Ảnh hưởng:** tên schema trong bản tạm (`SkuListItem`, `Recommendation`, `ScenarioRun`, `Freshness`...) là đoán theo 05/08. Lần chạy `gen:api` đầu tiên với backend thật nhiều khả năng phải sửa alias trong `types.ts`. Đề nghị D3 đặt tên Pydantic model khớp 05 (`Recommendation`, `ZoneBar`, `ScenarioResult`, `FlipCondition`).

## 4. `GET /health` không có khung `data/meta`

- **Vấn đề:** 08 §1 nói "phản hồi có số" dùng khung `{data, meta}`; 01 §Tiêu chí ghi `/health` trả `{"status":"ok","db":"ok","ai":"ok|disabled"}` trần.
- **Lựa chọn:** client không ép khung: mỗi hook khai báo kiểu riêng (`HealthStatus` trần; `Envelope<T>` / `Page<T>` cho các endpoint còn lại). Fixture `/health` theo 01.
- **Ảnh hưởng:** không có. Nếu backend bọc `/health` vào `data`, sửa một kiểu.
- **Cập nhật khi ghép M0:** backend bọc `/health` trong `{data, meta}`, nên web đã đổi theo. Xem [M0-tich-hop.md](M0-tich-hop.md) mục 1.

## 5. Fixture: phần bổ sung ngoài ví dụ 08

- **Vấn đề:** 08 chỉ có 1 dòng SKU (NOICHIEN2L) và 3 kết quả mô phỏng; màn Tổng quan và So sánh cần nhiều hơn để nhìn ra bố cục.
- **Lựa chọn:**
  - `skus.json`: dòng NOICHIEN2L giữ nguyên ví dụ 08 §3.4; thêm AOGIUNHIET-M, BINHNUOC500-HONG, DENLEDTET-10M, GIAYLOT50 theo câu chuyện 6 nhân vật (00 §7, 09 §2.1). Số của các dòng thêm là minh họa.
  - `scenario-scn-2291.json`: 3 kết quả của 08 §3.8 + `reduce_budget 30%` và `pause_until_restock` lấy từ wireframe 09 §2.3; `pause_until_restock` cũng vi phạm `campaign_must_run`. Bỏ các trường `"..."` của ví dụ, điền mốc thời gian 01:00/01:02 theo wireframe.
  - `recommendation-rec-551.json`: đúng 08 §3.8, thêm `scenario_id`, `created_at`, `superseded_by` theo 08 §3.9.
  - `alerts.json`: al-31 của 08 §3.11 + 2 cảnh báo minh họa (bình nước, nồi chiên).
- **Ảnh hưởng:** fixture là dữ liệu mẫu, không phải đầu ra cam kết. UI hiện nhãn "Dữ liệu mẫu" khi bật fixture.

## 6. Route cho mục menu cần tham số

- **Vấn đề:** 09 §1 có `/sku/:sku`, `/sku/:sku/compare`, `/campaign/:id/compare` và Trợ lý là panel, nhưng menu cần 8 mục bấm được.
- **Lựa chọn:** thêm `/sku`, `/compare` (trang chọn đối tượng, hiện là placeholder) và `/assistant` (trang riêng, 09 §6 cũng cần cho màn hẹp). Panel Trợ lý bên phải mở bằng nút trên thanh trên cùng hoặc `Ctrl+K`, `Esc` đóng.
- **Ảnh hưởng:** không đổi hợp đồng; WEB-04/05 có thể đổi `/sku`, `/compare` thành ô tìm SKU.

## 7. Theme sáng/tối

- **Lựa chọn:** token CSS trên `:root` / `[data-theme='dark']`, map vào Tailwind bằng `@theme inline`; biến thể `dark:` theo `data-theme`. Mặc định theo hệ thống, người dùng đổi được (Theo hệ thống / Sáng / Tối), lưu `localStorage` bọc try/catch. Script nhỏ trong `index.html` đặt theme trước khi vẽ để không nháy.
- **Lý do:** 09 §6 yêu cầu dark mode bằng biến CSS; token giúp WEB-02 dùng lại.
- **Ảnh hưởng:** mọi component dùng tên token (`bg-surface`, `text-muted`, `border-border`, `text-ok`/`warn`/`danger`), không dùng màu Tailwind trực tiếp.

## 8. Badge tối thiểu trong WEB-01

- **Vấn đề:** WEB-02 (thành phần dùng chung) nằm ngoài phạm vi chính, nhưng màn Tổng quan phải hiện nhãn tin cậy và độ mới cho trung thực (00 §3 nguyên tắc 2).
- **Lựa chọn:** làm bản tối thiểu `ConfidenceBadge` (chữ + chấm đặc/nửa/rỗng), `ComputeStateTag`, `FreshnessBadge` (tooltip bằng `title`). Chưa làm CitationChip, Range, popover, ZoneBar.
- **Ảnh hưởng:** WEB-02 mở rộng các component này thay vì viết lại.

## 9. Tên biến chuỗi tiếng Việt

- **Vấn đề:** đặt tên export `vi` trong `i18n.ts` trùng với global `vi` của Vitest.
- **Lựa chọn:** export `viText`.

## 10. Quy mô PR

- **Vấn đề:** quy ước PR ≤ 400 dòng đổi (không tính dữ liệu và file sinh). Khung gồm cấu hình, client, i18n, format, shell, 8 màn placeholder và test nên vượt mức này.
- **Lựa chọn:** giữ một PR vì đây là khung khởi tạo (INF-05 + WEB-01 phụ thuộc chặt); không làm WEB-02 để PR không phình. `package-lock.json`, fixture JSON và `schema.d.ts` (sẽ bị sinh lại) không tính vào ngưỡng.
- **Ảnh hưởng:** reviewer nên đọc theo thứ tự: `src/api/client.ts`, `src/lib/format.ts`, `src/components/AppShell.tsx`, `src/pages/ShiftOverviewPage.tsx`.

## 11. Script cài đặt của esbuild

- **Vấn đề:** npm 11 cảnh báo `allow-scripts` cho postinstall của esbuild.
- **Lựa chọn:** không bật. esbuild lấy binary qua optional dependency theo nền tảng, build và test chạy bình thường không cần postinstall.
- **Ảnh hưởng:** nếu một máy báo thiếu binary esbuild, chạy `npm approve-scripts esbuild` rồi `npm ci`.
