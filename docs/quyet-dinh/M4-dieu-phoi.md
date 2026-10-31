# Nhật ký quyết định · Điều phối + live (M4: LIVE-01, ORC-01, ORC-04, ORC-05, API-07 phần live)

Code: `backend/app/live/` (`replay.py`, `watcher.py`, `control.py`), `backend/app/orchestrator/` (`event_log.py`, `state.py`, `recompute.py`, `alerts_eval.py`, `stream.py`, `runtime.py`), `backend/app/api/live.py`, `backend/app/api/stream.py`. Nối vào `app/main.py` (lifespan), `app/api/router.py` (2 dòng), `app/api/common.py` + `app/api/snapshot.py` (`compute_state`).

Test: `tests/unit/orchestrator/` (máy trạng thái, luật stale, event_log + handler lỗi, SSE hub, bản sao replay, ngân sách cảnh báo), `tests/integration/test_live_orchestrator.py` (replay `nho` qua đường thật, chạy lại từ đầu), `tests/api/test_live_stream.py`, `tests/e2e/test_live_alerts_demo.py` (`demo`).

## 1. Luồng chạy trong process FastAPI

```
LiveWatcher (poll trang_thai.json 2 s, hoặc poke)
  └─ thread + khóa ghi: ingest_live_delta(live_dir)  → DataUpdated, LiveTick
EventBus
  ├─ "*" → EventLogSink (event_log)      ├─ "*" → StreamHub (SSE /stream)
  ├─ DataUpdated / GoalChanged / ParameterChanged → Orchestrator.request (SKU → queued)
  │     worker (1 task, gộp các yêu cầu đang chờ) → thread + khóa ghi:
  │       chiến dịch bị chạm → dự báo gốc của ngày → drift (CUSUM) → dự báo tại giờ kế
  │       → run_simulation cho chiến dịch có mục tiêu → sku_compute_state fresh/failed
  │       → AlertEvaluator.evaluate(as_of)
  │     → ForecastUpdated, StageDone, DriftDetected, RecommendationChanged, ComputeFailed,
  │       AlertRaised/AlertResolved
  └─ DriftDetected / RecommendationChanged / ComputeFailed → AlertEvaluator.handle
```

`Runtime` (`orchestrator/runtime.py`) dựng tất cả trên bus của app. `event_log` và SSE hub luôn bật. Orchestrator, AlertEvaluator và watcher bật khi `APP_ENV != test` (watcher cần thêm `LIVE_ENABLED`).

## 2. Quyết định

### Q1. Tắt orchestrator khi `APP_ENV=test`
Các test API hiện có upload file và đổi mục tiêu, nên phát `DataUpdated`/`GoalChanged`. Nếu orchestrator chạy nền, nó sẽ ghi DuckDB song song với test, gây chậm và dễ lỗi ngẫu nhiên. Vì vậy ở môi trường test chỉ bật `event_log` + SSE. Test của phần này tự bật bằng `Runtime(..., compute=True)`.

### Q2. Một khóa ghi cho cả đường live, worker gộp yêu cầu
Watcher, recompute và AlertEvaluator dùng chung một `threading.Lock` và chạy trong thread (`asyncio.to_thread`), nên event loop (SSE, API) không bị chặn. Handler `DataUpdated` chỉ đánh dấu `queued` rồi trả về ngay. Một worker duy nhất lấy **toàn bộ** SKU đang chờ trong một lượt. Nếu replay chạy nhanh hơn tốc độ tính, các tick bị gộp lại chứ không xếp hàng dài. Simulator dùng chung `_RUN_LOCK` với `POST /scenarios/run` (cùng cache, không xung đột ghi `recommendations`).

### Q3. Phạm vi tính lại (mức sàn 06 §2)
Mỗi `DataUpdated` tính lại mọi chiến dịch có SKU trong `skus_changed`, cùng mọi SKU của các chiến dịch đó (gồm cả thành phần combo, vì ngân sách và tồn dùng chung). `skus_changed` rỗng nghĩa là tính tất cả. `GoalChanged`/`ParameterChanged` có scope `campaign` thì tính các SKU của chiến dịch đó, scope `sku` thì SKU đó, scope `shop` thì tính tất cả. Chưa làm điểm ưu tiên và hàng đợi ưu tiên (ORC-02, P1).

### Q4. Chiến dịch không có mục tiêu: chỉ dự báo, không mô phỏng
`run_simulation` cần `goal_id`, và AI/orchestrator không được tự tạo mục tiêu (05 §4). Chiến dịch chưa có mục tiêu vẫn được dự báo lại và vẫn có cảnh báo, nhưng không có đề xuất mới.

### Q5. Dự báo gốc của ngày + drift
- **Dự báo gốc** = champion tại 00:00 giờ VN của ngày dữ liệu, 24 giờ, tính một lần, lưu `forecasts` rồi dùng lại cho mọi tick. Cả CUSUM (03 §9) và luật "3 giờ lệch dự báo" đều so với dự báo này. Lý do: dự báo tại giờ kế tiếp không có giờ nào đã qua để so.
- Mỗi tick: `scan_drift(matrix, dự_báo_gốc, as_of)`. `DriftDetected` chỉ phát **một lần** cho mỗi (ngày, SKU, hướng). SKU có `up` dùng `nowcast` trong 24 giờ (`active_up`), như M2 đề xuất. Khi replay chạy lại từ đầu thì xóa bộ nhớ drift.
- Dự báo tại giờ kế (`start` của simulator) dài 72 giờ (= `MAX_HORIZON`), lưu `forecasts`, rồi đưa thẳng vào `run_simulation(forecasts=...)`. Nhờ vậy SKU drift chạy mô phỏng bằng nowcast.

### Q6. `sku_compute_state` và quy tắc stale (ORC-04)
- Trạng thái lưu: `queued` (vừa có dữ liệu chạm), `computing`, `fresh` (kèm `data_as_of`, `computed_at`, `last_input_hash`), `failed` (sau 3 lần lỗi; hai lần đầu quay lại `queued` để tick sau thử lại).
- `stale` không lưu mà suy ra lúc đọc, ở `orchestrator.state.derive_state`, được `api.common.compute_state` gọi lại:
  - Trạng thái `queued`/`computing`/`failed` đã lưu thì dùng luôn.
  - Nếu không, kết quả "mới tới" mốc muộn hơn trong hai mốc: `data_as_of` của chính kết quả, và `data_as_of` lưu khi orchestrator đánh dấu fresh.
  - Kết quả là `stale` khi đồng hồ dữ liệu đã đi quá mốc đó **hơn 30 phút** (ngưỡng nhỏ nhất của 06 §4: đơn hàng). Trước đây chỉ cần dữ liệu mới hơn là thành `stale`. Với tick theo giờ, hai cách cho cùng kết quả.
- `snapshot.sku_rows` đọc thêm `sku_compute_state.data_as_of`.

### Q7. Luật cảnh báo cụ thể (ORC-05, 09 §2.5)
| kind | Điều kiện | Mức | Khóa dedupe |
|---|---|---|---|
| `stockout_risk` (out) | SKU trong chiến dịch mà giờ ads **mới nhất đã biết** là `running`, tồn giờ cuối = 0, **và đầu ngày còn hàng** | cao | `stockout_risk:sku:<sku>:out` |
| `stockout_risk` (low) | như trên, tồn > 0, `hours_of_cover.p50 < 6` (cum_p50 của dự báo tại giờ kế) | cao | `…:<sku>:low` |
| `sales_above_forecast` | theo **sản phẩm**, ≥ 10 sp trong 3 giờ và: Σ bán > 1,5 × Σ p90 và ≥ 2 × Σ p50 (luật chặt), **hoặc** trong ngày đã có CUSUM `up` cho sản phẩm và Σ bán > Σ p90, ≥ 1,5 × Σ p50 (luật nhẹ có drift xác nhận) | cao nếu có biến thể cover < 24 giờ, còn lại TB | `sales_above_forecast:product:<pid>:<ngày>` |
| `sales_below_forecast` | theo sản phẩm, chỉ biến thể không hết hàng trong 3 giờ: Σ bán < Σ p10, Σ p10 ≥ 3 | cao nếu ảnh hưởng > 1 tr | `…:product:<pid>:<ngày>` |
| `DriftDetected` | **không tự bật cảnh báo**: gắn vào cảnh báo bán lệch cùng sản phẩm/hướng/ngày (`detail.drift`), hoặc nhớ lại để xác nhận luật nhẹ ở trên. Orchestrator đưa alarm của tick vào `evaluate` nên xác nhận có hiệu lực ngay trong tick đó | — | như trên |
| `budget_exhausted` | giờ ads mới nhất "Hết ngân sách" trước 20:00 | TB | `…:campaign:<cid>:<ngày>` |
| `data_stale` | `freshness()` của orders/inventory/ads vượt ngưỡng (demo dùng đồng hồ dữ liệu) | TB | `data_stale:source:<src>` |
| `data_blocking` | issue `error` đang mở trên SKU có ads chạy, **gộp theo mã issue** (vd "Thiếu giá vốn cho 21 SKU đang chạy quảng cáo") | cao | `data_blocking:code:<code>` |
| `recommendation_changed` | `RecommendationChanged` có `old` (hành động tốt nhất hoặc trạng thái đổi) | TB | `…:campaign:<cid>` |
| `compute_failed` | `ComputeFailed` (trừ lỗi của chính evaluator) | cao | `compute_failed:<target>:<stage>` |

Chọn như vậy vì những lý do sau:
- **"Đầu ngày còn hàng"**: lần chạy đầu trên `nho` có 6 SKU của CD007 hết hàng từ nhiều tuần trước và bị báo "hết hàng" ngay 00:59. Đó không phải tin mới, và chiến dịch nhiều SKU vẫn chạy được nhờ các SKU còn lại.
- **Gộp theo sản phẩm + ngưỡng**: luật theo từng SKU với ngưỡng 5 sp báo cả `AOGIUNHIET-M` và `GIAYLOT50` lúc 02:59 (đêm, p90 nhỏ). Đó chỉ là nhiễu lúc mở màn sale. Trên `demo` (300 SKU), luật "Σ bán > Σ p90" bật 11 sản phẩm trong ngày, vì dự báo ngày đôi vốn thấp (M2: champion ngày đôi trượt). CUSUM một mình bật thêm 10 sản phẩm. Hai luật đi đôi (chặt, hoặc nhẹ + drift) giữ lại bình nước và máy hút bụi.
- **Gộp `data_blocking` theo mã**: `demo` cố ý thiếu giá vốn cho khoảng 20 SKU. Báo từng SKU thì 20 cảnh báo mức cao chiếm hết ngân sách 8 chỗ, làm cảnh báo bình nước bị ẩn.
- **Giờ ads mới nhất đã biết**: ads trễ 2 giờ, nên "ads còn chạy" dựa trên giờ ads cuối cùng có số liệu.
- Ảnh hưởng (`impact_vnd`): với hết hàng là chi ads/giờ (trung bình 3 giờ ads gần nhất) × số giờ còn lại trong ngày; với bán lệch là phần chênh so với p90/p10 × giá niêm yết. Cách tính ghi ở `detail.impact_basis`.

### Q8. Chống spam
- Dedupe dùng `create_alert` của governance. Gặp lại cùng khóa thì cập nhật tại chỗ (`occurrences` +1), không phát `AlertRaised`.
- Cảnh báo bán lệch đặt khóa theo sản phẩm và ngày, không tự đóng trong ngày (tránh bật/tắt liên tục). Hết ngày dữ liệu thì đóng.
- Cảnh báo tồn, ads, dữ liệu tự đóng khi điều kiện hết (`resolve_by_key`). Ví dụ CD005 hết ngân sách lúc 21:00 thì cảnh báo "áo M hết hàng nhưng ads vẫn chạy" được đóng.
- Lớp bọc `_upsert` thêm ba điều:
  - không bao giờ hạ mức;
  - gộp `detail.skus`/`detail.drift` với bản đã lưu, giữ `first_data_hour`;
  - cảnh báo đang `hidden_by_budget` thì để yên. `create_alert` chỉ dedupe các trạng thái open/ack/snoozed, nên nếu không chặn thì mỗi tick sẽ chèn thêm một dòng mới.
- **Ngân sách**: tối đa 8 cảnh báo `open`, xếp theo mức → ảnh hưởng → cũ trước. Phần còn lại là `hidden_by_budget`, và được mở lại khi có chỗ trống. Chỉ phát `AlertRaised` cho cảnh báo đang hiện. "Một ca" ở mức sàn là tập cảnh báo đang mở của shop: khóa theo ngày và việc đóng khi hết ngày giữ nó trong ngày dữ liệu.
- **Replay chạy lại từ đầu** (watcher thấy `restarted`): đóng mọi cảnh báo hệ thống của lần chạy cũ (`note = "Phát lại dữ liệu từ đầu"`), xóa bộ nhớ drift, đưa `failed` về `fresh`.

### Q9. Giờ trên cảnh báo
`raised_at` dùng **giờ thật**, vì snooze/ack của governance cũng dùng giờ thật. Ngày demo 11.11 nằm sau giờ thật, nên nếu dùng đồng hồ dữ liệu thì cảnh báo vừa snooze sẽ mở lại ngay. Giờ của dữ liệu đã bật cảnh báo nằm ở `detail.first_data_hour` (lần đầu) và `detail.data_hour` (lần cập nhật cuối), ví dụ "13:59" nghĩa là dữ liệu tới 13:59.

### Q10. Live: dùng bản Python của `replay.js`, thêm lệnh "nhảy tới giờ H"
- `app/live/replay.py` ghi đúng byte như `replay.js` (test so với `eval/live_replay.py`). Nút demo không cần Node. `POST /live/control` không gọi `node` qua subprocess như 08 §3.13. Người demo vẫn chạy được `node replay.js` bằng tay, watcher không phân biệt hai nguồn.
- Nguồn replay là `DATA_DIR/live_11_11/_replay.json` (dữ liệu đầu vào của replay, không phải đáp án). Thư mục ghi là `LIVE_DIR`.
- Lệnh: `start {from_hour, interval_seconds}`, `stop`, `reset` (xóa mọi lô live, về lịch sử), `jump {hour, wait}` (thêm so với 08). `jump` ghi thư mục ở mốc H rồi nạp ngay. Nhảy lùi được xử lý như replay chạy lại từ đầu. Với `wait=true`, API chờ tính xong và có cảnh báo rồi mới trả lời.
- `/live/control` cần quyền `demo.control` (lead) và `DEMO_MODE=true` (nếu không thì 409 `STATE_CONFLICT`). Mỗi lệnh ghi một dòng `audit_log` (`live.<command>`).

### Q11. SSE `/stream`
- Vòng đệm 500 sự kiện, `id` tăng dần. Hỗ trợ header `Last-Event-ID` hoặc `?last_event_id=` (cho EventSource không gửi được header). Heartbeat 15 giây. `?limit=`/`?idle_timeout=` để đóng sớm (curl, test).
- Ánh xạ: `LiveTick` → `live.tick {day, orders_until "14:59", ads_until, *_at}`, `DataUpdated` → `ingest.batch_loaded`, `RecommendationChanged` → `recommendation.changed`, `AlertRaised/Resolved` → `alert.raised/resolved` (bản ghi alert đầy đủ, giờ `+07:00`), `DecisionRecorded` → `decision.changed {id, state}`, `ComputeFailed` → `compute.failed`.
- `compute.state` gửi **theo lô**: `{skus: [...], state, computed_at}`, không gửi một event cho mỗi SKU như 08 §4. Một tick trên demo chạm khoảng 200 SKU.
- `freshness.changed` và `alert.investigated` chưa phát (cần nguồn từ governance/AI).

## 3. Kết quả đo (máy đang chạy song song agent khác)

Replay cả ngày 11.11 (24 tick, `jump` từng giờ, đường thật: watcher → orchestrator → AlertEvaluator). Giờ ghi là `detail.first_data_hour`, tức "dữ liệu tới".

| | `nho` (30 SKU) | `demo` (300 SKU) |
|---|---|---|
| Bình nước `BINHNUOC500` bán vọt | `sales_above_forecast` lúc **10:59** (CUSUM `up` cả 3 màu lúc 09:59; luật nhẹ đạt ở tick sau) | **10:59** (CUSUM `up` 10:59), mức cao |
| Áo giữ nhiệt `AOGIUNHIET-M` | "sắp hết" 10:59 (tồn 9 → 1), **"đã hết hàng nhưng CD005 vẫn chạy" 13:59**, tự đóng khi CD005 hết ngân sách 21:00 | sắp hết 10:59, **hết hàng 13:59** |
| Nồi chiên `NOICHIEN2L` | sắp hết 15:59, hết 19:59 | sắp hết 08:59, hết 16:59 |
| Tổng cảnh báo trong ngày | 10 (không khóa nào bị lặp) | 33 (sales_above 16, stockout 15, data 2), cuối ngày ≤ 8 đang mở |
| Thời gian một tick (LiveTick → StageDone) | ~6 s | 15–35 s, có tick 80 s khi máy bị agent khác chiếm CPU (mục tiêu 06: < 5 phút) |

Test giữ các mốc trên: `tests/integration/test_live_orchestrator.py` (`nho`, 6 tick + chạy lại từ đầu, ~25–140 s tùy tải), `tests/e2e/test_live_alerts_demo.py` (`demo`, 4 tick, ~3,5 phút). Hiệu năng `test_demo_tick_to_fresh_under_5_minutes` gắn `@pytest.mark.perf`. CD002 11/11 01:00 profit vẫn ra `schedule_hours:0-2,12-14` (`eval.demo_whatif --only 1`).

Lần đầu trên `demo` có 67 cảnh báo: 20 `data_blocking` (mỗi SKU thiếu giá vốn một cái), 10 cảnh báo chỉ do CUSUM, các luật theo SKU. Sau Q7/Q8 còn 33 cảnh báo.

## 4. Ghi chú hợp nhất
- `app/governance/alerts.create_alert` chỉ dedupe trạng thái `open/acknowledged/snoozed`. Đề nghị coi cả `hidden_by_budget` là "đang sống" khi dedupe. Hiện `AlertEvaluator._upsert` tự kiểm trước khi gọi.
- `Orchestrator._simulate` import `_RUN_LOCK` từ `app/api/scenarios.py`. Nên chuyển khóa này sang `app/simulator/service.py`.
