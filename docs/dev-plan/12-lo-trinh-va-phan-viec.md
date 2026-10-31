# 12 · Lộ trình và phân việc

Toàn bộ task trong plan cần khoảng **72 người-ngày** theo ước lượng gốc, chưa tính thời gian tích hợp và sửa lỗi. Riêng nhóm P0 (thiếu thì demo hỏng) cần **51,5 người-ngày**. Trong khi đó, 4 dev × 10 ngày chỉ có **40 người-ngày**.

Vì vậy lịch 10 ngày dưới đây chạy theo **mức sàn**: mỗi task P0 làm bản rút gọn đã ghi rõ. Tổng mức sàn là **42 người-ngày** (kể cả diễn tập), tức vượt công suất khoảng 5%, không có dư.

Ba lựa chọn để có biên an toàn:
- thêm một dev backend thứ 5, nhận điều phối và API (khoảng 5 người-ngày),
- kéo dài lên khoảng 13 ngày để làm đủ ước lượng gốc của P0,
- hoặc chấp nhận mức sàn, và nếu tới cuối ngày 7 phần nào còn trễ thì chuyển phần đó sang phiên bản 48 giờ.

Nếu hackathon ngắn hơn 10 ngày, chuyển ngay sang danh sách 48 giờ ở mục 6.

Bảng ở file này là **nguồn chính** về người phụ trách, ưu tiên và mức sàn. Ước lượng gốc lấy từ bảng "Task gợi ý" cuối các file 01–11.

---

## 1. Mốc

| Mốc | Tên | Kết thúc (lịch 10 ngày) | Tiêu chí qua mốc (đo được) |
|---|---|---|---|
| M0 | Khung dự án | Ngày 1 | `make dev` chạy backend + frontend; CI chạy pytest + npm test + test chống rò `ground_truth`; DuckDB migrate tạo đủ bảng; mock `nho` và `demo` sinh được trên mọi máy (Windows) |
| M1 | Dữ liệu sạch | Ngày 4 | Import đủ 7 file cỡ `demo` < 60 giây; `eval_data.py`: ghép SKU tự động ≥ 90%, sai ≤ 1%, bắt 12/12 lỗi cài sẵn; `hourly_panel` có 300 SKU × 2.880 giờ; không còn PII trong DuckDB |
| M2 | Dự báo và chấm điểm | Ngày 6 | `eval_forecast.py`: champion tốt hơn baseline ngây thơ ≥ 15% WAPE trên 10.10; coverage 80% (PIT ngẫu nhiên hóa, 03 §7.2) trong [0,72; 0,88]; mọi SKU có nhãn tin cậy (luật WAPE ≤ 1,10 × WAPE_floor, 03 §7.3) |
| M3 | Quyết định | Ngày 8 | `POST /scenarios/run` < 1 giây (5 hành động, 1.000 mẫu); ví dụ trang 10 ra 22,5 / 16,5 / 16.875đ; e2e `test_noichien_1111.py` xanh (`schedule_hours` thắng, `pause` bị `campaign_must_run`); `eval_ads.py`: iROAS sai số trung vị ≤ 25%, gần thật hơn ROAS sàn ở ≥ 80% chiến dịch; màn So sánh chạy với dữ liệu thật |
| M4 | AI và live | Ngày 9 | Replay 11.11 chạy, mọi chiến dịch bị chạm được tính lại < 5 phút mỗi tick; cảnh báo size M và bình nước hiện đúng; what-if "hàng về sớm 1 ngày" lật kết luận CD002; agent điều tra size M đúng nguyên nhân; grounding 100%; test injection đạt; có bản ghi `AI_MODE=record` cho kịch bản |
| M5 | Demo | Ngày 10 | Kịch bản diễn tập 10 bước (11-kiem-thu-va-cham-diem mục 5) đạt 2 lần liên tiếp; có video dự phòng; slide có số từ báo cáo chấm điểm |

```
Ngày   1    2    3    4    5    6    7    8    9    10
M0   ━━◆
M1   ━━━━━━━━━━━━━━━━━━◆
M2             ━━━━━━━━━━━━━━━◆
M3        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━◆
M4                       ━━━━━━━━━━━━━━━━━━━━◆
M5                                          ━━━━◆
```

---

## 2. Bảng task đầy đủ

- **Ưu tiên:** P0 = thiếu thì demo hỏng; P1 = làm khi P0 của chính người đó xong; P2 = chỉ khi dư thời gian.
- **Gốc:** ước lượng ở file chuyên đề (người-ngày).
- **Sàn:** ước lượng phiên bản rút gọn dùng cho lịch 10 ngày. "—" = không làm trong 10 ngày.

### Nền tảng (01)

| Mã | Việc | Người | Gốc | Sàn | Ưu tiên | Phụ thuộc | Definition of Done / phiên bản sàn |
|---|---|---|---|---|---|---|---|
| INF-01 | Khởi tạo `backend/` (uv, pyproject, ruff, mypy, pytest) | D3 | 0,5 | 0,5 | P0 | — | `make test` chạy, CI xanh trên PR rỗng |
| INF-02 | `core/`: config, logging, timeutil, money, ids, errors, `events.py` | D3 | 1 | 0,5 | P0 | INF-01 | Sàn: config, timeutil (UTC ↔ GMT+7), money, EventBus; logging tối giản |
| INF-03 | `warehouse/db.py` + `migrate()` + `schema.sql` khung | D1 | 0,5 | 0,5 | P0 | INF-01 | `make db-reset` tạo DB |
| INF-04 | CLI `typer` (lệnh ở 01 mục 5) | D3 | 0,5 | 0,25 | P0 | INF-02 | Sàn: `ingest`, `fit`, `estimate-ads`, `serve` |
| INF-05 | Khởi tạo `web/` (Vite, Tailwind, TanStack Query, sinh type OpenAPI) | D4 | 0,5 | 0,5 | P0 | — | `npm run dev`, `npm run gen:api` chạy |
| INF-06 | Test chống rò `ground_truth` + script CI local | D3 | 0,25 | 0,25 | P0 | INF-01 | `tests/unit/test_no_ground_truth_leak.py` xanh |
| INF-07 | Hướng dẫn cài trên Windows, chạy thử máy sạch | D1 | 0,25 | — | P1 | INF-03 | — |

### Dữ liệu (02)

| Mã | Việc | Người | Gốc | Sàn | Ưu tiên | Phụ thuộc | Definition of Done / phiên bản sàn |
|---|---|---|---|---|---|---|---|
| WH-01 | `schema.sql` lớp dữ liệu + `repo.py` upsert | D1 | 0,5 | 0,5 | P0 | INF-03 | Đủ bảng ở 02 |
| WH-02 | `bundle_components` + API khai báo combo | D1 | 0,25 | 0,25 | P0 | WH-01 | Seed `COMBO-NOICHIEN = NOICHIEN2L + GIAYLOT50` |
| ING-01 | `adapters/base.py`, `detect.py`, `pii.py` | D1 | 0,5 | 0,5 | P0 | WH-01 | Nhận đúng loại 7 file; bỏ cột PII, che ghi chú |
| ING-02 | Adapter đơn hàng + trạng thái + `DUP_ORDER` | D1 | 0,5 | 0,5 | P0 | ING-01 | Số đơn trùng khớp manifest |
| ING-03 | Adapter ads + `timezone_check.py` + `ADS_GAP_DAY`/`ADS_LAG` | D1 | 1 | 0,75 | P0 | ING-01 | Giờ ads khớp đơn sau khi đổi +7 |
| ING-04 | Adapter tồn (ngày + giờ), ghi chú nhập/điều chỉnh, `restock_plans`, `restock_receipts` | D1 | 1 | 0,75 | P0 | ING-01 | Bắt đủ tồn âm; "Nhập {qty}" thành `restock_receipts` |
| ING-05 | Adapter giá vốn + `parse_vnd` + test | D1 | 0,5 | 0,5 | P0 | ING-01 | 0 dòng giá vốn đọc lỗi |
| ING-06 | Adapter chiến dịch + chương trình sale (`sale_commitments`) | D1 | 0,5 | 0,5 | P0 | ING-01 | Có ràng buộc CD002 khung flash sale |
| ING-07 | `quality.py`: luật kiểm + `data_quality_issues` | D1 | 1 | 0,75 | P0 | ING-02..06 | Sàn: đủ luật cho 12 lỗi cài sẵn + luật chặn (thiếu giá vốn) |
| ING-08 | `sku_matching.py`: chuẩn hóa, điểm, gán 1–1, ngưỡng, hàng chờ | D1 | 1,5 | 1 | P0 | ING-02..06 | Sàn: chuỗi không dấu + mã + giá; ≥ 90% tự động |
| ING-09 | Embedding gợi ý cho alias khó | D3 | 0,5 | — | P2 | ING-08 | — |
| PNL-01 | Aggregate đơn → panel + giá + lịch sự kiện | D1 | 1 | 0,75 | P0 | ING-08 | Số bán khớp `nhu_cau_theo_ngay` ±1% |
| PNL-02 | `stock.py`: tồn theo giờ, cờ hết hàng, combo | D1 | 1,5 | 1 | P0 | PNL-01 | Giờ hết hàng khớp ±1% |
| PNL-03 | `allocation.py`: chia chi ads về SKU | D2 | 0,5 | 0,5 | P0 | PNL-01 | Tổng chi SKU = chi chiến dịch |
| PNL-04 | Cập nhật tăng dần + `ingest_live_delta` | D1 | 1 | 0,75 | P0 | PNL-02 | Nạp 1 giờ live < 5 giây |

### Mô hình dự báo (03)

| Mã | Việc | Người | Gốc | Sàn | Ưu tiên | Phụ thuộc | Definition of Done / phiên bản sàn |
|---|---|---|---|---|---|---|---|
| FC-01 | `base.py`, `nb_quantiles`, schemas, `FitContext` | D2 | 1,5 | 1 | P0 | INF-02, PNL-02 | Sàn: hệ số giờ/thứ/sự kiện của shop, quantile NB |
| FC-02 | `censoring.py` + `profile_ewma` | D2 | 1 | 1 | P0 | FC-01 | Hiệu chỉnh giờ hết hàng; model mặc định cho mọi SKU |
| FC-03 | `seasonal_calendar` | D2 | 1 | — | P1 | FC-01 | Sàn dùng hệ số sự kiện trong `FitContext` |
| FC-04 | `trend_damped` + nhận pha | D2 | 0,75 | — | P1 | FC-01 | — |
| FC-05 | `tsb` cho hàng thưa | D2 | 0,5 | — | P1 | FC-01 | Sàn: hàng thưa dùng `profile_ewma`, nhãn reference |
| FC-06 | `analog` cho hàng mới | D2 | 0,5 | — | P1 | FC-01 | Sàn: MAYHUTMINI nhãn scenario |
| FC-07 | `nowcast` + `drift.py` (CUSUM) | D2 | 0,75 | — | P1 | FC-02 | Sàn: cảnh báo bán lệch dự báo ở ORC-05 thay cho drift |
| FC-08 | `classify.py` phân nhóm + API sửa nhóm | D2 | 0,5 | — | P1 | FC-09 | Sàn: nhóm khai báo sẵn cho nhân vật demo |
| FC-09 | `backtest.py`, `metrics.py`, `registry.py`, `labels.py` | D2 | 1,5 | 1 | P0 | FC-02 | Sàn: backtest 10.10, WAPE, coverage, nhãn |
| FC-10 | DDL `model_registry`, `model_scores`, `forecasts` + repo | D2 | 0,25 | 0,25 | P0 | WH-01 | — |

### Hiệu quả ads (04)

| Mã | Việc | Người | Gốc | Sàn | Ưu tiên | Phụ thuộc | Definition of Done / phiên bản sàn |
|---|---|---|---|---|---|---|---|
| ADS-01 | `dataset.py`: chiến dịch × giờ, luật loại giờ, đoạn tạm dừng do tồn | D2 | 1 | 0,5 | P0 | PNL-03 | Sàn: luật loại giờ hết hàng và tồn thấp |
| ADS-02 | Offset nhịp giờ `B_{h,w}` từ SKU không ads | D2 | 0,5 | — | P1 | ADS-01 | — |
| ADS-03 | `model.py`: posterior lưới (δ, κ, a), vector hóa | D2 | 1,5 | 1 | P0 | ADS-01 | Sàn: lưới thô |
| ADS-04 | `iroas.py`: iROAS, biên, quantile, nhãn, `uplift()` | D2 | 0,75 | 0,5 | P0 | ADS-03 | `IroasEstimate.uplift()` dùng được trong simulator |
| ADS-05 | Kiểm chéo bằng giờ hết ngân sách | D2 | 0,5 | — | P2 | ADS-04 | — |
| ADS-06 | DDL `iroas_estimates` + CLI `estimate-ads` | D2 | 0,25 | 0,25 | P0 | WH-01 | — |

### Kinh tế và mô phỏng (05)

| Mã | Việc | Người | Gốc | Sàn | Ưu tiên | Phụ thuộc | Definition of Done / phiên bản sàn |
|---|---|---|---|---|---|---|---|
| ECO-01 | `contribution.py` (gồm combo), chặn khi thiếu giá vốn | D2 | 0,5 | 0,5 | P0 | ING-05 | Nồi chiên 11.11 ra 9.280đ/sp |
| ECO-02 | `parameters.py` + bảng `parameters` + API có version, `set_by` | D3 | 1 | 0,5 | P0 | WH-01 | Ngoài khoảng bị từ chối; `ai_suggested` chưa có hiệu lực |
| ECO-03 | `goals.py` + 4 hàm mục tiêu | D2 | 0,75 | 0,5 | P0 | ECO-01 | Sàn: `profit` và `sell_target` |
| ECO-04 | `breakeven.py` + `ZoneBar` + test trang 10 | D4 | 0,5 | 0,5 | P0 | ECO-01 | 22,5 / 16,5 / 16.875đ; dùng chung công thức với frontend |
| SIM-01 | `policies.py`: lịch chi 5 hành động | D2 | 0,75 | 0,75 | P0 | ECO-03 | — |
| SIM-02 | `engine.py`: lấy mẫu [S,T], common random numbers, posterior iROAS | D2 | 1,5 | 1 | P0 | SIM-01, FC-09, ADS-04 | < 1 s cho 5 hành động |
| SIM-03 | `constraints.py`: tồn, combo, flash reserve, ràng buộc | D2 | 1 | 0,75 | P0 | SIM-02 | `campaign_must_run` chặn pause CD002 |
| SIM-04 | Độ nhạy (bisection) + `FlipCondition` | D2 | 0,75 | 0,25 | P0 | SIM-03 | Sàn: 2 yếu tố (`restock_delay_days`, giá trị hàng dư) |
| SIM-05 | `recommend.py` + DDL `scenarios`, `scenario_results`, `recommendations` | D3 | 0,75 | 0,5 | P0 | SIM-03 | Trả `Recommendation` đúng schema |
| SIM-06 | Test e2e §10, §11 + benchmark | D2 | 0,75 | 0,5 | P0 | SIM-05 | `test_noichien_1111.py` xanh |

### Điều phối và live (06)

| Mã | Việc | Người | Gốc | Sàn | Ưu tiên | Phụ thuộc | Definition of Done / phiên bản sàn |
|---|---|---|---|---|---|---|---|
| ORC-01 | Handler trên EventBus, `event_log`, tính lại mọi chiến dịch bị chạm | D3 | 0,5 | 0,5 | P0 | INF-02, SIM-05 | Handler lỗi không dừng bus |
| ORC-02 | Triage có điểm ưu tiên + hàng đợi | D3 | 0,75 | — | P1 | ORC-01 | — |
| ORC-03 | Worker pool, `input_hash`, StageCache | D3 | 0,75 | — | P1 | ORC-02 | Sàn: dựa vào cache mô phỏng của 05 |
| ORC-04 | `sku_compute_state`, quy tắc stale | D3 | 0,25 | 0,25 | P0 | ORC-01 | API trả `compute_state` |
| ORC-05 | AlertEvaluator (gồm `DriftDetected`), gom nhóm, ngân sách cảnh báo | D3 | 0,5 | 0,5 | P0 | ORC-01 | Size M và bình nước bật cảnh báo đúng khi replay |
| LIVE-01 | LiveWatcher + `live_cursor`, gọi `ingest_live_delta`, `LiveTick` | D1 | 0,5 | 0,5 | P0 | PNL-04 | Replay chạy lại từ đầu vẫn đúng |
| LIVE-02 | Ước lượng chi ads sau `ads_den_het` | D2 | 0,5 | — | P1 | LIVE-01 | — |
| EVAL-06 | Loadtest cỡ `tai_nang` | D1 | 0,5 | — | P1 | LIVE-01 | — |

### AI (07)

| Mã | Việc | Người | Gốc | Sàn | Ưu tiên | Phụ thuộc | Definition of Done / phiên bản sàn |
|---|---|---|---|---|---|---|---|
| AI-01 | `client.py`: vòng tool calling, ngân sách, log | D3 | 0,75 | 0,75 | P0 | INF-02 | — |
| AI-02 | Registry công cụ + Pydantic + kiểm quyền + công cụ đọc | D3 | 1 | 0,75 | P0 | AI-01, FC-09, ADS-04 | Sàn: công cụ dùng trong kịch bản demo |
| AI-03 | Công cụ tính nối `SimRequest` | D3 | 0,25 | 0,25 | P0 | AI-02, SIM-05 | Khớp `POST /scenarios/run` |
| AI-04 | Grounding check + chip nguồn | D3 | 1 | 0,75 | P0 | AI-01 | Câu có số lạ bị chặn |
| AI-05 | Nhập dữ liệu thông minh (map cột file lạ, đọc ảnh) | D3 | 1 | — | P1 | AI-02, ING-08 | Sàn: adapter cố định đã đủ cho 7 file mock |
| AI-06 | What-if tiếng Việt | D3 | 0,75 | 0,75 | P0 | AI-03, ECO-02 | Ca "hàng về sớm 1 ngày" lật kết luận CD002 |
| AI-07 | Agent điều tra 6 bước | D3 | 1 | 0,5 | P0 | AI-02, ORC-05 | Sàn: checklist chạy không cần LLM, LLM viết tóm tắt |
| AI-08 | Giải thích tầng 2 + ghi chú bàn giao | D3 | 0,5 | — | P1 | AI-03 | Sàn: tầng 2 dùng `Recommendation.reasons` |
| AI-09 | Chống injection | D3 | 0,25 | 0,25 | P0 | AI-02 | Test ghi chú cài sẵn đạt |
| AI-10 | Cache + `AI_MODE` record/replay | D3 | 0,5 | 0,5 | P0 | AI-01 | Kịch bản demo chạy offline |
| AI-11 | `suggest_parameter` + màn xác nhận | D3 | 0,25 | — | P1 | ECO-02 | — |
| EVAL-05 | Eval AI ~40 câu (`eval_ai.py`) | D2 + D3 | 1,5 | — | P1 | AI-06, AI-07 | — |

### API và quản trị (08, 10)

| Mã | Việc | Người | Gốc | Sàn | Ưu tiên | Phụ thuộc | Definition of Done / phiên bản sàn |
|---|---|---|---|---|---|---|---|
| API-01 | Khung router, user/role, khung phản hồi + lỗi, freshness | D3 | 0,5 | 0,5 | P0 | INF-02 | Contract test freshness |
| API-03 | Router ingest (files, batches, sku-queue, bundles) + data-quality | D4 | 0,75 | 0,75 | P0 | ING-08, WH-02, API-01 | Luồng upload → mapping → confirm chạy |
| API-04 | Router skus, campaigns, panel, forecast, scores, iroas | D3 | 0,5 | 0,5 | P0 | FC-09, ADS-04 | — |
| API-05 | Router goals, parameters, scenarios, breakeven, recommendations | D3 | 0,5 | 0,5 | P0 | SIM-05, ECO-04 | `CONSTRAINT_VIOLATION` đúng |
| API-06 | Router decisions, actions, alerts | D4 | 0,5 | 0,5 | P0 | GOV-01, ORC-05 | — |
| API-07 | SSE `/stream`, `/ai/chat`, live status/control | D3 | 0,5 | 0,5 | P0 | ORC-01, AI-01, LIVE-01 | Sàn: `/ai/chat` trả JSON một lần |
| GOV-01 | decisions/actions/alerts + máy trạng thái | D3 | 0,75 | 0,5 | P0 | INF-03 | Sàn: lưu, gửi duyệt, duyệt; đủ snapshot |
| GOV-02 | Ma trận quyền `require_role` | D4 | 0,25 | 0,25 | P0 | API-01 | Lead không tự duyệt được |
| GOV-03 | Snapshot + `governance.replay` | D2 | 1 | — | P1 | GOV-01 | — |
| GOV-04 | Kiểm lại thao tác | D3 | 0,5 | — | P1 | GOV-01 | Sàn: nút "Đánh dấu đã kiểm lại" thủ công trong GOV-01 |
| GOV-05 | Shadow mode | D2 | 1 | — | P2 | GOV-03 | — |
| GOV-06 | Test quét PII trên DuckDB và payload OpenAI | D1 | 0,25 | — | P1 | ING-01, AI-10 | — |
| GOV-07 | `audit_log` + middleware | D3 | 0,5 | — | P1 | API-01 | — |
| EVAL-04 | Contract test mọi endpoint | D3 | 0,5 | — | P1 | API-03..07 | — |

### Giao diện (09)

| Mã | Việc | Người | Gốc | Sàn | Ưu tiên | Phụ thuộc | Definition of Done / phiên bản sàn |
|---|---|---|---|---|---|---|---|
| WEB-01 | Router + AppShell + TanStack Query + fixture JSON | D4 | 0,5 | 0,5 | P0 | INF-05 | Chạy được với dữ liệu giả |
| WEB-02 | Thành phần dùng chung | D4 | 0,75 | 0,75 | P0 | WEB-01 | Đủ badge, chip, format |
| WEB-03 | Tổng quan ca | D4 | 0,75 | 0,75 | P0 | WEB-02, API-04 | — |
| WEB-04 | Chi tiết SKU + biểu đồ | D4 | 1 | — | P1 | WEB-02, API-04 | — |
| WEB-05 | So sánh 3 tầng + ZoneBar + thanh trượt | D4 | 1,5 | 1,5 | P0 | WEB-02, API-05, ECO-04 | 2 ví dụ ZoneBar đúng; ca CD002 hiện đúng |
| WEB-06 | Wizard nhập dữ liệu | D4 | 1 | 0,75 | P0 | API-03 | Sàn: 3 bước (tải → hàng chờ SKU → kiểm tra) |
| WEB-07 | Cảnh báo + Nhật ký + duyệt | D4 | 0,75 | 0,75 | P0 | API-06 | — |
| WEB-08 | Panel Trợ lý | D4 | 0,75 | 0,75 | P0 | API-07 | — |
| WEB-09 | Cài đặt + điều khiển demo | D4 | 1 | — | P1 | API-05, API-07 | Sàn: chạy replay bằng lệnh trong terminal |
| WEB-10 | SSE client + invalidate + dark mode + a11y | D4 | 0,5 | 0,5 | P0 | API-07 | — |

### Chấm điểm và demo (11)

| Mã | Việc | Người | Gốc | Sàn | Ưu tiên | Phụ thuộc | Definition of Done / phiên bản sàn |
|---|---|---|---|---|---|---|---|
| EVAL-01 | Fixture `nho` + golden test | D1 | 0,5 | — | P1 | ING-08, PNL-02 | — |
| EVAL-02 | `eval_data.py` + `eval_forecast.py` + `run_all.py` | D2 | 0,75 | 0,5 | P0 | ING-07, ING-08, FC-09 | Báo cáo .md + .json |
| EVAL-07 | `eval_ads.py` + câu cho slide | D4 | 0,25 | 0,25 | P0 | ADS-04 | — |
| EVAL-03 | `--policy` cho mock-data + `eval_decision.py` | D2 | 1,5 | — | P2 | SIM-05 | — |
| DEMO-01 | Diễn tập, bản ghi AI, video dự phòng | cả team (chủ trì D4 + PM) | 0,5/người | 0,5/người | P0 | M4 | 10 bước đạt 2 lần liên tiếp |
| DEMO-02 | Playwright E2E | D4 | 1 | — | P2 | WEB-05..08 | — |

### Tổng tải theo mức sàn

Tính từ các bảng trên. DEMO-01 tính 0,5 cho mỗi dev; EVAL-05 chia đôi D2 và D3.

| Người | P0 mức sàn (gồm DEMO-01) | P0 ước lượng gốc | P1 + P2 ước lượng gốc |
|---|---|---|---|
| D1 Data | 10 | 12,25 | 1,5 |
| D2 ML | 11,25 | 15,5 | 9,75 |
| D3 Backend + AI | 11,25 | 14 | 6 |
| D4 Frontend + API phụ | 9,5 | 9,75 | 3 |
| PM / pitch lead | — | — | — |
| **Tổng** | **42** | **51,5** | **20,25** |

Đọc bảng:
- D2 và D3 là điểm nghẽn: ở mức sàn mỗi người vẫn quá khoảng 1,25 ngày. Ai xong sớm thì nhận việc của D2/D3 trước. Gợi ý: D4 nhận EVAL-02, D1 nhận ORC-04 và ORC-05.
- PM không nhận task code. PM giữ kịch bản demo, slide, câu hỏi ban giám khảo, và là người dùng thử ở mỗi buổi demo nội bộ.

---

## 3. Lịch 10 ngày theo người (mức sàn)

| Ngày | D1 Data | D2 ML | D3 Backend + AI | D4 Frontend |
|---|---|---|---|---|
| 1 | INF-03 · WH-01 · ING-01 | FC-10 · ADS-06 · đọc dữ liệu, chốt đặc trưng | INF-01 · INF-02 · INF-06 | INF-05 · WEB-01 |
| 2 | WH-02 · ING-02 · ING-03 | ECO-01 · ECO-03 | INF-04 · API-01 · ECO-02 | WEB-02 · ECO-04 |
| 3 | ING-04 · ING-05 · ING-06 | SIM-01 (đầu vào giả) | GOV-01 · AI-01 | WEB-03 (fixture) · GOV-02 |
| 4 | ING-07 · ING-08 **→ M1** | SIM-02 (dự báo giả, iROAS prior) | AI-04 | WEB-06 (fixture) |
| 5 | PNL-01 · PNL-02 | FC-01 · PNL-03 | AI-02 (dữ liệu thô) | API-03 · WEB-06 nối API |
| 6 | PNL-04 · LIVE-01 | FC-02 · FC-09 · EVAL-02 **→ M2** | SIM-05 · API-04 | WEB-05 (fixture) |
| 7 | Hỗ trợ D2: dữ liệu cho ADS-01 · kiểm số panel | ADS-01 · ADS-03 · ADS-04 | ORC-01 · ORC-04 · ORC-05 · API-05 | WEB-05 nối API · EVAL-07 |
| 8 | P1: INF-07, EVAL-01 hoặc đỡ D2 | SIM-03 · SIM-04 · SIM-06 **→ M3** | AI-03 · AI-06 · API-07 | API-06 · WEB-07 |
| 9 | Sửa lỗi tích hợp, chạy replay đi chạy lại | Chỉnh số trên nhân vật demo | AI-07 · AI-09 · AI-10 **→ M4** | WEB-08 · WEB-10 |
| 10 | DEMO-01 | DEMO-01 | DEMO-01 (bản ghi AI) | DEMO-01 (video) **→ M5** |

Lưu ý thứ tự:
- M1 ghi ở ngày 4 vì dữ liệu "sạch" (ING xong) là mốc. Panel (PNL) xong ngày 6, ngay trước khi D2 cần dữ liệu thật để fit.
- D2 ngày 2–4 viết kinh tế và mô phỏng trên **đầu vào giả** theo schema của 05, để không phải chờ panel.
- **Đóng băng tính năng cuối ngày 9.** Ngày 10 chỉ sửa lỗi và diễn tập.

---

## 4. Đường găng

```
INF-01 → INF-03 → WH-01 → ING-01 → ING-02..06 → ING-08 → PNL-01 → PNL-02 → FC-01 → FC-02 → FC-09
                                                             └→ PNL-03 → ADS-01 → ADS-03 → ADS-04 ┐
FC-09 ──────────────────────────────────────────────────────────────────────────────────────────┴→ SIM-02 (thay đầu vào giả) → SIM-03 → SIM-05 → API-05 → WEB-05 → AI-06 → DEMO-01
```

- Đoạn dài nhất là D2 từ FC-01 đến SIM-06 (khoảng 5,75 người-ngày nối tiếp, từ ngày 5 đến ngày 8).
- Giảm rủi ro:
  - SIM-01/02 viết trước trên dự báo giả và iROAS prior.
  - WEB-05 dùng fixture lấy đúng ví dụ ở 08-api mục 3.8.
  - Nếu ADS-03 trễ, SIM-02 dùng `IroasEstimate` prior (nhãn `scenario`) để không chặn M3.

---

## 5. Hợp đồng phải chốt sớm

Muốn làm song song thì chốt **hợp đồng trước, cài đặt sau**:

| Ngày | Hợp đồng | Ai chốt | Ai dùng |
|---|---|---|---|
| 1 | `schema.sql` (02), khung phản hồi API (08 mục 1), danh sách sự kiện (01, 06) | D1, D3 | mọi người |
| 2 | Pydantic `ForecastResult`, `IroasEstimate`, `SimRequest`, `ActionSpec`, `ScenarioResult`, `Recommendation`, `ZoneBar`, `FlipCondition` (03–05) | D2 | D3, D4 |
| 2 | Fixture JSON cho mọi màn (lấy từ ví dụ ở 08) | D4 + D3 | D4 |
| 3 | Danh sách công cụ AI + schema (07 mục 3) | D3 | D2 (kiểm số) |

Đổi một hợp đồng đã chốt thì phải báo nhóm và sửa file docs trong cùng PR.

---

## 6. Danh sách cắt giảm nếu chỉ còn 48 giờ

**Giữ (mức sàn tối thiểu):**

| Phần | Phiên bản 48 giờ |
|---|---|
| Nhập dữ liệu | Adapter cố định cho 7 file mock. Ghép SKU bằng chuỗi không dấu. Wizard 2 bước (tải lên → xem lỗi) |
| Panel | Theo giờ: bán, tồn, hết hàng, chi ads chia đều theo nhu cầu |
| Dự báo | Chỉ `profile_ewma` + quantile NB. Backtest 10.10 ra WAPE và coverage |
| iROAS | So giờ bật/tắt ads, bỏ giờ tồn thấp, khoảng bằng bootstrap |
| Mô phỏng | 3 hành động: continue, pause, schedule_hours. Ràng buộc CD002. Hòa vốn + ZoneBar |
| Màn hình | Tổng quan ca + So sánh (tầng 1, 2) + panel Trợ lý |
| AI | What-if + grounding + chống injection + `AI_MODE=replay` |
| Live | `replay --from 9`; frontend poll `/live/status` và `/skus` mỗi 5 giây, bỏ SSE |
| Chấm điểm | `eval_forecast.py` và `eval_ads.py` cho slide |

**Bỏ:** SSE, vai trò/quyền, máy trạng thái duyệt (thay bằng nút "Lưu quyết định" ghi nhật ký), agent điều tra (thay bằng checklist cố định, không cần LLM), cảnh báo ngoài `stockout_risk` và `sales_below_forecast`, Cài đặt, eval AI, loadtest, shadow mode, regret, đọc ảnh, ghi chú bàn giao, Playwright.

**Lịch 48 giờ:**

| Khối giờ | D1 | D2 | D3 | D4 |
|---|---|---|---|---|
| 0–8 | Schema + adapter đơn, ads | Kinh tế + hòa vốn (trang 10, CD002) | Khung backend + client AI | Khung web + ZoneBar với fixture |
| 8–16 | Adapter tồn, giá vốn, chiến dịch + ghép SKU | `profile_ewma` + quantile | Công cụ đọc + `run_scenario` | Màn So sánh với fixture |
| 16–24 | Panel + live poll | Backtest 10.10 + iROAS đơn giản | Grounding + what-if | Tổng quan ca + nối API |
| 24–32 | Hỗ trợ tích hợp | Mô phỏng 3 hành động + ràng buộc | Chống injection + replay | Panel Trợ lý |
| 32–40 | Báo cáo chấm điểm | Kiểm số trên nhân vật demo | Bản ghi AI cho kịch bản | Hoàn thiện UI |
| 40–48 | Diễn tập | Diễn tập | Diễn tập | Video dự phòng |

---

## 7. Cách làm việc

| Nghi thức | Quy ước |
|---|---|
| Daily | 15 phút lúc 9:00. Mỗi người nói: hôm qua xong mã nào, hôm nay làm mã nào, đang bị ai chặn |
| Bảng việc | Mỗi task là một thẻ, mang đúng mã ở bảng trên. Cột: Chưa làm · Đang làm · Chờ review · Xong |
| Nhánh | `feat/<MÃ>-mo-ta-ngan`, ví dụ `feat/ING-03-ads-utc` |
| Commit | Câu đầu ≤ 72 ký tự, có mã task |
| PR | ≤ 400 dòng đổi (không tính dữ liệu, file sinh). Mô tả: làm gì, test gì, ảnh chụp nếu là UI |
| Review | 1 người duyệt, trong 4 giờ làm việc. D1 ↔ D2 review chéo dữ liệu/model; D3 ↔ D4 review chéo API/UI |
| Nhánh chính | `main` được bảo vệ, chỉ merge qua PR có CI xanh |
| Definition of Done chung | Code + test + CI xanh + đã chạy với dữ liệu `demo` + cập nhật docs nếu đổi hợp đồng |
| Demo nội bộ | Cuối ngày 4, 6, 8: 15 phút chạy thử với PM để chỉnh kịch bản |
| Đóng băng | Cuối ngày 9 |
