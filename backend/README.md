# MegaDay Twin · backend

FastAPI + DuckDB + OpenAI, Python 3.11, quản lý bằng [uv](https://docs.astral.sh/uv/). Một process duy nhất: API, event bus, (sau này) watcher live và scheduler.

Plan: [01 · Kiến trúc và công nghệ](../docs/dev-plan/01-kien-truc-va-cong-nghe.md). Nhật ký quyết định của khung này: [docs/quyet-dinh/INF-backend.md](../docs/quyet-dinh/INF-backend.md).

## Cài đặt

Cần Python 3.11 và uv. Không dùng `pip` toàn cục; mọi lệnh đi qua `uv run`.

```powershell
# Windows (PowerShell)
winget install --id astral-sh.uv -e        # mở terminal mới sau khi cài
uv python install 3.11
cd backend
uv sync                                    # tạo .venv + cài đúng uv.lock
copy .env.example .env                     # điền OPENAI_API_KEY nếu dùng AI
uv run python -m app.cli migrate           # tạo data/megaday.duckdb, đủ bảng + seed combo
uv run python tasks.py dev                 # http://localhost:8000/docs
```

```bash
# Linux / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
cd backend && uv sync && cp .env.example .env
uv run python -m app.cli migrate
make dev                                   # = uv run python tasks.py dev
```

Kiểm nhanh: `curl http://localhost:8000/api/v1/health` →

```json
{"data":{"status":"ok","db":"ok","ai":"disabled","ai_mode":"live","app_env":"dev","version":"0.1.0","message":"..."},
 "meta":{"computed_at":"2026-11-11T14:59:00+07:00","data_as_of":null,"compute_state":null,"confidence":null,"sources":[]}}
```

`ai` là `ok` khi `AI_ENABLED=true` và có `OPENAI_API_KEY` (hoặc `AI_MODE=replay`), ngược lại `disabled`. OpenAPI: `/api/v1/openapi.json` (cũng phục vụ ở `/openapi.json` cho `npm run gen:api`).

## Lệnh

### Lệnh dev (`tasks.py`, chạy được trên Windows lẫn Linux)

| `uv run python tasks.py …` | `make …` | Làm gì |
|---|---|---|
| `install` | `make install` | `uv sync` |
| `lint` | `make lint` | `ruff check` + `ruff format --check` |
| `fmt` | `make fmt` | tự sửa lint + định dạng |
| `typecheck` | `make typecheck` | `mypy app` |
| `test` | `make test` | `pytest` |
| `check` | `make check` | lint + typecheck + test, **giống CI**. Chạy trước khi push |
| `migrate` | `make migrate` | tạo/cập nhật bảng |
| `db-reset` | `make db-reset` | xóa DuckDB rồi tạo lại |
| `dev` | `make dev` | uvicorn `--reload`, cổng 8000 |

Có thể ghép: `uv run python tasks.py fmt check`. `Makefile` chỉ gọi lại `tasks.py`, nên máy không có `make` không thiếu gì.

### CLI ứng dụng (`uv run python -m app.cli …`)

| Lệnh | Trạng thái | Ghi chú |
|---|---|---|
| `migrate [--db PATH] [--no-seed]` | có | Idempotent. Mặc định nạp seed combo |
| `reset [--yes]` | có | Xóa file DuckDB + WAL rồi migrate |
| `seed` | có | Nạp khai báo combo mẫu `COMBO-NOICHIEN = NOICHIEN2L + GIAYLOT50` (WH-02) |
| `tables` | có | Liệt kê bảng và số dòng |
| `serve [--host --port --reload]` | có | uvicorn `app.main:app` |
| `ingest --dir DIR [--force] [--no-panel]` | có | Nạp file của shop (mặc định `../mock-data/out/demo`), in tóm tắt: số dòng mỗi bảng, alias chờ xác nhận, vấn đề theo mã, thời gian. Chạy lại cùng thư mục thì bỏ qua file đã nạp (theo SHA-256) |
| `ingest-live --dir DIR` | có | Nạp phần mới của thư mục live (`live_stream/` của `replay.js`, hoặc `live_11_11/` cả ngày). Gọi lại mỗi khi `trang_thai.json` đổi |
| `rebuild-panel` | có | Dựng lại toàn bộ `hourly_panel` |
| `eval [--size demo] [--live-hours 3]` | có | Chạy `python -m eval.eval_data` (tiến trình riêng) |
| `backtest` | có | Backtest rolling-origin → `model_scores` |
| `fit` | có | Dự báo champion cho mọi SKU → `forecasts` |
| `estimate-ads` | có | iROAS mọi chiến dịch → `iroas_estimates` |

### Nạp dữ liệu và chấm điểm lớp dữ liệu (M1)

```powershell
cd backend
uv run python -m app.cli reset --yes                         # DuckDB trống + seed combo
uv run python -m app.cli ingest --dir ../mock-data/out/demo  # ~15 giây trên máy dev
uv run python -m app.cli ingest-live --dir ../mock-data/out/demo/live_11_11   # cả ngày 11.11
uv run python -m eval.eval_data --size demo                  # hoặc: uv run python -m app.cli eval
```

Phát lại 11.11 theo giờ: chạy `node replay.js --size demo --interval 20` trong `mock-data/`, rồi gọi `ingest-live --dir ../mock-data/out/demo/live_stream` sau mỗi giờ (LIVE-01 sẽ tự gọi). `eval_data` tạo DuckDB tạm, chạy pipeline thật, so với `ground_truth/` và ghi `eval/reports/data_<cỡ>_<thời điểm>.md` + `.json` (không commit).

Pipeline (`app/ingest/pipeline.py`): nhận diện file theo header → adapter (bỏ PII khi đọc) → ghi `products/skus/orders/order_lines`, `campaigns`, `sale_commitments` → ghép SKU (`sku_matching`) → gán `sku_id` cho tồn/kế hoạch nhập/phiếu nhập/giá vốn (dòng có alias đang chờ thì giữ lại, không ghi) → `ad_hourly`, `inventory_snapshots`, `restock_*`, `cost_items` → `data_quality_issues` (`app/ingest/quality.py`) → `hourly_panel` → `DataUpdated`. Quyết định: [docs/quyet-dinh/M1-pipeline.md](../docs/quyet-dinh/M1-pipeline.md).

## API (M3)

```powershell
cd backend
uv run python -m app.cli reset --yes
uv run python -m app.cli ingest --dir ../mock-data/out/demo
uv run python -m app.cli backtest; uv run python -m app.cli fit; uv run python -m app.cli estimate-ads
uv run uvicorn app.main:app --port 8000          # hoặc: uv run python tasks.py dev
```

Mọi endpoint ở `http://localhost:8000/api/v1`, OpenAPI ở `/api/v1/openapi.json`, Swagger ở `/docs`. Trừ `/health`, request phải có `X-User` (vd `u-ops-1`) và `X-Role: viewer|operator|lead`.

```bash
H='-H X-User:u-ops-1 -H X-Role:operator'
curl $H http://localhost:8000/api/v1/freshness
curl $H "http://localhost:8000/api/v1/skus?limit=5"
curl -H X-User:u-lead-1 -H X-Role:lead -H Content-Type:application/json -X POST      http://localhost:8000/api/v1/goals -d '{"scope_id":"CD002","kind":"profit","deadline":"2026-11-12T00:00:00+07:00"}'
curl $H -H Content-Type:application/json -X POST http://localhost:8000/api/v1/scenarios/run      -d '{"campaign_id":"CD002","samples":1000}'
```

Các nhóm route: `system` (`/me`, `/freshness`), `skus`, `campaigns`, `goals-parameters`, `scenarios` (+ `/recommendations`), `ingest` (+ `/data-quality`). Danh sách đầy đủ, quyền, tên trường và ví dụ phản hồi thật: [docs/quyet-dinh/M3-api.md](../docs/quyet-dinh/M3-api.md). Lần gọi `/scenarios/run` đầu tiên ở một mốc mới phải dự báo nên mất 10–25 giây; sau khi `fit` xong, mỗi lần chạy dưới 1 giây. Test API: `uv run pytest tests/api` (e2e demo: `tests/api/test_cd002_1111.py`; đo thời gian: `-m perf`).

## Cấu trúc

```
backend/
├─ pyproject.toml, uv.lock, .python-version   # uv; ruff, mypy, pytest cấu hình ở pyproject
├─ tasks.py, Makefile                         # lệnh dev đa nền tảng
├─ .env.example                               # mẫu biến môi trường, không có key thật
├─ data/                                      # megaday.duckdb (gitignore)
├─ app/
│  ├─ main.py          # create_app(): /api/v1, xử lý lỗi, lifespan migrate + EventBus
│  ├─ cli.py           # typer
│  ├─ core/            # config, timeutil, money, ids, errors, events, logging, messages_vi
│  ├─ warehouse/       # db.py (1 connection ghi, migrate), schema.sql (HỢP ĐỒNG), repo.py, seeds/
│  ├─ api/             # router.py (/api/v1), deps.py (X-User/X-Role), common.py, snapshot.py, system/skus/campaigns/economics/scenarios/ingest.py
│  └─ ingest/ panel/ forecast/ ads_effect/ economics/ simulator/
│     orchestrator/ governance/ ai/ live/      # package rỗng, docstring trỏ file plan + task
├─ tests/{unit,integration,e2e}/
└─ eval/               # chấm điểm bằng đáp án — nơi DUY NHẤT được đọc ground_truth/
```

## Hợp đồng cho các module khác

| Cần gì | Dùng | Ghi chú |
|---|---|---|
| Cấu hình | `app.core.config.get_settings()` | Đường dẫn tương đối tính từ `backend/` |
| Thời gian | `app.core.timeutil`: `VN_TZ`, `to_utc`, `to_local`, `parse_local("2026-11-11 14:00")`, `floor_hour`, `local_date`, `is_double_day`, `is_pre_double_day`, `is_post_double_day`, `is_payday`, `local_day_bounds_utc`, `iso_local` | Code và DB luôn UTC có tz |
| Tiền | `app.core.money.parse_vnd(text) -> int \| None`, `format_vnd` | `None` → issue `MONEY_FORMAT` |
| Id | `app.core.ids.new_id("dec") -> "dec-01J…"` | ULID |
| Lỗi | `app.core.errors`: `DataQualityError`(422), `ConstraintViolation`(409), `StaleDataError`(409 `STALE_INPUT`), `StateConflict`(409), `ParamOutOfRange`(400), `NotFound`(404), `Forbidden`(403)… | API tự map sang `{"error":{code,message,details}}` |
| Sự kiện | `app.core.events`: `EventBus`, `DataUpdated`, `RecomputeRequested`, `ForecastUpdated`, `DriftDetected`, `RecommendationChanged`, `AlertRaised`, `DecisionRecorded`, `GoalChanged`, `ParameterChanged`, `StageDone`, `ComputeFailed`, `AlertResolved`, `LiveTick` | Bus ở `app.state.bus`; `subscribe("*", …)` để ghi `event_log` |
| DB | `app.warehouse.db.get_db()`: `.write` (connection ghi duy nhất), `with db.reader() as cur` | Session `TimeZone='UTC'` |
| Ghi/đọc | `app.warehouse.repo`: `upsert_rows`, `upsert_df`, `fetch_all`, `fetch_one`, `as_json`, `ensure_sku`, `declare_bundle`, `get_bundle_components`, `seed_bundles` | Upsert theo khóa tự nhiên, idempotent |
| Nạp dữ liệu | `app.ingest.pipeline`: `ingest_files(conn, dir_or_files, *, force, build_panel, bus) -> IngestReport`, `ingest_live_delta(conn, live_dir, *, bus) -> LiveDeltaReport`, `reset_live_data` | Phát `DataUpdated` (+ `LiveTick`) lên `bus` nếu truyền |
| Chất lượng dữ liệu | `app.ingest.quality`: `run_cross_source_checks`, `refresh_all`, `write_batch_issues`, `issue_counts` | Mã lỗi theo 02 §3.1, mức ở `adapters.base.ISSUE_SEVERITY` |
| Dữ liệu test | `tests/conftest.py`: `loaded_db` (connection, `nho` đã nạp + panel, chỉ đọc), `loaded_ingest`; `tests/mockdata.py`: `load_mock(conn, size, live=...)` | Test cần ghi thì tự dựng DB riêng |
| Chuỗi tiếng Việt | `app.core.messages_vi` | Không viết chuỗi hiển thị rải rác trong code |
| Log | `app.core.logging.get_logger(__name__)` | JSON; không log PII |

Lưu ý schema: cột `at` (bảng `event_log`, `ai_tool_calls`, `audit_log`) là từ khóa trong DuckDB 2.x, **luôn viết `"at"`** trong SQL. `sku_aliases.raw_code` dùng `''` (không phải NULL) khi nguồn không có mã.

## Quy ước

Theo [01 §7](../docs/dev-plan/01-kien-truc-va-cong-nghe.md): code tiếng Anh, chuỗi hiển thị tiếng Việt trong `messages_vi.py`; tiền `int` VND; thời gian UTC; hợp đồng giữa lớp là pydantic v2. `mypy` strict cho `app/core`, `app/warehouse`, `app/economics`, `app/simulator`, `app/forecast/base.py`; các package khác chạy `mypy` thường (vẫn bắt buộc annotate hàm).
