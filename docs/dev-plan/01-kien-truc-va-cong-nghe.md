# 01 · Kiến trúc và công nghệ

Hệ thống là **một backend Python (FastAPI + DuckDB) chạy trong một process**, cộng một frontend React, chia 5 lớp và một lớp AI phía trên. Các lớp chỉ nói chuyện qua hợp đồng pydantic. Mọi tính toán nặng nằm ở backend; frontend chỉ hiển thị và gửi tham số.

## 1. Sơ đồ lớp

```
 Người vận hành ── hỏi bằng lời / ảnh ──┐
        ▲                               ▼
        │                 ┌──────────────────────────────────────────────┐
        │                 │ LỚP AI (app/ai)  OpenAI: hiểu yêu cầu → chọn │
        │                 │ & gọi công cụ → kiểm số → giải thích có nguồn│
        │                 └───────────────┬──────────────▲───────────────┘
        │                     gọi công cụ │              │ số thật từ công cụ
        │                 ┌───────────────▼──────────────┴───────────────┐
        │                 │ ĐIỀU PHỐI & CỔNG CÔNG CỤ (app/orchestrator)   │
        │                 │ sự kiện → xếp ưu tiên SKU → tính lại → cache  │
        │                 └──┬──────────────┬───────────────┬────────────┘
 ┌──────┴──────┐  ┌──────────▼───────┐ ┌────▼────────────┐ ┌▼──────────────────┐
 │ 5. GIAO DIỆN│◀─│ 3. QUYẾT ĐỊNH    │◀│ 2. MÔ HÌNH       │◀│ 1. DỮ LIỆU         │◀─ file CSV/JSON
 │ web/ 3 tầng │  │ economics/       │ │ forecast/        │ │ ingest/ warehouse/ │   live_stream/
 │ thanh vùng  │  │ simulator/       │ │ ads_effect/      │ │ panel/             │
 └──────┬──────┘  └──────────────────┘ └──────────────────┘ └────────────────────┘
        │ duyệt / thao tác       ┌───────────────────────────────────────────────┐
        └───────────────────────▶│ 4. QUẢN TRỊ (app/governance): nhật ký quyết   │
                                 │ định, phiên bản model & tham số, phân quyền,  │
                                 │ shadow mode, đánh giá sau ca                  │
                                 └───────────────────────────────────────────────┘
```

| Lớp | Package | Đầu vào | Đầu ra (hợp đồng) |
|---|---|---|---|
| 1 Dữ liệu | `ingest`, `warehouse`, `panel` | File, thư mục live | Bảng chuẩn trong DuckDB, `hourly_panel`, `data_quality_issues` |
| 2 Mô hình | `forecast`, `ads_effect` | `hourly_panel` | `ForecastResult`, `IroasEstimate` |
| 3 Quyết định | `economics`, `simulator` | Forecast, iROAS, tham số, mục tiêu, ràng buộc | `ScenarioResult`, `Recommendation` |
| 4 Quản trị | `governance` | Lựa chọn của người dùng | `Decision`, `Action`, bản ghi phiên bản |
| 5 Giao diện | `web/` | API `/api/v1`, SSE | Màn hình |
| AI | `ai` | Câu hỏi, công cụ | `AiAnswer` có `citations` |
| Điều phối | `orchestrator`, `live` | Sự kiện `DataUpdated` | Lịch tính lại, cache, `alerts` |

## 2. Luồng dữ liệu và luồng sự kiện

**Luồng dữ liệu (batch, lúc onboarding):**

```
file → ingest.adapters.<nguồn>.read() → RawFrame (đã bỏ PII)
     → ingest.normalize → ingest.quality.check() → data_quality_issues
     → ingest.sku_matching → sku_aliases (+ hàng chờ xác nhận)
     → warehouse.upsert(batch_id) → bảng chuẩn
     → panel.build_hourly_panel() → hourly_panel
     → forecast.backtest.run_all() → model_scores → champion theo (nhóm, bối cảnh)
     → ads_effect.estimate_all() → iroas_estimates
```

**Luồng sự kiện (trong ca, live):** dùng một event bus trong process (`app/core/events.py`, `asyncio.Queue`).

| Sự kiện | Phát bởi | Payload | Ai nghe |
|---|---|---|---|
| `DataUpdated` | `live.watcher` sau mỗi lần nạp | `batch_id, sources[], as_of, skus_changed[]` | orchestrator |
| `RecomputeRequested` | orchestrator | `sku_ids[], reason, priority` | worker tính toán |
| `ForecastUpdated` | forecast | `sku_id, forecast_id` | simulator, drift |
| `DriftDetected` | forecast.drift | `sku_id, direction, stat` | alerts |
| `RecommendationChanged` | simulator | `campaign_id, old, new` | alerts, SSE |
| `AlertRaised` | alerts | `alert_id` | SSE `/api/v1/stream` |
| `DecisionRecorded` | governance | `decision_id` | SSE, AI bàn giao ca |

Chi tiết điều phối, ưu tiên và cache: [06](06-dieu-phoi-va-hieu-nang.md).

## 3. Vì sao chọn stack này

| Lựa chọn | Lý do | Phương án đã loại |
|---|---|---|
| Python 3.11 | pandas/numpy/scipy/statsmodels cho dự báo và hồi quy; OpenAI SDK chính thức | Node: thiếu thư viện thống kê |
| FastAPI | Pydantic sẵn, OpenAPI tự sinh (frontend sinh type), SSE dễ | Flask: thiếu validation; Django: nặng |
| DuckDB (file `data/megaday.duckdb`) | SQL phân tích nhanh trên ~1 triệu dòng, đọc CSV trực tiếp, một file, không cần server | Postgres: thêm vận hành; SQLite: chậm khi aggregate |
| asyncio task + APScheduler trong cùng process | Đủ cho 1 shop, không cần broker | Celery/Redis: quá sức hackathon |
| React 18 + Vite + TS + Tailwind + TanStack Query | Nhanh, type-safe, cache query sẵn | Next.js: SSR không cần |
| OpenAI Python SDK | Có credits; function calling + structured outputs | — |

DuckDB chỉ cho phép **một tiến trình ghi**. Vì vậy backend giữ một connection ghi duy nhất (`app/warehouse/db.py`) và mở connection đọc riêng cho mỗi request. Script ngoài (eval, notebook) chỉ mở `read_only=True`.

## 4. Cấu trúc thư mục

```
hackathon/
├─ mock-data/                     # đã có: bộ sinh dữ liệu (không sửa trong plan này)
├─ docs/dev-plan/                 # bộ plan này
├─ backend/
│  ├─ pyproject.toml              # quản lý bằng uv
│  ├─ .env.example
│  ├─ data/                       # megaday.duckdb, cache/ (gitignore)
│  ├─ app/
│  │  ├─ main.py                  # tạo FastAPI app, mount router, khởi động scheduler + watcher
│  │  ├─ core/
│  │  │  ├─ config.py             # Settings (pydantic-settings) đọc .env
│  │  │  ├─ events.py             # EventBus, định nghĩa sự kiện
│  │  │  ├─ logging.py            # structlog JSON
│  │  │  ├─ timeutil.py           # VN_TZ, to_utc(), floor_hour(), is_double_day()
│  │  │  ├─ money.py              # parse_vnd()
│  │  │  └─ ids.py                # new_id() = ULID
│  │  ├─ ingest/
│  │  │  ├─ adapters/
│  │  │  │  ├─ base.py            # SourceAdapter, RawFrame
│  │  │  │  ├─ seller_center_orders.py
│  │  │  │  ├─ ads_report.py
│  │  │  │  ├─ inventory_sheet.py
│  │  │  │  ├─ cost_sheet.py
│  │  │  │  ├─ campaign_structure.py
│  │  │  │  └─ sale_program.py
│  │  │  ├─ detect.py             # nhận diện loại file từ header
│  │  │  ├─ pii.py                # bỏ / che dữ liệu cá nhân
│  │  │  ├─ quality.py            # luật kiểm chất lượng
│  │  │  ├─ timezone_check.py     # phát hiện lệch múi giờ
│  │  │  ├─ sku_matching.py       # ghép SKU 3 nguồn
│  │  │  └─ pipeline.py           # ingest_files(), ingest_live_delta()
│  │  ├─ warehouse/
│  │  │  ├─ db.py                 # connection ghi duy nhất, migrate()
│  │  │  ├─ schema.sql            # DDL (xem 02 §4)
│  │  │  └─ repo.py               # hàm đọc/ghi có type
│  │  ├─ panel/
│  │  │  ├─ build.py              # build_hourly_panel()
│  │  │  ├─ stock.py              # dựng tồn theo giờ, cờ hết hàng
│  │  │  └─ allocation.py         # chia chi ads chiến dịch về SKU
│  │  ├─ forecast/
│  │  │  ├─ base.py               # ForecastModel, ForecastResult
│  │  │  ├─ models/               # profile_ewma.py, seasonal_calendar.py, trend_damped.py,
│  │  │  │                        # tsb.py, analog.py, nowcast.py
│  │  │  ├─ classify.py           # phân nhóm nhu cầu
│  │  │  ├─ censoring.py
│  │  │  ├─ backtest.py
│  │  │  ├─ metrics.py
│  │  │  ├─ registry.py           # champion/challenger
│  │  │  ├─ labels.py             # verified/reference/scenario
│  │  │  └─ drift.py              # CUSUM
│  │  ├─ ads_effect/
│  │  │  ├─ dataset.py            # chọn giờ dùng được, loại giờ gây nhiễu
│  │  │  ├─ model.py              # posterior lưới (a, k, decay)
│  │  │  ├─ iroas.py              # IroasEstimate
│  │  │  └─ switchback.py         # thiết kế thử nghiệm (giai đoạn sau)
│  │  ├─ economics/
│  │  │  ├─ contribution.py       # đóng góp/sp
│  │  │  ├─ parameters.py         # danh mục tham số
│  │  │  ├─ goals.py              # hàm mục tiêu + ràng buộc
│  │  │  └─ breakeven.py          # ngưỡng, thanh vùng, độ nhạy
│  │  ├─ simulator/
│  │  │  ├─ policies.py           # 5 hành động → lịch chi theo giờ
│  │  │  ├─ engine.py             # Monte Carlo vector hóa
│  │  │  ├─ constraints.py        # tồn, combo, flash sale, cấu trúc chiến dịch
│  │  │  └─ recommend.py          # Recommendation
│  │  ├─ orchestrator/            # xem 06
│  │  ├─ governance/              # xem 10
│  │  ├─ ai/                      # xem 07
│  │  ├─ live/watcher.py          # theo dõi live_stream/trang_thai.json
│  │  └─ api/                     # router theo nhóm, xem 08
│  ├─ tests/{unit,integration,e2e}/
│  └─ eval/                       # chấm điểm bằng ground_truth, xem 11
└─ web/
   ├─ package.json, vite.config.ts, tailwind.config.ts
   └─ src/{pages,components,api,lib}/
```

## 5. Cài đặt local (Windows)

Máy hiện chỉ có Node.js v24, **chưa có Python**.

```powershell
# 1. Python 3.11 + uv
winget install --id Python.Python.3.11 -e
winget install --id astral-sh.uv -e
# mở terminal mới
python --version     # 3.11.x
uv --version

# 2. Sinh dữ liệu
cd D:\Projects\hackathon\mock-data
node generate.js --size demo

# 3. Backend
cd ..\backend
uv venv --python 3.11
uv sync                                   # cài theo pyproject.toml
copy .env.example .env                    # điền OPENAI_API_KEY
uv run python -m app.cli migrate
uv run python -m app.cli ingest --dir ..\mock-data\out\demo
uv run python -m app.cli backtest
uv run uvicorn app.main:app --reload --port 8000

# 4. Frontend
cd ..\web
npm install
npm run gen:api                           # sinh type từ http://localhost:8000/openapi.json
npm run dev                               # http://localhost:5173

# 5. Live
cd ..\mock-data
node replay.js --size demo --interval 20
```

Phụ thuộc Python (trong `pyproject.toml`): `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `duckdb`, `pandas`, `numpy`, `scipy`, `statsmodels`, `rapidfuzz`, `unidecode`, `apscheduler`, `sse-starlette`, `structlog`, `openai`, `python-ulid`. Dev: `pytest`, `pytest-asyncio`, `ruff`, `mypy`.

CLI (`app/cli.py`, dùng `typer`): `migrate`, `ingest --dir`, `rebuild-panel`, `backtest`, `estimate-ads`, `eval --dir`, `reset`.

## 6. Biến môi trường (`backend/.env.example`)

```dotenv
APP_ENV=dev
DUCKDB_PATH=data/megaday.duckdb
DATA_DIR=../mock-data/out/demo          # thư mục lịch sử để ingest
LIVE_DIR=../mock-data/out/demo/live_stream
LIVE_POLL_SECONDS=2
DISPLAY_TZ=Asia/Ho_Chi_Minh
PLATFORM_FEE_RATE=0.12

OPENAI_API_KEY=
OPENAI_MODEL_REASONING=                 # model suy luận cho agent điều tra, what-if khó
OPENAI_MODEL_FAST=                      # model nhỏ cho ghép cột, phân loại, trích ý định
OPENAI_MODEL_EMBED=                     # model embedding cho ghép SKU
AI_ENABLED=true                         # false → app chạy đủ, tắt phần hỏi đáp
AI_DAILY_TOKEN_BUDGET=2000000
AI_DEMO_CACHE=true                      # dùng câu trả lời cache cho kịch bản demo

MC_SAMPLES=1000
RECOMPUTE_TOP_N=50
LOG_LEVEL=INFO
```

Không hardcode tên model. Trước khi chọn, kiểm danh sách model và bảng giá hiện tại của OpenAI.

## 7. Quy ước code

- **Typing bắt buộc.** `mypy --strict` cho `app/economics`, `app/simulator`, `app/forecast/base.py`. Các package khác chạy `mypy` thường.
- **Hợp đồng giữa lớp = pydantic v2 model** đặt trong `schemas.py` của package. Không truyền DataFrame qua ranh giới lớp, trừ `panel` → `forecast`/`ads_effect` (dùng DataFrame có schema kiểm bằng hàm `validate_panel(df)`).
- **Tiền:** số nguyên VND (`int`). Tỷ lệ: `float` 0–1. Số lượng: `int` khi đếm, `float` khi là kỳ vọng.
- **Thời gian:** luôn `datetime` có tz UTC trong code và DB. Chỉ đổi sang `Asia/Ho_Chi_Minh` ở API response (trường `*_local`) và UI. Hàm `floor_hour(ts)` dùng chung.
- **Id:** ULID dạng chuỗi (`new_id()`), trừ khóa tự nhiên (SKU sàn, mã đơn, mã chiến dịch).
- **Logging:** structlog JSON. Mỗi log có `batch_id` / `request_id` / `sku_id` khi liên quan. Không log PII, không log nội dung prompt đầy đủ ở mức INFO.
- **Lỗi:** exception riêng trong `app/core/errors.py` (`DataQualityError`, `ConstraintViolation`, `StaleDataError`). API map sang mã HTTP + `code` máy đọc được.
- **Test:** `pytest`. Unit cho hàm thuần (parse tiền, công thức hòa vốn, CUSUM). Integration chạy trên `mock-data/out/nho` (30 SKU, < 1 giây sinh). E2E trên `demo` theo 6 nhân vật. CI local: `uv run ruff check && uv run mypy app && uv run pytest`.
- **Tên:** code tiếng Anh, snake_case. Chuỗi hiển thị tiếng Việt nằm trong `app/core/messages_vi.py` (backend) và `web/src/lib/i18n.ts`.
- **Tái lập:** mọi hàm ngẫu nhiên nhận `rng: np.random.Generator`. Seed từ request hoặc mặc định `20261111`.

## 8. Nối mock-data vào backend

| Thư mục mock-data | Backend dùng thế nào |
|---|---|
| `out/<cỡ>/*.csv, *.json` | `cli ingest --dir` lúc onboarding; hoặc kéo thả qua UI (`POST /api/v1/ingest/files`) |
| `out/<cỡ>/live_11_11/` | Không ingest trực tiếp (là dữ liệu cả ngày); chỉ để tham khảo |
| `out/<cỡ>/live_stream/` | `live.watcher` đọc `trang_thai.json` mỗi `LIVE_POLL_SECONDS`; khi đổi thì `ingest_live_delta()` |
| `out/<cỡ>/ground_truth/` | **Chỉ** `backend/eval/` đọc. Code trong `app/` bị cấm import đường dẫn này (test `tests/unit/test_no_ground_truth_leak.py` grep chuỗi `ground_truth` trong `app/`) |

Thứ tự demo: `generate.js --size demo` → `cli migrate` → `cli ingest` → `cli backtest` → `cli estimate-ads` → mở UI → `replay.js`.

## Tiêu chí hoàn thành

- [ ] `uv sync` + `npm install` chạy sạch trên máy Windows mới theo §5.
- [ ] `GET /api/v1/health` trả `{"status":"ok","db":"ok","ai":"ok|disabled"}`.
- [ ] Event bus có test: phát `DataUpdated` → orchestrator nhận trong < 100 ms.
- [ ] Test chống rò `ground_truth` chạy trong CI.
- [ ] Frontend sinh type từ OpenAPI, không viết tay type API.

## Task gợi ý

| Mã | Việc | Người | nd |
|---|---|---|---|
| INF-01 | Khởi tạo `backend/` (uv, pyproject, cấu trúc thư mục, ruff, mypy, pytest) | D3 | 0,5 |
| INF-02 | `core/`: config, logging, timeutil, money, ids, errors, events | D3 | 1 |
| INF-03 | `warehouse/db.py` + `migrate()` + `schema.sql` khung | D1 | 0,5 |
| INF-04 | CLI `typer` với các lệnh §5 | D3 | 0,5 |
| INF-05 | Khởi tạo `web/` (Vite, Tailwind, TanStack Query, sinh type OpenAPI) | D4 | 0,5 |
| INF-06 | Test chống rò ground_truth + script CI local | D3 | 0,25 |
| INF-07 | Hướng dẫn cài đặt Windows (README backend) đã chạy thử trên 1 máy sạch | D1 | 0,25 |
