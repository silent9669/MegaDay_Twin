# Nhật ký quyết định · Khung backend (M0)

Task: INF-01, INF-02, INF-03, INF-04, INF-06, WH-01, WH-02. Nhánh `feat/INF-01-backend-khung`.

Mỗi mục ghi: vấn đề, lựa chọn, lý do, ảnh hưởng. Các chỗ DDL lệch plan được đánh dấu `-- [DEV]` trong `backend/app/warehouse/schema.sql`.

---

## A. Công cụ và cấu trúc

### A1. Lệnh tiện ích đa nền tảng: `tasks.py` + `Makefile` mỏng
- **Vấn đề:** Plan (12 · M0) nói `make dev`, `make test`, `make db-reset`, nhưng máy dev là Windows và không có `make`.
- **Lựa chọn:** `backend/tasks.py` (Python thuần, không phụ thuộc thêm) chứa mọi lệnh dev: `install, lint, fmt, typecheck, test, check, migrate, db-reset, dev, serve`. `backend/Makefile` chỉ gọi lại `uv run python tasks.py <task>`.
- **Lý do:** Một nguồn logic duy nhất, chạy y hệt trên Windows lẫn Linux/CI. Không tách lệnh dev vào CLI ứng dụng (`app/cli.py`) để app không phụ thuộc vào ruff/mypy/pytest.
- **Ảnh hưởng:** Người dùng Windows chạy `uv run python tasks.py check`; người có make chạy `make check`. Lệnh `make dev` ở **gốc repo** (chạy cả backend + frontend) chưa làm vì đụng `web/`; khi web xong có thể thêm `Makefile` gốc gọi `backend/tasks.py dev` và `npm run dev`.

### A2. uv ở chế độ không đóng gói (`package = false`)
- **Vấn đề:** `app` không cần cài như một package; plan chạy `python -m app.cli`, `uvicorn app.main:app`.
- **Lựa chọn:** `[tool.uv] package = false`, pytest dùng `pythonpath = ["."]`. Python ghim `>=3.11,<3.12` + `.python-version = 3.11`.
- **Ảnh hưởng:** Mọi lệnh chạy từ `backend/`. `uv sync --locked` trong CI bảo đảm đúng `uv.lock`.

### A3. Phạm vi mypy strict
- **Vấn đề:** 01 §7 yêu cầu strict cho `economics`, `simulator`, `forecast/base.py`.
- **Lựa chọn:** Strict cho các package đó **và** `app.core`, `app.warehouse` (hợp đồng dùng chung). Các package khác vẫn bật `disallow_untyped_defs`. Do mypy không cho `strict = true` theo module, dùng bộ cờ tương đương trong `[[tool.mypy.overrides]]`.
- **Ảnh hưởng:** Agent viết `economics/`, `simulator/`, `forecast/base.py` phải qua strict ngay khi tạo file.

### A4. Phụ thuộc thêm ngoài danh sách 01 §5
- `typer` (CLI, có trong 01 nhưng không nằm trong danh sách phụ thuộc), `tzdata` (Windows không có cơ sở dữ liệu múi giờ), `pytz` (DuckDB cần để trả `TIMESTAMPTZ` về Python), dev: `httpx` (TestClient), `pandas-stubs`.

### A5. Ruff
- Bỏ `RUF001-003` (chuỗi tiếng Việt) và `N818` (tên exception lấy theo plan: `DataQualityError`, `ConstraintViolation`, `StaleDataError`, `StateConflict`…).

---

## B. Schema DuckDB (hợp đồng)

### B1. Cột `at` là từ khóa trong DuckDB 2.x
- **Vấn đề:** `event_log.at`, `ai_tool_calls.at`, `audit_log.at` (06, 07, 10) làm DuckDB 2.0 báo lỗi cú pháp (`AT` dùng cho time travel).
- **Lựa chọn:** Giữ tên cột `at` như plan nhưng khai báo `"at"` có ngoặc kép.
- **Lý do:** Không đổi tên hợp đồng; lỗi khi quên ngoặc là lỗi cú pháp ngay lập tức, dễ thấy.
- **Ảnh hưởng:** Mọi SQL phải viết `"at"` (`SELECT "at" FROM event_log`, `ORDER BY "at"`). `repo.upsert_rows` tự quote tên cột.

### B2. `sku_aliases.raw_code` dùng `''` thay cho NULL
- **Vấn đề:** 02 §4 cho `raw_code` NULL khi nguồn không có mã (giá vốn, đơn hàng) và đặt `UNIQUE (source_type, raw_code, raw_name)`. Trong SQL, NULL khác nhau trong UNIQUE, nên upsert alias giá vốn sẽ nhân đôi dòng thay vì ghi đè.
- **Lựa chọn:** `raw_code VARCHAR NOT NULL DEFAULT ''`.
- **Ảnh hưởng:** Agent ghép SKU (ING-08) ghi `''` khi không có mã, lọc bằng `raw_code = ''` (không phải `IS NULL`).

### B3. Cột bổ sung (thêm, không đổi tên)
| Bảng | Cột | Lý do |
|---|---|---|
| `ingest_batches` | `duration_ms` | 06 §6 đo thời gian import bằng `ingest_batches.duration_ms` nhưng DDL 02 không có |
| `forecasts`, `scenarios`, `recommendations` | `input_hash` | 06 §3: kết quả cuối "luôn ghi DuckDB kèm `input_hash`"; 10 §2 so `input_hash` khi gửi duyệt |
| `goals` | `version`, `valid_from`, `valid_to` | 10 §4 nói mục tiêu có version, giữ bản cũ; DDL 05 không có |
| (mới) `schema_version` | `version, applied_at, note` | Ghi migration đã chạy để `migrate()` idempotent khi schema đổi sau M0 |

Tất cả đều nullable hoặc có default, nên code viết theo DDL gốc vẫn chạy.

### B4. Không thêm `shop_id` vào mọi bảng
- **Vấn đề:** 10 §8 nói "mọi bảng nghiệp vụ có cột `shop_id`", nhưng DDL ở 02–07 không có, và khóa chính của chúng không gồm `shop_id`.
- **Lựa chọn:** Giữ DDL như 02–07; chỉ `alerts`, `audit_log` có `shop_id` (đúng DDL 10). Thêm `SHOP_ID=shop-demo` vào config để repo/cache dùng khi cần.
- **Lý do:** Đa shop nằm ngoài phạm vi (00 §2). Thêm `shop_id` vào khóa chính của mọi bảng là đổi hợp đồng lớn, không đem lại gì cho demo.
- **Ảnh hưởng:** Khi làm đa shop thật cần một migration thêm `shop_id` + đổi khóa.

### B5. Chỗ plan dùng tên khác nhau, đã chốt theo DDL
| Chỗ | Plan nói | Chốt |
|---|---|---|
| Nguồn live/lịch sử của batch | 06 §5: `batch.source_kind='live'` | `ingest_batches.mode` (`history`/`live`) theo 02 |
| Khóa batch | 10 §4: `ingest_batches.id` | `batch_id` theo 02 |
| `model_registry` | 10 §4: `trained_at, train_window, scores` | Cột theo 03 §8 (`code_hash`, `created_at`, `params`); điểm nằm ở `model_scores` |
| `iroas_estimates` | 10 §4: `version, method, window` | Cột theo 04 §7 (`model_id`, `period_start/end`) |
| `decisions`, `actions`, `alerts`, `audit_log`, `ai_*` | khóa là `id` | Giữ `id` (không đổi thành `decision_id`) |
| `parameters.set_by` | 05: `…|ai_suggested_confirmed`; 10: còn `ai_suggested` | Ghi chú cả hai giá trị trong comment |
| 06, 07, 10 | `CREATE TABLE` không có `IF NOT EXISTS` | Thêm `IF NOT EXISTS` để `migrate()` idempotent |

### B6. Khóa ngoại giữ như plan
- Đã thử trên DuckDB 2.0: `UPDATE`, `INSERT … ON CONFLICT DO UPDATE` và `INSERT OR REPLACE` trên bảng cha đang được tham chiếu đều chạy. Giữ mọi `REFERENCES` của 02/03/05.
- **Ảnh hưởng:** Xóa dữ liệu (ví dụ nạp lại live 11.11, 06 §5) phải xóa bảng con trước (`order_lines` trước `orders`). `skus` phải có trước khi ghi `order_lines`, `inventory_snapshots`, `bundle_components`…

### B7. `scenario_results` có khóa chính trên cột JSON `action`
- DuckDB chấp nhận và chặn trùng theo văn bản JSON. Simulator (SIM-05) phải tuần tự hóa `ActionSpec` theo một cách cố định (cùng thứ tự trường, ví dụ `model_dump_json()` của cùng một model) để cùng hành động ra cùng khóa.

### B8. Múi giờ của connection
- Connection ghi và mọi cursor đọc chạy `SET TimeZone='UTC'`, nên `TIMESTAMPTZ` trả về là `datetime` UTC có tz (01 §7). Không có lệnh này DuckDB trả theo múi giờ máy (Asia/Saigon trên máy dev).

---

## C. Seed combo (WH-02)

### C1. Cơ chế khai báo
- **Lựa chọn:** File khai báo `app/warehouse/seeds/bundles.json` + `repo.declare_bundle(conn, BundleDecl)` (thay toàn bộ thành phần của một combo, idempotent) + `repo.seed_bundles(conn)`. `cli migrate` nạp seed mặc định (`--no-seed` để tắt); có lệnh riêng `cli seed`. API `GET/POST /bundles` (08) để task API-03 gọi `declare_bundle`.
- **Lý do:** Thành phần combo không có trong file nào của shop (02 §2.3); seed là "người dùng đã khai báo lúc onboarding" cho demo.

### C2. Dòng SKU giữ chỗ
- **Vấn đề:** `bundle_components` có khóa ngoại tới `skus`, mà `skus` chỉ có sau khi ingest đơn hàng.
- **Lựa chọn:** `ensure_sku()` tạo dòng `products`/`skus` tối thiểu (tên = mã) với `ON CONFLICT DO NOTHING`. Ingest sau đó upsert ghi đè tên, giá.
- **Ảnh hưởng:** Agent ingest **phải upsert** `products`/`skus` (`ON CONFLICT DO UPDATE`), không `INSERT` thuần, và không được hạ `is_bundle` của `COMBO-NOICHIEN` về false.

### C3. `product_id` của combo
- 02 §4: `product_id` = tiền tố SKU trước dấu `-`. Với `COMBO-NOICHIEN` luật này cho `COMBO`, sẽ gộp mọi combo vào một sản phẩm.
- **Lựa chọn:** `repo.derive_product_id(sku, is_bundle=True)` trả nguyên mã SKU cho combo. SKU thường giữ luật của plan.

---

## D. Core

### D1. `parse_vnd`
- Theo 02 §2.4, thêm: dùng `Decimal` + làm tròn nửa lên (tránh lỗi số thực, ví dụ `1,2tr`), nhận `₫`, `VNĐ`, `đồng`, dấu trừ, và **dấu phẩy phân tách nghìn khi không có đơn vị** (`"45,000"` → 45000, vì không có tiền lẻ dưới 1 đồng). Có đơn vị thì dấu phẩy là thập phân (`"1,5tr"`, `"12,5k"`). Nhận cả `int`/`float`. `"45..000"`, `"1.2.3"`, `"abc"`, `""` → `None`.

### D2. Event bus
- Sự kiện là pydantic model đóng băng (`frozen`), `type` = tên lớp (đúng giá trị ghi `event_log.type`). Đủ 7 sự kiện lõi (01 §2) + 6 sự kiện phụ (06 §1).
- `publish` chỉ đẩy vào queue; `run()` gọi handler tuần tự. Handler lỗi: log + phát `ComputeFailed` (trừ khi chính `ComputeFailed` lỗi, để không lặp vô hạn). Lỗi khi ghi log cũng không làm dừng bus.
- `subscribe("*", h)` nhận mọi sự kiện, dùng cho sink `event_log` (ORC-01 đăng ký).
- `LiveTick` dùng tên trường tiếng Anh có kiểu: `day` (date), `orders_inventory_until`, `ads_until` (datetime UTC) thay cho `ngay`, `don_hang_va_ton_den_het`, `ads_den_het` dạng chuỗi giờ của `trang_thai.json` (code tiếng Anh, thời gian luôn có tz).
- `RecommendationChanged.old/new` là `dict` (Recommendation đã tuần tự hóa) vì model `Recommendation` chưa có; SIM-05 có thể đổi sang kiểu cụ thể.

### D3. Logging
- structlog JSON ra stderr (`LOG_JSON=false` để đọc dễ khi dev). Logger lấy `sys.stderr` tại thời điểm ghi, không giữ stream cũ (lỗi thật đã gặp: CLI runner đóng stream làm bus chết).

### D4. Biến môi trường
- 01 §6 dùng `DATA_DIR`, `LIVE_DIR`; 06 và 10 dùng `MOCK_DATA_ROOT`, `LIVE_ROOT`. Chốt tên của 01, **nhận cả tên cũ làm alias**.
- Thêm từ 07/10: `AI_MODE`, `AI_SESSION_TOKEN_BUDGET`, `DEMO_MODE`, `LIVE_ENABLED`; từ 06: `ORC_WORKERS`; mới: `SHOP_ID`, `LOG_JSON`, `CORS_ORIGINS` (cho Vite `localhost:5173`).
- Biến rỗng (`OPENAI_MODEL_EMBED=`) đọc thành `None`. Đường dẫn tương đối tính từ `backend/`, không phụ thuộc thư mục đang đứng.

---

## E. API

### E1. `GET /api/v1/health`
- Trả theo khung `{"data": …, "meta": …}` của 08 §1. `data` gồm `status`, `db`, `ai` đúng như 01 (`ok|disabled`) và thêm `ai_mode`, `app_env`, `version`, `message`. `ai=ok` khi `AI_ENABLED` và có key (hoặc `AI_MODE=replay`).
- DB lỗi → HTTP 503, `status=degraded`, `db=error`. Không cần header `X-User`.

### E2. Đường dẫn OpenAPI
- 08 nói `/api/v1/openapi.json`; 01 §5 cho `npm run gen:api` đọc `/openapi.json`. Phục vụ **cả hai** (cùng nội dung). Swagger UI ở `/docs`.

### E3. Map lỗi
- `AppError` và lớp con trong `app/core/errors.py` mang `http_status` + `code` theo bảng 08 §1. Body sai schema → **400** `VALIDATION_ERROR` (08), không phải 422 mặc định của FastAPI. Lỗi không bắt → 500 `INTERNAL_ERROR`, không lộ chi tiết. Thêm hai mã ngoài bảng: `NOT_IMPLEMENTED` (501, cho endpoint giữ chỗ), `METHOD_NOT_ALLOWED` (405).
- Middleware gắn `X-Request-ID` (nhận từ client hoặc tự sinh `req-<ulid>`) và bind vào log.

---

## F. CLI

- Có: `migrate`, `reset`, `seed`, `tables`, `serve`. Stub: `ingest`, `rebuild-panel`, `backtest`, `fit` (12 · INF-04 liệt kê `fit`), `estimate-ads`, `eval` — in "Lệnh '…' chưa làm (task …). Xem <file plan>" và thoát **mã 3**, để script demo dừng rõ ràng thay vì im lặng.
- Trên Windows, `python -m app.cli` đổi stdout/stderr sang UTF-8 để in được tiếng Việt khi chuyển hướng ra file/pipe.

---

## G. Việc plan còn mâu thuẫn, chưa xử lý ở khung này

- **Mã lỗi dữ liệu:** 02 §3.1 dùng `ADS_TZ_SHIFT`, `ADS_GAP_DAY`, `PII_DROPPED`, `SUSPICIOUS_NOTE`; 10 §6 và 11 §2 dùng `pii_dropped`, `ads_timezone_shift`, `ads_missing_day`, `possible_instruction_in_note`. Đề xuất agent ING-07 dùng mã **của 02** (viết hoa) và `eval_data.py` ánh xạ theo đó.
- **`compute_state`:** 06 §4 coi `stale` là trạng thái suy ra (fresh nhưng `as_of` cũ), 08 liệt kê `stale` trong `compute_state`. Bảng `sku_compute_state.state` chỉ lưu `fresh|queued|computing|failed`; API tính `stale` lúc trả.
