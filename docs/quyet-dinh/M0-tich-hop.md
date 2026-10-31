# M0 · Quyết định khi ghép backend và web

Hai phần của M0 được làm song song rồi ghép trên nhánh `feat/M0-khung`. File này ghi các chỗ phải chốt lúc ghép. Quyết định riêng của từng phần nằm ở [INF-backend.md](INF-backend.md) và [INF-05-web.md](INF-05-web.md).

## 1. `GET /api/v1/health` dùng khung `{data, meta}`

- **Vấn đề:** backend trả `/health` bọc trong `{data, meta}` theo 08 §1. Web đọc theo 01 (JSON trần `{status, db, ai}`), nên khi chạy thật web sẽ đọc sai.
- **Lựa chọn:** theo backend. Mọi endpoint đều dùng một khung, client không phải nhớ ngoại lệ.
- **Đã sửa:** `web/src/api/schema.d.ts` (`HealthStatus` thêm `ai_mode`, `app_env`, `version`, `message`; bỏ `ai: 'error'` vì backend không trả giá trị này), `queries.ts` (`useHealth` đọc `Envelope<HealthStatus>`), fixture `health.json`, `client.test.ts`.
- **Lưu ý:** khi DB lỗi, backend trả HTTP 503 kèm thân `{data, meta}` (không phải thân lỗi). Client hiện ném `ApiError(503, 'HTTP_ERROR')`. Khi làm WEB-03 (huy hiệu trạng thái) thì đọc thân 503 để hiện "đang suy giảm" thay vì lỗi chung.

## 2. Một workflow CI với job cổng `ci`

- **Vấn đề:** hai workflow `backend.yml` và `web.yml` có lọc theo đường dẫn. Khi bật luật "check bắt buộc", PR chỉ sửa một phần sẽ có check của phần kia không bao giờ chạy, và GitHub giữ PR ở trạng thái chờ mãi.
- **Lựa chọn:** gộp thành `.github/workflows/ci.yml`. Job `changes` (dorny/paths-filter) quyết định chạy phần nào. Job `ci` luôn chạy và chỉ đỏ khi có job lỗi hoặc bị hủy; job bị bỏ qua tính là qua. Chỉ bắt buộc `ci`.
- **Thêm job `mock-data`:** sinh lại cỡ `nho` và `demo`, so với bản đã commit, bỏ qua `manifest.json` (có dòng thời điểm tạo). Đã thử trên máy: khớp y hệt.
- **Backend chạy cả khi `mock-data/` đổi**, vì test và chấm điểm của backend đọc dữ liệu đó.

## 3. Đã chạy thật trên máy

- Backend: `ruff`, `ruff format --check`, `mypy app`, `pytest` (100 test) xanh.
- Web: `lint`, `typecheck`, `test` (24 test), `build` xanh.
- Chạy đồng thời backend (cổng 8000) và web (cổng 5173), gọi `/api/v1/health` qua proxy của Vite: nhận đúng khung `{data, meta}`. Cổng, proxy và CORS khớp nhau.

## 4. Việc chuyển sang mốc sau

- `npm run gen:api` chưa chạy để thay schema viết tay, vì backend mới có `/health`. Chạy sau API-01, khi backend có đủ schema.
- Mã lỗi dữ liệu: 02 dùng `ADS_TZ_SHIFT`…, 11 dùng `ads_timezone_shift`…. ING-07 dùng mã của 02 (theo đề xuất của backend), và sửa 11 trong cùng PR M1.
