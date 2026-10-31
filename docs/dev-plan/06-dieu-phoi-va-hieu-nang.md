# 06 · Điều phối tính toán và chế độ live

Mọi việc tính lại chạy qua một event bus nội bộ trong cùng process FastAPI. Mỗi khi có dữ liệu mới, hệ thống chỉ tính lại các SKU có điểm ưu tiên cao hoặc vừa đổi dữ liệu. Mục tiêu: **dưới 5 phút cho top 50 SKU** khi chạy dữ liệu cỡ `demo` giữa ngày 11.11, và giao diện không bao giờ hiện số cũ mà không gắn nhãn.

Module: `backend/app/core/events.py` (EventBus và định nghĩa sự kiện, khung ở 01-kien-truc-va-cong-nghe, cài đặt ở INF-02), `backend/app/orchestrator/` (triage, worker, cache) và `backend/app/live/` (watcher `live_stream`).

---

## 1. Luồng sự kiện

```
 watcher live_stream ─┐
 upload file (ingest)─┼─▶ DataUpdated ──▶ Triage ──▶ RecomputeRequested (theo SKU, có độ ưu tiên)
 đổi mục tiêu/tham số ┘                                │
                                                       ▼
                                          worker pool (asyncio, N=4)
                                          panel → forecast → simulator → recommendation
                                                       │
                                     ┌─────────────────┴──────────────────┐
                                     ▼                                    ▼
                         RecommendationChanged                    ComputeFailed
                                     │                                    │
                                     ▼                                    ▼
                           AlertEvaluator ──▶ AlertRaised        alert loại "data/compute"
                                     │
                                     ▼
                           SSE /api/v1/stream (UI cập nhật)
```

Bảy sự kiện lõi đã định nghĩa ở 01-kien-truc-va-cong-nghe. File này thêm 5 sự kiện phụ (đánh dấu *) cho điều phối và UI.

| Sự kiện | Phát ra bởi | Payload chính | Ai nghe |
|---|---|---|---|
| `DataUpdated` | live watcher, ingest | `batch_id`, `sources[]`, `as_of`, `skus_changed[]` (rỗng = chưa biết) | orchestrator (triage) |
| `RecomputeRequested` | orchestrator | `sku_ids[]`, `reason`, `priority` | worker |
| `ForecastUpdated` | forecast | `sku_id`, `forecast_id` | simulator, drift |
| `DriftDetected` | forecast.drift (03) | `sku_id`, `direction`, `stat` | AlertEvaluator |
| `RecommendationChanged` | simulator (recommend) | `campaign_id`, `old`, `new` (Recommendation) | AlertEvaluator, SSE |
| `AlertRaised` | AlertEvaluator | `alert_id` | SSE |
| `DecisionRecorded` | governance | `decision_id` | SSE, AI bàn giao ca |
| `GoalChanged`*, `ParameterChanged`* | API goals/parameters | `scope`, `ids`, `version` | orchestrator (ưu tiên tối đa cho phạm vi đổi) |
| `StageDone`* | worker | `sku_id` hoặc `campaign_id`, `stage`, `duration_ms` | event_log (đo hiệu năng) |
| `ComputeFailed`* | worker | `target`, `stage`, `error` | AlertEvaluator, SSE |
| `AlertResolved`* | AlertEvaluator | `alert_id` | SSE |
| `LiveTick`* | live watcher | `ngay`, `don_hang_va_ton_den_het`, `ads_den_het` | SSE (UI hiện "dữ liệu tới 14:59") |

### Cài đặt bus

```python
# backend/app/core/events.py  (khung; INF-02 cài đặt)
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
import asyncio, uuid, datetime as dt

@dataclass(frozen=True)
class Event:
    type: str
    payload: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

Handler = Callable[[Event], Awaitable[None]]

class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = {}
        self._queue: asyncio.Queue[Event] = asyncio.Queue()

    def subscribe(self, event_type: str, handler: Handler) -> None: ...
    async def publish(self, event: Event) -> None: ...   # đẩy vào queue, không chờ handler
    async def run(self) -> None: ...                     # vòng lặp nền, gọi handler, bắt lỗi từng handler
```

- Một process, một bus. Không dùng Redis/Kafka trong hackathon.
- Mọi event được ghi vào bảng `event_log` (thêm vào schema WH, xem mục 7) để debug và vẽ timeline trong demo.
- Handler lỗi không làm chết bus: log + phát `ComputeFailed`.

---

## 2. Triage: điểm ưu tiên SKU

Triage nhận `DataUpdated`, xác định SKU bị ảnh hưởng, tính điểm, đẩy vào **hàng đợi ưu tiên** (`asyncio.PriorityQueue`, điểm cao chạy trước).

> **Mức sàn 10 ngày (P0):** chưa cần điểm ưu tiên. Mỗi `DataUpdated`, tính lại mọi chiến dịch có SKU trong `skus_changed` (cỡ demo tối đa 49 chiến dịch × < 1 s mỗi chiến dịch, vẫn dưới mục tiêu 5 phút). Điểm ưu tiên và hàng đợi bên dưới là P1 (ORC-02), làm khi cần cỡ `tai_nang`.

### Công thức

```
priority(sku) = 100 × w_force
              + 40 × stock_risk
              + 25 × spend_share
              + 25 × deviation
              + 10 × staleness
```

| Thành phần | Công thức | Miền | Ý nghĩa |
|---|---|---|---|
| `w_force` | 1 nếu SKU nằm trong phạm vi `GoalChanged/ParameterChanged` hoặc người dùng đang mở SKU đó, 0 nếu không | {0,1} | Việc người dùng vừa làm luôn chạy trước |
| `stock_risk` | `clip(1 − hours_of_cover / 24, 0, 1)`, `hours_of_cover = tồn_khả_dụng / max(p50_dự_báo_giờ_tới, ε)` | [0,1] | Sắp hết hàng trong 24 giờ |
| `spend_share` | chi ads 3 giờ gần nhất của SKU / tổng chi ads shop 3 giờ gần nhất | [0,1] | Đang tốn nhiều tiền |
| `deviation` | `clip(|thực_tế_3h − p50_3h| / max(p90_3h − p10_3h, 1), 0, 2) / 2` | [0,1] | Bán lệch khỏi dự báo |
| `staleness` | `clip(giờ_từ_lần_tính_cuối / 6, 0, 1)` | [0,1] | Lâu chưa tính lại |

Quy tắc phụ:
- SKU combo kéo theo các SKU thành phần (và ngược lại), cùng điểm cao nhất của nhóm.
- SKU cùng chiến dịch được tính chung một lượt simulator vì ngân sách dùng chung (xem 05-kinh-te-va-mo-phong).
- `affected_skus = null` (ví dụ file giá vốn mới) → mọi SKU bị ảnh hưởng nhưng chỉ đẩy top `TRIAGE_TOP_N = 50` ngay; phần còn lại vào hàng "nền" chạy khi rảnh.

### Ví dụ với nhân vật demo lúc 13:00 ngày 11.11

| SKU | stock_risk | spend_share | deviation | Điểm | Thứ tự |
|---|---|---|---|---|---|
| NOICHIEN2L (còn ~40 sp) | 0,9 | 0,22 | 0,3 | 49,0 | 1 |
| AOGIUNHIET-M (vừa về 0) | 1,0 | 0,05 | 0,9 | 63,8 | (cao hơn nếu đang mở) |
| BINHNUOC500-XANH (viral) | 0,4 | 0,08 | 1,0 | 43,0 | 2–3 |
| COCMEME-A | 0 | 0,04 | 0,1 | 3,5 | nền |

Con số trong bảng là minh họa cách tính, không phải đầu ra cam kết.

---

## 3. Tính tăng dần (incremental)

Mỗi bước có **khóa phiên bản đầu vào**. Nếu khóa không đổi thì dùng lại kết quả trong cache.

| Bước | Khóa phiên bản (hash của) | Đầu ra | Thời gian mục tiêu / SKU |
|---|---|---|---|
| `panel` | `sku_id`, max(`ingest_batches.id`) của các nguồn liên quan, `as_of` | dòng `hourly_panel` mới từ mốc cuối | ≤ 50 ms |
| `forecast` | khóa panel + `model_registry.version` của champion + `horizon_h` | `forecasts` (p10/p50/p90 theo giờ, 48 giờ tới) | ≤ 1,5 s (baseline) |
| `simulate` (theo chiến dịch) | `(campaign_id, forecast_ids, estimate_id, param_version, goal_id, as_of)`, đúng khóa cache ở 05-kinh-te-va-mo-phong | `scenarios` + `scenario_results` cho 5 hành động + `recommendations` | ≤ 0,8 s với `samples=1000`; ≤ 0,25 s với `samples=300` |
| `recommend` | khóa simulate | `recommendations` (zone, nhãn tin cậy, lý do) | ≤ 20 ms |

- Panel chỉ **nối thêm** các giờ mới, không dựng lại toàn bộ. Khi nguồn bị sửa ngược (đơn chuyển "Đã hủy"), đánh dấu các giờ bị ảnh hưởng là bẩn và tính lại từ giờ bẩn sớm nhất.
- Forecast baseline fit theo lô hằng đêm (00:30) cho mọi SKU. Trong ngày chỉ **cập nhật mức** (level update) bằng dữ liệu mới, không fit lại cả model (xem 03-lop-mo-hinh).
- Simulator chạy lại khi một phần tử của khóa đổi (`as_of` đổi mỗi tick). Kéo tham số trên UI gọi lại với `samples=300`, thả tay gọi `samples=1000` (05, 09).

### Cache

```python
# backend/app/orchestrator/cache.py
class StageCache:
    """LRU trong bộ nhớ, khóa = (stage, sku_id, input_hash). Kết quả bền nằm trong DuckDB."""
    def get(self, stage: str, sku_id: str, input_hash: str) -> Any | None: ...
    def put(self, stage: str, sku_id: str, input_hash: str, value: Any) -> None: ...
```

- Giới hạn 5.000 mục. Cache bị xóa khi model champion đổi.
- Kết quả cuối (forecast, scenario_results, recommendations) luôn ghi DuckDB kèm `input_hash` để API đọc thẳng.

---

## 4. Trạng thái tính toán và nhãn "đang cập nhật"

Mỗi SKU có một trạng thái trong bảng `sku_compute_state`:

```
fresh ──(DataUpdated chạm SKU)──▶ queued ──▶ computing ──▶ fresh
  ▲                                              │
  └──────────────(lỗi 3 lần)── failed ◀──────────┘
```

| Trạng thái | UI hiện | Có được dùng để quyết định? |
|---|---|---|
| `fresh` | kết quả bình thường, "tính lúc 14:05, dữ liệu tới 13:59" | Có |
| `queued` / `computing` | kết quả cũ mờ 60%, nhãn **"Đang cập nhật · dữ liệu lúc 13:05"** | Có, nhưng nút "Duyệt" hiện cảnh báo |
| `failed` | kết quả cũ + nhãn đỏ **"Không tính lại được, xem chi tiết"** | Không, nút "Duyệt" bị khóa |
| `stale` (fresh nhưng `as_of` cũ hơn ngưỡng của ca) | nhãn vàng **"Dữ liệu cũ hơn 2 giờ"** | Có, kèm cảnh báo |

Ngưỡng `stale` mặc định: đơn hàng 30 phút, tồn 60 phút, ads 3 giờ (vì ads trễ sẵn 2 giờ). Lưu trong `parameters` (scope shop), sửa được.

Quy tắc bắt buộc: **không bao giờ hiện kết quả cũ như kết quả mới**. API luôn trả `computed_at`, `data_as_of`, `compute_state` cùng mọi kết quả (xem 08-api).

---

## 5. Watcher `live_stream`

`replay.js` ghi đè 3 CSV và `trang_thai.json` mỗi giờ mô phỏng (ghi file tạm rồi đổi tên, nên đọc luôn an toàn).

```python
# backend/app/live/watcher.py
class LiveWatcher:
    def __init__(self, root: Path, bus: EventBus, poll_s: float = 2.0): ...
    async def run(self) -> None:
        """Mỗi poll_s giây đọc trang_thai.json; nếu mốc đổi thì nạp tăng dần."""
```

Thuật toán mỗi lần mốc đổi:
1. Đọc `trang_thai.json` → `don_hang_va_ton_den_het` (vd `14:59`), `ads_den_het` (vd `12:59`).
2. Với mỗi CSV: lưu `last_row_count` theo file trong bảng `live_cursor`. Đọc file, **bỏ qua `last_row_count` dòng đầu**, giao phần mới cho `ingest_live_delta` (PNL-04 ở 02-lop-du-lieu), dùng chung adapter với import thường, `batch.source_kind = 'live'`.
   - Nếu số dòng giảm (replay chạy lại từ đầu) → xóa dữ liệu live của ngày đó (`DELETE ... WHERE batch.source_kind='live' AND date=11/11`) và nạp lại từ dòng 0.
3. Tồn kho live là snapshot theo giờ, nạp vào `inventory_snapshots` với `snapshot_at` = cột "Thời điểm cập nhật".
4. Phát `DataUpdated` với `affected_skus` = SKU xuất hiện trong phần mới (đơn + tồn) và SKU của chiến dịch có dòng ads mới.
5. Phát `LiveTick` để UI cập nhật đồng hồ "dữ liệu tới".

Cấu hình (`.env`):

```
LIVE_ENABLED=true
LIVE_ROOT=../mock-data/out/demo/live_stream
LIVE_POLL_SECONDS=2
```

Lưu ý dữ liệu:
- Cột "Thời gian" trong ads là **UTC**. Adapter đã đổi về GMT+7 (02-lop-du-lieu). Watcher không xử lý lại.
- Ads trễ 2 giờ so với đơn. UI và simulator phải dùng `ads_den_het` làm mốc chi ads thật. Giờ sau mốc đó, chi ads được **ước lượng** theo ngân sách và nhịp chi, có cờ `estimated=true`.

---

## 6. Mục tiêu hiệu năng

| Thao tác | Mục tiêu | Đo ở đâu |
|---|---|---|
| Kéo thanh tham số: ZoneBar cập nhật tạm trên trình duyệt (công thức hòa vốn) | < 100 ms | Frontend, `performance.now()` |
| Kéo thanh tham số: gọi lại `POST /scenarios/run` với `samples=300` | < 400 ms p95 | middleware đo thời gian API |
| `POST /scenarios/run` một SKU, 5 hành động | < 1 s p95 | middleware đo thời gian API |
| Từ `LiveTick` đến khi top 50 SKU `fresh` (cỡ demo) | < 5 phút; mục tiêu thật < 60 s | `event_log`: `LiveTick.at` → `StageDone(recommend)` cuối |
| Từ `LiveTick` đến cảnh báo đầu tiên trên UI | < 90 s | `event_log` + SSE timestamp |
| Fit lô hằng đêm cho 300 SKU | < 3 phút | job log |
| Fit lô hằng đêm cho 2.000 SKU (`tai_nang`) | < 15 phút | job log |
| Import lịch sử 120 ngày cỡ demo (~118k dòng đơn) | < 60 s | `ingest_batches.duration_ms` |
| Bộ nhớ process backend, cỡ demo | < 1,5 GB | `psutil` |

Gợi ý kỹ thuật:
- Simulator vector hóa: mảng `(n_samples=1000, horizon_h=48)` cho mỗi hành động, dùng numpy, không vòng for theo mẫu.
- DuckDB: một connection ghi chung, connection đọc riêng cho API. Ghi theo lô bằng `INSERT ... SELECT * FROM df`.
- Worker pool `ORC_WORKERS=4` (asyncio + `run_in_executor` cho phần numpy nặng).

---

## 7. Bảng phụ cần thêm vào schema

Thêm vào schema (D3 tạo cùng ORC-01; `decisions`, `actions`, `alerts` ở 10-quan-tri-va-bao-mat, `ai_sessions`, `ai_tool_calls` ở 07-lop-ai):

```sql
CREATE TABLE event_log (
  id VARCHAR PRIMARY KEY, type VARCHAR, payload JSON, at TIMESTAMPTZ
);
CREATE TABLE sku_compute_state (
  sku_id VARCHAR PRIMARY KEY, state VARCHAR, last_input_hash VARCHAR,
  computed_at TIMESTAMPTZ, data_as_of TIMESTAMPTZ, error VARCHAR, retries INTEGER DEFAULT 0
);
CREATE TABLE live_cursor (
  file VARCHAR PRIMARY KEY, last_row_count BIGINT, last_tick VARCHAR, updated_at TIMESTAMPTZ
);
```

---

## 8. Kiểm tải với cỡ `tai_nang`

```bash
cd mock-data && node generate.js --size tai_nang
cd ../backend && python -m eval.loadtest --size tai_nang --replay-interval 5
```

Script `backend/eval/loadtest.py`:
1. Import lịch sử `tai_nang`, đo thời gian.
2. Chạy fit lô, đo thời gian.
3. Gọi `replay.js --size tai_nang --interval 5` qua `subprocess`, theo dõi `event_log`.
4. Xuất báo cáo `backend/eval/reports/loadtest_<thời gian>.md`: thời gian từng bước, p50/p95 từ `LiveTick` tới top 50 `fresh`, số SKU bị bỏ lại hàng nền, đỉnh RAM.

Đạt khi: top 50 `fresh` < 5 phút ở mọi tick, không có tick nào bị bỏ (backlog hàng đợi về 0 trước tick kế tiếp đối với top 50).

---

## Tiêu chí hoàn thành

- [ ] Bus chạy nền, mọi event ghi `event_log`, handler lỗi không làm dừng bus.
- [ ] Triage tính điểm đúng công thức; unit test với 4 SKU ví dụ cho đúng thứ tự.
- [ ] Combo và SKU cùng chiến dịch được tính cùng lượt.
- [ ] Cache theo `input_hash`; chạy lại cùng dữ liệu không tính lại (test đếm số lần gọi stage).
- [ ] `sku_compute_state` đúng state machine; API trả `compute_state`, `computed_at`, `data_as_of`.
- [ ] Watcher nạp tăng dần, xử lý được replay chạy lại từ đầu, phát `LiveTick`.
- [ ] Đạt bảng hiệu năng ở cỡ demo; có báo cáo loadtest cỡ `tai_nang`.

## Task gợi ý

Người, ước lượng và ưu tiên theo 12-lo-trinh-va-phan-viec.

| Mã | Việc | Người | Người-ngày | Ưu tiên | Phụ thuộc |
|---|---|---|---|---|---|
| ORC-01 | Đăng ký handler trên EventBus, `event_log`, tính lại mọi chiến dịch bị chạm (mức sàn) | D3 | 0,5 | P0 | INF-02, SIM-05 |
| ORC-02 | Triage có điểm ưu tiên + hàng đợi + nhóm combo/chiến dịch | D3 | 0,75 | P1 | ORC-01 |
| ORC-03 | Worker pool, `input_hash`, StageCache | D3 | 0,75 | P1 | ORC-02 |
| ORC-04 | `sku_compute_state`, quy tắc stale, trả qua API | D3 | 0,25 | P0 | ORC-01 |
| ORC-05 | AlertEvaluator (gồm `DriftDetected`), gom nhóm, ngân sách cảnh báo | D3 | 0,5 | P0 | ORC-01 |
| LIVE-01 | LiveWatcher + `live_cursor`, gọi `ingest_live_delta`, phát `LiveTick` | D1 | 0,5 | P0 | PNL-04 |
| LIVE-02 | Ước lượng chi ads cho giờ sau `ads_den_het` (cờ estimated) | D2 | 0,5 | P1 | LIVE-01, ECO-01 |
| EVAL-06 | Loadtest cỡ `tai_nang` + báo cáo | D1 | 0,5 | P1 | LIVE-01 |
