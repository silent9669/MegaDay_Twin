# Nhật ký quyết định · API đợt M3 (API-01, API-03, API-04, API-05, ECO-02, GOV-02)

Code: `backend/app/api/` gồm `deps.py` (X-User/X-Role, `require_role`, cursor DB, `publish`, `audit`), `common.py` (đồng hồ dữ liệu, freshness, `compute_state`, phân trang), `snapshot.py` (dữ liệu màn Tổng quan), `system.py`, `skus.py`, `campaigns.py`, `economics.py` (tham số, mục tiêu), `scenarios.py` (mô phỏng, hòa vốn, đề xuất), `ingest.py` (nhập dữ liệu, chất lượng). Đăng ký ở `router.py`. Test nằm ở `backend/tests/api/`, với `test_cd002_1111.py` là e2e trên `demo`. Thêm `tests/unit/simulator/test_zone_inputs.py`.

Mỗi mục ghi vấn đề, lựa chọn, lý do và ảnh hưởng. Ví dụ phản hồi thật ở cuối file.

## 1. Endpoint đã làm

Base URL: `http://localhost:8000/api/v1`. Vite proxy `/api` → `localhost:8000`. Mọi endpoint (trừ `/health`) cần header `X-User`.

| Method | Path | Vai trò tối thiểu | Chạy với dữ liệu thật (demo) |
|---|---|---|---|
| GET | `/health` | không | ✓ |
| GET | `/me` | viewer | ✓ |
| GET | `/freshness[?now=]` | viewer | ✓ |
| GET | `/skus?sort=priority\|sku\|cover\|sold_today&group=&campaign_id=&has_alert=&has_recommendation=&q=&include_bundles=&limit=&cursor=` | viewer | ✓ |
| GET | `/skus/{sku}` | viewer | ✓ |
| GET | `/skus/{sku}/panel?start=&end=` (hoặc `from=&to=`)`&grain=hour\|day` | viewer | ✓ |
| GET | `/skus/{sku}/forecast?horizon_hours=24&compute=true` | viewer | ✓ |
| PUT, PATCH | `/skus/{sku}/demand-group` | operator | ✓ (test) |
| GET | `/forecast/scores` (= `/models/scores` của 08)`?group=&window=&champion_only=` | viewer | ✓ |
| GET | `/campaigns?sku=&limit=&cursor=`, `/campaigns/{id}`, `/campaigns/{id}/iroas` | viewer | ✓ |
| GET | `/parameters?scope=&scope_id=` (hoặc `sku=`, `campaign_id=`, `session_id=`)`&key=` | viewer | ✓ |
| PUT | `/parameters/{key}` | operator; khóa `editable_by=lead` cần lead | ✓ (test) |
| POST | `/parameters/{key}/confirm` | operator (và phải có quyền sửa khóa đó) | ✓ (test) |
| GET | `/goals?scope=&scope_id=&active=`, `/goals/{goal_id}` | viewer | ✓ |
| POST | `/goals`; PUT `/goals/{scope}/{scope_id}` (dạng của 08) | lead | ✓ |
| POST | `/scenarios/run` | operator | ✓ |
| GET | `/scenarios/{scenario_id}` | viewer | ✓ |
| POST | `/scenarios/breakeven` | operator | ✓ |
| GET | `/recommendations?campaign_id=&sku=&active=true&status=`, `/recommendations/{id}` | viewer | ✓ |
| POST | `/ingest/files` (multipart, trường `files[]` hoặc `files`, `?force=`) | operator | ✓ (test, nho) |
| GET | `/ingest/batches`, `/ingest/batches/{batch_id}` | viewer | ✓ |
| POST | `/ingest/batches/{batch_id}/confirm` | operator | ✓ (test) |
| GET | `/ingest/sku-queue?status=review\|auto\|decided\|all&source_type=` | operator | ✓ |
| POST | `/ingest/sku-queue/{alias_id}` `{"sku": "..."}` hoặc `{"action": "ignore"}` | operator | ✓ (test) |
| GET | `/data-quality/issues?status=open&sku=&severity=blocking\|warning\|info&code=&batch_id=` và `/data-quality` (tóm tắt) | viewer | ✓ |

Chưa làm (M4): decisions, actions, alerts, AI, SSE `/stream`, live, `/bundles`, `/ingest/batches/{id}/mapping`, `/ingest/demo-seed`, `/models/registry`.

## 2. Web cần biết để nối

- **Header:** `X-User: u-ops-1` và `X-Role: viewer|operator|lead`. Thiếu `X-User` trả 401 `UNAUTHENTICATED`. Thiếu `X-Role` thì tính là `viewer`. `X-Role` lạ trả 401. Vai trò không đủ trả 403 `FORBIDDEN`, `details = {required, role}`.
- **Khung phản hồi:** `{data, meta}`. Danh sách là `{data: [...], meta, next_cursor}`, với `next_cursor` ở cấp ngoài cùng như 08. `meta` luôn có `data_as_of`, `computed_at`, `compute_state`, `confidence`, `sources[]`, `cache_hit` (null nếu không áp dụng). Mọi thời điểm có offset `+07:00`.
- **`Source`** (`meta.sources[]`, `reason_sources[]`): `{id, kind, label, data_as_of, tool, href}`. `kind` ∈ `scenario | forecast | iroas | commitment | contribution | snapshot | panel`. `href` là đường API (`/api/v1/...`), không phải route web.
- **Tên trường khác plan / thêm vào:**
  - `/skus`: `hours_of_cover = {p10, p50, p90, horizon_hours}`. `p10` là trường hợp bán nhanh. `null` nghĩa là chưa hết hàng trong horizon. Thêm `sold_3h_units`, `forecast_id`, `confidence`, `product_id`, `is_bundle`. `campaign = {id, status (tiếng Việt), status_code}`. `recommendation = {recommendation_id, status, best, confidence, scenario_id, created_at}`. `deviation` = `on_track | below_forecast | above_forecast | null` (null khi dự báo không bắt đầu trong phần dữ liệu đã có). `meta.total` = số dòng khớp bộ lọc trên mọi trang (§6 S5).
  - `/skus/{sku}`: thêm các trường của `get_sku_snapshot`. Nhóm nhu cầu nằm ở `demand_group_info = {value, source, confirmed}`, còn `demand_group` ở cấp trên vẫn là chuỗi như ở danh sách. Chiến dịch chi tiết ở `campaign_detail` (`shared_with`, `daily_budget_vnd`). Thêm `cost`, `contribution_per_unit_vnd`, `incoming`, `commitments[].windows_local`.
  - `/campaigns/{id}`: `price_max_vnd` là max của thanh trượt V, bằng giá bán cao nhất của các SKU không phải combo (05 §3: V ≤ giá bán). Thêm `sku_detail[]` (giá niêm yết, giá hiệu lực, tồn, giá vốn), `goal` (mục tiêu đang hiệu lực, có `label`), `commitments`, `iroas` tóm tắt, `spend_today_vnd`, `budget_used_pct`.
  - `/campaigns/{id}/iroas`: `iroas = {p10, p50, p90, mean}`. **`p50` = trung bình hậu nghiệm**, vì bảng `iroas_estimates` chỉ lưu mean, p10, p90. Thay `n_switch_events` bằng `n_clean_off_hours` và `excluded_hours`. `method` = `model_id`.
  - `/skus/{sku}/forecast`: `hourly[] = {t, mean, p10, p50, p90}` tính từ giờ dữ liệu hiện tại. `total` chỉ có khi dự báo bắt đầu đúng giờ đó (khi đó tổng tích lũy mới đúng). `score = {wape, coverage_80, bias, window (= backtest), n_points (= SKU-ngày)}`.
  - `/parameters`: mỗi khóa trong danh mục một dòng. `value` là giá trị hiệu lực cho phạm vi được hỏi. `scope` là nơi lấy giá trị (`default | shop | sku | campaign | session`). Thêm `editable_by` (vai trò thấp nhất được sửa), `can_edit` (cho người gọi), `stored[]` (mọi giá trị đã đặt), `pending_suggestions[]` (gợi ý AI chưa xác nhận), `max_rule`. Với `leftover_value_per_unit` khi hỏi theo SKU: `value = default = 0,5 × giá vốn` và `max = giá bán`.
  - `/goals`: thêm `label` ("Lợi nhuận tới 00:00 12/11") cho tiêu đề màn So sánh. Body nhận cả `target_units` lẫn `constraints.target_units` (dạng của 08). Các khóa khác trong `constraints`, cùng `reason`, được lưu vào `goals.params`.
  - `/scenarios/run` → `ScenarioRun = {scenario_id, request, results[], recommendation, cache_hit, input_hash, blocked, timings_ms, requested_by, created_at}`. `request` là `SimRequest` đã chạy, với `start` và `horizon_hours` đã được điền. Gửi lại đúng `request` thì trúng cache. Mỗi `results[]` có thêm `key` (`schedule_hours:0-2,12-14`) và `label` tiếng Việt. `spend`, `units_sold`, `leftover_units` là `Dist {mean,p10,p50,p90}` **cho mọi hành động**, kể cả hành động không khả thi.
  - `recommendation`: thêm `best_label`, `runner_up_label`, `reason_sources[]` (cùng thứ tự `reasons`, có thể `null`), `active`. `zone_bar.inputs` = body của `POST /scenarios/breakeven` (xem Q5).
  - `/scenarios/breakeven`: thêm `pi_continue_vnd`, `status` (`ok | hold_better | pause_sure | continue_sure`), `q_star_units`, `reason`. `zone_bar.inputs` = chính body gửi lên. Body nhận thêm `alt_leftover_values` (mặc định `[20000]`).
  - `/ingest/files`: `status` của batch là `loaded | needs_sku_review | skipped | failed | unsupported`. Plan mô tả cả chuỗi `uploaded → needs_mapping → …`, nhưng chưa có bước AI ghép cột (M4).
  - `/data-quality/issues`: `kind = code`, `severity` đã đổi sang tên API (`blocking | warning | info`), có `message` tiếng Việt.
- **`compute_state`** (06 §4): nếu `sku_compute_state` ghi `queued | computing | failed` thì trả trạng thái đó. Nếu không, kết quả có `data_as_of` cũ hơn đồng hồ dữ liệu hiện tại là `stale`, còn lại là `fresh`. Chưa có orchestrator nên hiện chỉ gặp `fresh`, `stale` hoặc `null` (chưa tính).
- **Thanh trượt:** kéo thì gửi `samples: 300`, thả thì gửi `samples: 1000`, kèm `param_overrides` (`leftover_value_per_unit`, `restock_delay_days` nhận −7..7, `key@SKU` cho riêng một SKU). Giá trị dư lớn hơn giá bán trả 400 `PARAM_OUT_OF_RANGE`, `details.max` = giá bán. Nhờ đó web giới hạn được thanh trượt V ở `price_max_vnd`.

## 3. Quyết định

### Q1. Đồng hồ dữ liệu thay cho đồng hồ tường khi `DEMO_MODE`
- **Vấn đề:** dữ liệu mock là ngày 11.11, trong khi đồng hồ máy là 30/10. Tính độ trễ theo giờ thật thì ra số âm hoặc vô nghĩa.
- **Lựa chọn:** `/freshness` lấy "now" = `max(as_of)` của các batch orders, inventory, ads khi `DEMO_MODE=true` (mặc định), và ghi rõ `clock: "data"`. Truyền `?now=` thì so với mốc đó (`clock: "query"`). Khi tắt demo thì dùng giờ thật (`clock: "wall"`).
- **Lý do:** đúng tinh thần 06 §4. Trễ ads 2–3 giờ hiện ra đúng như trong live.
- **Ảnh hưởng:** `meta.data_as_of` = `max(as_of)` của orders/inventory, cùng mốc mà simulator dùng (`simulator.inputs.data_as_of`): `as_of` 01:00 nghĩa là có dữ liệu tới 00:59. "Hôm nay" trên `/skus` là ngày VN của giờ dữ liệu cuối cùng.

### Q2. Ngưỡng stale cố định trong code
Đơn hàng 30 phút, tồn 60 phút, ads 180 phút (06 §4). Plan ghi là lưu trong `parameters`, nhưng danh mục ECO-02 chưa có các khóa này. Không thêm khóa vào `CATALOGUE` để tránh đụng contract kinh tế. Hằng số nằm ở `common.STALE_MINUTES`. Giá vốn `partial` khi còn issue `MISSING_COST` mở, kèm `missing_skus`.

### Q3. `X-Role` thiếu → viewer, lạ → 401
Theo nguyên tắc quyền tối thiểu: không gửi vai trò thì chỉ được đọc. Gửi vai trò lạ nghĩa là client cấu hình sai nên trả 401, không ngầm hạ xuống viewer. Quyền sửa tham số kiểm hai lớp: `Operator` (viewer luôn bị 403), rồi `can_edit(key, role)` theo `editable_by`. Ví dụ operator sửa `platform_fee_rate` bị 403.

### Q4. Mỗi request một cursor DuckDB; mô phỏng và nhập dữ liệu chạy tuần tự
Endpoint là hàm sync (threadpool của FastAPI). Mỗi request dùng một `db.reader()` riêng. `run_simulation` nằm sau một khóa của process vì lần chạy đầu ở một mốc mới phải dự báo (10–25 giây), và tránh hai request cùng dự báo trùng nhau. Ingest cũng có khóa riêng. Sự kiện (`ParameterChanged`, `GoalChanged`, `RecommendationChanged`, `DataUpdated`) được đưa lên bus bằng `anyio.from_thread.run_sync`, an toàn với event loop.

### Q5. `zone_bar.inputs` chỉ có khi tái tạo được đúng ngưỡng
- **Vấn đề:** web (M3-web §2) cần n, H, m, Π_c để vẽ lại ZoneBar bằng `breakeven.ts`. Thanh vùng của simulator (05 §7.2) lại tính từ mô phỏng chứ không theo ca bán đều.
- **Lựa chọn:** thêm `BreakevenRequest` (pydantic, cũng là body của `POST /scenarios/breakeven`) vào `economics/breakeven.py`, và trường tùy chọn `ZoneBar.inputs` (`[DEV]`). Hàm `recommend.uniform_inputs` dựng ca bán đều tương đương: `n` và `H` của thanh, `m̄`, `V̄`, continue bán `sold_c/H` mỗi giờ, chi `s` mỗi giờ với `s` chọn sao cho `Π_c` khớp mô phỏng. Hàm chạy lại `uniform_breakeven` và **chỉ gắn `inputs` khi ra đúng `threshold`**. Không gắn khi pause vẫn chi tiền hoặc khi `s < 0`.
- **Lý do:** không bịa số (cùng lập luận M3-web §2). Ngưỡng phụ "hàng dư 0đ" của ca bán đều có thể lệch nhẹ so với ngưỡng phụ của simulator, vì giá trị hàng dư mô phỏng là kỳ vọng theo từng mẫu.
- **Ảnh hưởng:** demo 11/11 00:00 có `inputs` cho CD003, CD004, CD005. CD002 không có thanh vùng (V mặc định > m, M3-mo-phong Q6), nên cũng không có `inputs`.

### Q6. `reason_sources` suy từ câu chữ của `simulator.recommend`
`reasons` vẫn là chuỗi trần (không đổi schema simulator). API gán nguồn theo nội dung câu:
- có "cam kết" → `commitment` (`/campaigns/{id}`);
- có tên SKU kèm "hết"/"nhu cầu" → `forecast` của SKU đó;
- "Dự báo …" → `forecast`;
- có tên SKU kèm "lãi" → `contribution` (`/skus/{sku}`);
- có "ads"/"iROAS" → `iroas`;
- còn lại → `scenario` của lần chạy.

Mọi nguồn đều mang `data_as_of` của `inputs_ref`. Nếu sau này simulator đổi câu chữ thì cùng sửa `scenarios.reason_sources` (có test e2e kiểm có `commitment` và `forecast`).

### Q7. Hợp đồng `/scenarios/run`
- `goal_id` không bắt buộc. Thiếu thì lấy mục tiêu đang hiệu lực của chiến dịch. Không có mục tiêu thì trả 422 `BLOCKING_DATA_ISSUE` với `details.missing = "goal"`. API không tự tạo mục tiêu vì mục tiêu phải do người chốt (10 §1).
- Thiếu giá vốn hoặc giá bán (`SimOutput.blocked`) trả 422 `BLOCKING_DATA_ISSUE`, `details = {reasons, recommendation}`, đúng bảng mã lỗi của 08. Ví dụ thật: CD010.
- **"Tắt riêng SKU"**: thêm trường `disable_skus` vào body. Chiến dịch dùng ngân sách chung trả 409 `CONSTRAINT_VIOLATION` (`details.constraint = "sku_level_control"`, lời nhắn gợi ý tách chiến dịch). Chiến dịch 1 SKU trả 400 kèm gợi ý dùng hành động `pause`. SKU không thuộc chiến dịch trả 400.
- `leftover_value_per_unit` không có max cố định. Simulator chỉ chặn dạng `key@SKU`, nên API chặn thêm dạng không có SKU bằng giá bán cao nhất của chiến dịch.
- Trúng cache thì `meta.cache_hit = true`, không ghi DB, không ghi audit.
- `start` nhận `+07:00` hoặc `Z`. Khóa cache của simulator chuẩn hóa `start` về UTC (`service._key`), nên API chuyển nguyên `start` cho simulator. Trước đây API tự đổi, nay đã bỏ (xem §6 S4).
- 409 "tắt CD002 trong 12:00–14:00" của 08 thuộc `POST /decisions/{id}/actions` (M4). Ở mô phỏng, hành động vi phạm vẫn được tính và trả `feasible: false` + `violations`, để màn So sánh hiện lý do bị khóa.

### Q8. `GET /scenarios/{id}` đọc từ bộ nhớ, thiếu thì đọc DB
API giữ 64 `ScenarioRun` gần nhất trong bộ nhớ (đủ trường). Ngoài số đó thì dựng lại từ `scenarios` + `scenario_results` + `recommendations`. Bảng không lưu `lost_units`, `violation_details`, `goal_eligible`, `duplicate_of` nên các trường này là `null` hoặc mặc định. `request` đọc từ cột `scenarios.request`: đúng request đã chạy, `actions` vẫn `null` nếu dùng mặc định (§6 S2). Chỉ dòng ghi trước migration 3 mới dựng lại từ các cột, khi đó `request.actions` là danh sách hành động đã chạy.

### Q9. Mục tiêu có phiên bản
`POST /goals` (hoặc `PUT /goals/{scope}/{scope_id}`) tạo dòng mới với `confirmed_by` = `X-User`. Mục tiêu đang hiệu lực cùng phạm vi chuyển `active = false` và nhận `valid_to`. `version` = max cũ + 1 (10 §4). `create_goal` của ECO-03 luôn ghi `version = 1`, nên API cập nhật lại sau khi tạo.

### Q10. API-03 nạp trực tiếp, chưa có bước ghép cột
- File tải lên được lưu ở `<thư mục DuckDB>/uploads/<batch_id>/` rồi đi qua `ingest_files` thật.
- Batch có dòng bị giữ (alias đang chờ) mang `status = needs_sku_review`. Người dùng chốt alias bằng `POST /ingest/sku-queue/{alias_id}`, sau đó `POST /ingest/batches/{batch_id}/confirm` nạp lại file gốc với `force`. File gốc tìm ở thư mục upload hoặc `DATA_DIR`, và phải khớp SHA-256.
- `sku-queue` chỉ có một ứng viên (gợi ý đã lưu), vì `sku_aliases` không lưu danh sách ứng viên. Lọc `batch_id` bị bỏ qua vì alias không gắn với batch.
- PNG/JPG/XLSX được nhận nhưng trả `unsupported` (đọc ảnh thuộc phần AI).
- Thêm phụ thuộc `python-multipart` (FastAPI cần nó để đọc `multipart/form-data`).

### Q11. Ghi `audit_log` tối thiểu
Các thao tác ghi đều thêm một dòng `audit_log` (`parameter.update/confirm`, `goal.update`, `sku.demand_group`, `scenario.run`, `ingest.*`, `sku_alias.decide`) với `actor`, `actor_role`, `before`/`after`. Đây là phần sàn của GOV-07, đủ để Nhật ký tra lại sau. Ghi audit lỗi thì không làm hỏng request.

### Q12. `priority_score` theo 06 §2, bỏ `w_force`
`40·stock_risk + 25·spend_share + 25·deviation + 10·staleness`. `w_force` cần orchestrator biết người dùng đang mở SKU nào nên chưa tính. SKU đã hết hàng có `stock_risk = 1`, nên nằm đầu danh sách.

### Q13. Plan đặt tên khác yêu cầu đợt này: làm cả hai
`PUT` và `PATCH /skus/{sku}/demand-group`; `/forecast/scores` và `/models/scores`; `POST /goals` và `PUT /goals/{scope}/{scope_id}`; `/data-quality` (tóm tắt) và `/data-quality/issues`; `start/end` và `from/to` cho panel. Đã ghi lại vào 08.

## 4. Thời gian `/scenarios/run` (dự báo đã có)

| Ca | Thời gian |
|---|---|
| Server thật, DB demo đã `fit`, CD002 11/11 00:00, 1.000 mẫu | 0,27 giây (curl). Trong đó `run_simulation` 0,24 giây: đọc 53 ms, mô phỏng 61 ms, đề xuất + độ nhạy 93 ms |
| Như trên, 300 mẫu, `restock_delay_days = −1` | 0,26 giây |
| Như trên, 1.000 mẫu, override | 0,37 giây |
| Trúng cache | 0,08 giây |
| TestClient, demo tới 11/11 01:00 (`test_cd002_1111.py`), lần đầu, phải dự báo | 13,2 giây |
| Như trên, các lần sau (300/1.000/300 mẫu) | 0,56 / 0,69 / 0,56 giây |
| `pytest -m perf` (`test_cd002_run_under_1s`, tốt nhất trong 3 lần, 1.000 mẫu) | 0,65 giây (< 1 giây) |

Máy dev đang chạy song song bộ test đầy đủ và agent khác. Test thời gian gắn marker `perf` nên không chặn CI.

## 5. Ví dụ phản hồi thật (rút gọn)

Server `uvicorn app.main:app --port 8765` trên DB demo (`cli reset --yes` → `ingest --dir ../mock-data/out/demo` → `backtest` → `fit` → `estimate-ads`, chỉ có lịch sử nên dữ liệu tới 11/11 00:00). `…(+n)` là phần đã cắt.

**`GET /freshness`** (viewer)
```json
{"data": {"now": "2026-11-11T00:00:00+07:00", "clock": "data", "data_as_of": "2026-11-11T00:00:00+07:00",
  "sources": [
    {"source": "orders", "label": "Đơn hàng", "as_of": "2026-11-11T00:00:00+07:00", "status": "fresh", "lag_minutes": 0, "threshold_minutes": 30, "missing_skus": null},
    {"source": "ads", "label": "Quảng cáo", "as_of": "2026-11-10T21:00:00+07:00", "status": "fresh", "lag_minutes": 180, "threshold_minutes": 180},
    {"source": "cost", "label": "Giá vốn", "as_of": "2026-10-30T14:44:13+07:00", "status": "partial", "lag_minutes": null, "missing_skus": 39},
    "…(inventory, campaigns, sale_program)"]},
 "meta": {"data_as_of": "2026-11-11T00:00:00+07:00", "computed_at": "2026-10-30T14:52:33+07:00", "compute_state": "fresh", "confidence": null, "sources": [], "cache_hit": null}}
```

**`GET /skus?campaign_id=CD002`** (sau khi chạy mô phỏng, một dòng)
```json
{"data": [{"sku": "NOICHIEN2L", "name": "Nồi chiên không dầu mini 2L", "variant": "", "product_id": "NOICHIEN2L", "is_bundle": false,
  "demand_group": "on_dinh", "stock_available": 83, "hours_of_cover": {"p10": 12.0, "p50": 17.0, "p90": 35.0, "horizon_hours": 72.0},
  "sold_today_units": 36, "sold_3h_units": 10, "deviation": null,
  "campaign": {"id": "CD002", "status": "Đang chạy", "status_code": "running"},
  "recommendation": {"recommendation_id": "rec-01M65WR7SW…", "status": "recommend",
                     "best": {"kind": "schedule_hours", "pct": null, "windows_local": [[0, 2], [12, 14]]}, "confidence": "reference",
                     "scenario_id": "scn-01M65WR7X1…", "created_at": "2026-10-30T14:52:52+07:00"},
  "open_alerts": 0, "priority_score": 21.67, "confidence": "reference", "forecast_id": "fc-01M65WDGN1…",
  "compute_state": "fresh", "computed_at": "2026-10-30T14:47:02+07:00"}, "…"],
 "meta": {"data_as_of": "2026-11-11T00:00:00+07:00", "computed_at": "…"}, "next_cursor": null}
```

**`GET /skus/NOICHIEN2L`** (phần thêm so với dòng danh sách)
```json
{"incoming": [{"qty": 400, "planned_date": "2026-11-12", "seen_at": "2026-11-10T00:00:00+07:00"}],
 "price_list_vnd": 690000, "price_effective_vnd": 690000, "sold_last_hour_units": 3,
 "demand_group_info": {"value": "on_dinh", "source": "auto", "confirmed": false}, "data_labels": [],
 "campaign_detail": {"id": "CD002", "name": "[Tìm kiếm] Nồi chiên mini + giấy lót", "daily_budget_vnd": 1864000, "sku_level_control": false,
                     "status": "Đang chạy", "status_code": "running", "status_at": "2026-11-10T20:00:00+07:00", "shared_with": ["COMBO-NOICHIEN", "GIAYLOT50"]},
 "components_of": [], "used_in_combos": ["COMBO-NOICHIEN"],
 "commitments": [{"kind": "committed_price", "sku": "NOICHIEN2L", "value": 531000, "day_local": "2026-11-11"},
                 {"kind": "flash_reserve", "value": 23}, "…(+2 campaign_must_run)"],
 "cost": {"unit_cost_vnd": 455000, "packaging_cost_vnd": 3000, "valid_from": "2026-10-30T14:44:13+07:00", "note": null},
 "contribution_per_unit_vnd": 149200, "as_of": "2026-11-11T00:00:00+07:00",
 "source": {"id": "snap-NOICHIEN2L-2359", "kind": "snapshot", "label": "Tình trạng SKU, dữ liệu tới 23:59 10/11"}}
```

**`GET /skus/NOICHIEN2L/forecast?horizon_hours=4`**
```json
{"data": {"forecast_id": "fc-01M65WDGN1…", "sku": "NOICHIEN2L", "action": "continue", "origin": "2026-11-11T00:00:00+07:00", "horizon_hours": 4,
  "hourly": [{"t": "2026-11-11T00:00:00+07:00", "mean": 4.871, "p10": 1.0, "p50": 4.0, "p90": 11.0}, "…(+3)"],
  "total": {"p10": 2.0, "p50": 7.0, "p90": 16.0},
  "model": {"id": "tsb@1.0.0", "name": "tsb", "version": "1.0.0"},
  "score": {"wape": 0.335, "coverage_80": 0.856, "bias": 0.017, "window": "dd_1010", "n_points": 648, "demand_group": "on_dinh", "context": "double_day"},
  "demand_group": "on_dinh", "context": "double_day", "reasons": ["Hệ số ngày đôi học từ 3 ngày đôi trước", "…"], "confidence": "reference"},
 "meta": {"data_as_of": "2026-11-11T00:00:00+07:00", "computed_at": "2026-10-30T14:47:02+07:00", "compute_state": "fresh", "confidence": "reference",
          "sources": [{"id": "fc-01M65WDGN1…", "kind": "forecast", "label": "Dự báo tsb@1.0.0 lúc 2026-11-11T00:00:00+07:00", "tool": "get_forecast", "href": "/api/v1/skus/NOICHIEN2L/forecast"}]}}
```

**`GET /campaigns/CD002`**
```json
{"data": {"campaign_id": "CD002", "name": "[Tìm kiếm] Nồi chiên mini + giấy lót", "type": "Tìm kiếm", "daily_budget_vnd": 1864000, "sku_level_control": false,
  "skus": ["COMBO-NOICHIEN", "GIAYLOT50", "NOICHIEN2L"], "status": "Đang chạy", "status_code": "running", "spend_today_vnd": 1863999, "budget_used_pct": 100.0,
  "iroas": {"estimate_id": "iroas-01M65WDP1K…", "mean": 4.492, "p10": 3.394, "p90": 5.587, "platform_roas": 8.092, "breakeven_iroas": 4.956, "confidence": "reference"},
  "sku_detail": [{"sku": "NOICHIEN2L", "name": "Nồi chiên không dầu mini 2L", "is_bundle": false, "list_price_vnd": 690000, "price_effective_vnd": 690000, "stock_available": 83, "unit_cost_vnd": 455000}, "…(+2)"],
  "price_max_vnd": 690000,
  "commitments": [{"kind": "campaign_must_run", "campaign_id": "CD002", "windows_local": [[0, 2]], "window_start": "2026-11-11T00:00:00+07:00", "window_end": "2026-11-11T02:00:00+07:00",
                   "text": "Không tắt chiến dịch trong khung flash sale (đã cam kết với sàn)"}, "…([[12, 14]])"],
  "goal": {"goal_id": "goal-01M65WR7G4…", "kind": "profit", "label": "Lợi nhuận", "deadline": "2026-11-12T00:00:00+07:00", "confirmed_by": "u-lead-1", "version": 1}}}
```

**`GET /campaigns/CD002/iroas`**
```json
{"data": {"campaign_id": "CD002", "estimate_id": "iroas-01M65WDP1K…", "iroas": {"p10": 3.394, "p50": 4.492, "p90": 5.587, "mean": 4.492},
  "marginal_iroas": 1.61, "platform_roas": 8.092, "inflation": 1.801, "breakeven_iroas": 4.956, "method": "adstock_grid@1.0.0",
  "n_hours_used": 2548, "n_clean_off_hours": 413, "excluded_hours": {"unknown": 33, "launch": 72, "stock_pause": 0, "low_stock": 227},
  "delta": 0.5, "window": {"from": "2026-07-14T00:00:00+07:00", "to": "2026-11-11T00:00:00+07:00"}, "confidence": "reference",
  "reasons": ["Ads còn tác dụng sau khi tắt: δ = 0,5 mỗi giờ, dùng chung cho cả shop", "…"], "flags": ["MODEL_CHECK_DISAGREE"]},
 "meta": {"confidence": "reference", "compute_state": "fresh", "sources": [{"id": "iroas-01M65WDP1K…", "kind": "iroas", "href": "/api/v1/campaigns/CD002/iroas"}]}}
```

**`GET /parameters?scope=sku&scope_id=NOICHIEN2L&key=leftover_value_per_unit`** (viewer)
```json
{"data": [{"key": "leftover_value_per_unit", "label": "Giá trị 1 sp còn lại sau hạn (V)", "unit": "đ/sp", "kind": "scalar",
  "value": 227500.0, "default": 227500.0, "default_rule": "0.5 × giá vốn", "is_default": true, "min": 0.0, "max": 690000.0, "max_rule": "giá bán của SKU",
  "scope": "default", "scope_id": "*", "allowed_scopes": ["session", "sku"], "editable_by": "operator", "can_edit": false, "source": "seller",
  "is_scenario": false, "version": 0, "set_by": "system", "set_at": null, "reason": null, "stored": [], "pending_suggestions": []}], "next_cursor": null}
```

**`POST /goals`** (lead) `{"scope_id":"CD002","kind":"profit","deadline":"2026-11-12T00:00:00+07:00","reason":"Lãi trong ngày 11.11"}` → 201
```json
{"data": {"goal_id": "goal-01M65WR7G4EZ0B0ZKKF22FV858", "scope": "campaign", "scope_id": "CD002", "kind": "profit", "label": "Lợi nhuận tới 00:00 12/11",
  "deadline": "2026-11-12T00:00:00+07:00", "target_units": null, "params": {"reason": "Lãi trong ngày 11.11"}, "confirmed_by": "u-lead-1", "active": true, "version": 1}}
```

**`POST /scenarios/run`** (operator) `{"campaign_id":"CD002","goal_id":"goal-01M65WR7G4…","samples":1000}`: 0,27 giây
```json
{"data": {"scenario_id": "scn-01M65WR7X151P8VAR0ZHX5638T",
  "request": {"campaign_id": "CD002", "start": "2026-11-11T00:00:00+07:00", "horizon_hours": 24, "goal_id": "goal-01M65WR7G4…", "actions": null,
              "param_overrides": {}, "samples": 1000, "seed": 20261111, "session_id": null},
  "results": [
    {"key": "continue", "label": "Chạy tiếp", "action": {"kind": "continue", "pct": null, "windows_local": null}, "feasible": true, "violations": [],
     "objective": {"mean": -1614751.7, "p10": -1934759, "p50": -1750959, "p90": -1392075}, "contribution": {"mean": 1583310, "…": "…"},
     "spend": {"mean": 5591999, "p10": 5591999, "p50": 5591999, "p90": 5591999}, "units_sold": {"mean": 115.8, "p10": 95, "p50": 112, "p90": 140},
     "leftover_units": {"mean": 254.2, "…": "…"}, "leftover_value": {"mean": 2393937, "…": "…"}, "lost_units": {"mean": 133.4, "…": "…"},
     "stockout_hour_p50": "2026-11-11T14:00:00+07:00",
     "per_sku": [{"sku_id": "COMBO-NOICHIEN", "is_bundle": true, "contribution_per_unit": 29120.0, "leftover_value_per_unit": 0.0, "demand": {"…": "…"},
                  "units_sold": {"…": "…"}, "lost_units": {"…": "…"}, "leftover_units": null, "stockout_hour_p50": "2026-11-11T15:00:00+07:00"}, "…(+2)"],
     "goal_eligible": true, "p_target": null, "goal_note": null, "violation_details": []},
    {"key": "pause", "label": "Tạm dừng", "feasible": false, "violations": ["campaign_must_run"], "objective": {"mean": 4528580, "…": "…"}, "spend": {"mean": 0, "…": "…"}, "units_sold": {"mean": 101.3, "…": "…"}, "…": "…"},
    {"key": "reduce_budget:0.3", "objective": {"mean": 73463}, "spend": {"mean": 3914399}, "units_sold": {"mean": 115.0}, "…": "…"},
    {"key": "reduce_budget:0.5", "objective": {"mean": 1205379}, "spend": {"mean": 2796000}, "units_sold": {"mean": 114.0}, "…": "…"},
    {"key": "schedule_hours:0-2,12-14", "label": "Chỉ chạy theo khung giờ 00:00–02:00, 12:00–14:00", "feasible": true,
     "objective": {"mean": 2722332}, "spend": {"mean": 1473097}, "units_sold": {"mean": 107.0}, "stockout_hour_p50": "2026-11-11T16:00:00+07:00", "…": "…"},
    {"key": "pause_until_restock", "feasible": false, "violations": ["campaign_must_run"], "…": "…"}],
  "recommendation": {"recommendation_id": "rec-01M65WR7SW257PK3FQ01CFMFBY", "campaign_id": "CD002", "status": "recommend",
    "best": {"kind": "schedule_hours", "pct": null, "windows_local": [[0, 2], [12, 14]]}, "runner_up": {"kind": "reduce_budget", "pct": 0.5, "windows_local": null},
    "best_label": "Chỉ chạy theo khung giờ 00:00–02:00, 12:00–14:00", "runner_up_label": "Giảm ngân sách 50%",
    "p_best_beats_runner_up": 1.0, "expected_gain_vnd": 1516953, "zone_bar": null, "flip_conditions": [], "confidence": "reference",
    "reasons": ["Hàng NOICHIEN2L sẽ hết khoảng 14:00 11/11 (nhu cầu ~164 sp, tồn 83 sp): ads không bán thêm được khi hết hàng",
                "Ở giá 11.11 (531.000đ) mỗi sp NOICHIEN2L lãi 9.280đ; ads chỉ có lời khi iROAS ≥ 57.2",
                "Không được tắt hẳn vì đã cam kết với sàn trong khung flash sale",
                "Chỉ chạy theo khung giờ 00:00–02:00, 12:00–14:00 lợi hơn Giảm ngân sách 50% khoảng 1.516.953đ (xác suất 100%)",
                "Giữ hàng lợi hơn bán: mỗi sp còn lại đáng 56.725đ, bán chỉ lãi 12.585đ",
                "Kết luận giữ nguyên trong khoảng bất định hiện tại", "Dự báo nhu cầu ở mức 'reference'"],
    "reason_sources": [
      {"id": "fc-01M65WDGN1…", "kind": "forecast", "label": "Dự báo nhu cầu NOICHIEN2L", "data_as_of": "2026-11-11T00:00:00+07:00", "tool": "get_forecast", "href": "/api/v1/skus/NOICHIEN2L/forecast"},
      {"id": "m-NOICHIEN2L", "kind": "contribution", "label": "Giá bán, giá vốn, phí sàn của NOICHIEN2L", "tool": "get_sku_snapshot", "href": "/api/v1/skus/NOICHIEN2L", "data_as_of": "…"},
      {"id": "commit-CD002", "kind": "commitment", "label": "Cam kết chương trình sale của CD002", "tool": "get_campaign_status", "href": "/api/v1/campaigns/CD002", "data_as_of": "…"},
      {"id": "scn-01M65WR7X1…", "kind": "scenario", "tool": "run_scenario", "href": "/api/v1/scenarios/scn-01M65WR7X1…", "…": "…"}, "…(+3)"],
    "inputs_ref": {"forecast_ids": ["fc-01M65WDFP5…", "fc-01M65WDG0M…", "fc-01M65WDGN1…"], "estimate_id": "iroas-01M65WDP1K…",
                   "param_version": {"leftover_value_per_unit": 0, "restock_delay_days": 0, "…": 0}, "goal_id": "goal-01M65WR7G4…",
                   "data_as_of": "2026-11-11T00:00:00+07:00", "panel_version": "ib-01M65W8CF2…", "iroas_source": "estimate"},
    "scenario_id": "scn-01M65WR7X151P8VAR0ZHX5638T", "created_at": "2026-10-30T14:52:52+07:00", "superseded_by": null, "active": true},
  "cache_hit": false, "input_hash": "…", "blocked": false,
  "timings_ms": {"load": 53.4, "simulate": 60.8, "recommend": 92.5, "total": 240.7, "api_total": 244.2}, "requested_by": "user", "created_at": "…"},
 "meta": {"data_as_of": "2026-11-11T00:00:00+07:00", "computed_at": "2026-10-30T14:52:52+07:00", "compute_state": "fresh", "confidence": "reference",
          "sources": [{"id": "scn-01M65WR7X1…", "kind": "scenario", "tool": "run_scenario", "href": "/api/v1/scenarios/scn-…"},
                      {"id": "fc-01M65WDFP5…", "kind": "forecast", "label": "Dự báo COMBO-NOICHIEN", "href": "/api/v1/skus/COMBO-NOICHIEN/forecast"}, "…(+2 forecast, 1 iroas)"],
          "cache_hit": false}}
```
Gửi lại đúng `request` thì trả cùng `scenario_id`, `meta.cache_hit: true`, 0,08 giây. Ca 11/11 01:00 (có live tới 00:59) cho kết quả như `test_cd002_1111.py`: `best = schedule_hours [[0,2],[12,14]]`, `pause` và `pause_until_restock` `feasible=false` vì `campaign_must_run`, `horizon_hours = 23`.

**`zone_bar` có `inputs`** (CD005 áo giữ nhiệt, 11/11 00:00, `profit`)
```json
{"axis_label": "Lượng bán mỗi giờ nếu pause (sp)", "threshold": 4.172, "interval": [3.208, 4.292, 5.833], "verdict": "undetermined",
 "alt_thresholds": [{"label": "hàng dư 0đ", "threshold": 5.541, "leftover_value_per_unit": 0.0, "status": "ok"}],
 "inputs": {"units_left": 830.0, "hours_left": 24.0, "contribution_per_unit_vnd": 49320.0, "spend_per_hour_vnd": 27624.96,
            "rate_continue_per_hour": 6.101, "pause_rate_interval": [3.208, 4.292, 5.833], "leftover_value_per_unit_vnd": 35000.0, "alt_leftover_values": [0.0]}}
```

**`POST /scenarios/breakeven`** (ví dụ trang 10)
```json
{"data": {"zone_bar": {"axis_label": "Lượng bán mỗi giờ nếu pause (sp)", "threshold": 22.5, "interval": [18.0, 24.0, 31.0], "verdict": "undetermined",
           "alt_thresholds": [{"label": "hàng dư 20.000đ", "threshold": 16.5, "leftover_value_per_unit": 20000.0, "status": "ok"}],
           "inputs": {"units_left": 120.0, "hours_left": 4.0, "contribution_per_unit_vnd": 45000.0, "spend_per_hour_vnd": 450000.0, "rate_continue_per_hour": 40.0,
                      "pause_rate_interval": [18.0, 24.0, 31.0], "leftover_value_per_unit_vnd": 0.0, "alt_leftover_values": [20000.0]}},
          "flip_leftover_value_vnd": 16875, "pi_continue_vnd": 4050000, "status": "ok", "q_star_units": 90.0, "reason": null},
 "meta": {"compute_state": "fresh", "confidence": "scenario"}}
```

**Lỗi**
```json
// POST /scenarios/run {"campaign_id":"CD002","goal_id":"…","disable_skus":["GIAYLOT50"]} → 409
{"error": {"code": "CONSTRAINT_VIOLATION", "message": "Chiến dịch CD002 dùng ngân sách chung cho 3 SKU, không tắt riêng từng SKU được. Muốn làm vậy cần tách SKU sang chiến dịch riêng.",
           "details": {"constraint": "sku_level_control", "campaign_id": "CD002"}}}
// POST /scenarios/run với X-Role: viewer → 403
{"error": {"code": "FORBIDDEN", "message": "Vai trò của bạn không có quyền thực hiện thao tác này. Cần vai trò Vận hành, bạn đang là Xem.", "details": {"required": "operator", "role": "viewer"}}}
// GET /skus không có X-User → 401
{"error": {"code": "UNAUTHENTICATED", "message": "Thiếu thông tin người dùng (X-User).", "details": {}}}
// POST /scenarios/run CD010 (thiếu giá vốn) → 422 BLOCKING_DATA_ISSUE, details.reasons = ["Thiếu giá vốn: …", …]
// POST /scenarios/run {"param_overrides":{"leftover_value_per_unit":9000000}} → 400 PARAM_OUT_OF_RANGE, details.max = 690000
```

**`GET /ingest/sku-queue?limit=2`** (operator)
```json
{"data": {"summary": {"auto_matched": 860, "needs_review": 17, "confirmed": 0, "rejected": 0},
  "items": [{"alias_id": "sal-0d0a5df74ca1b76ea76c", "alias": "Thảm chùi chân thấm nước - hồng", "raw_code": "", "alias_kind": "cost_name", "source_type": "cost",
             "status": "pending", "candidates": [{"sku": "THAM2-HONG", "score": 0.931, "name": "Thảm chùi chân thấm nước (mẫu 2) - Hồng"}],
             "selected": "THAM2-HONG", "method": "fuzzy", "flag": "check", "decided_by": null}, "…"]},
 "next_cursor": "eyJvIjoyfQ"}
```

**`GET /data-quality/issues?severity=blocking&limit=3`**
```json
{"data": [{"id": "dq-028830536e737feb9ccbd9f8", "kind": "MISSING_COST", "severity": "blocking", "status": "open", "sku": "QUATMINI-XANH", "entity_type": "sku",
           "entity_id": "QUATMINI-XANH", "row_ref": null, "message": "Thiếu giá vốn, chưa tính được lợi nhuận",
           "detail": {"reason": "no_cost_row", "pending_aliases": [], "blocks": "economics"}, "batch_id": "ib-01M65W8CEW…", "detected_at": "2026-10-30T14:44:13+07:00", "resolved_by": null}, "…"],
 "next_cursor": "eyJvIjozfQ"}
```

## 6. Sửa lỗi nhỏ M3 (30/10, sau chạy thử end-to-end của web)

Các lỗi 1–6 ở M3-web §13. Kết quả mô phỏng và đề xuất không đổi: `eval/demo_whatif.py --only 1,4g,5,8a` ra đúng số như trước (CD002 11/11 01:00 profit: `schedule_hours 0-2,12-14`, hơn continue 3,99 tr, hơn giảm 50% 1,23 tr). Test: `tests/api/test_m3_fixes.py` và một khẳng định thêm ở `tests/api/test_cd002_1111.py`.

### S1. Lần chạy what-if không thay đề xuất đang hiệu lực (lỗi 2)
- **Vấn đề:** mỗi `POST /scenarios/run` có `param_overrides` (kéo thanh trượt) tạo đề xuất mới và supersede đề xuất của chiến dịch. Mở lại màn thì "đề xuất" là kết quả what-if của người vừa kéo.
- **Lựa chọn:** spec 08 không có cờ `persist`/`what_if`, nên body giữ nguyên. Simulator tự xếp loại (`service.is_what_if`): lần chạy là **what-if** khi có `param_overrides` (kể cả giá trị 0), có `session_id`, hoặc có `actions` khác tập hành động mặc định. Lần chạy what-if vẫn được lưu (`scenarios`, `scenario_results`, `recommendations`) để `GET /scenarios/{id}`, `GET /recommendations/{id}` và nguồn trích dẫn dùng được. Dòng đề xuất của nó mang `what_if = TRUE`: không bao giờ là đề xuất hiện hành, không supersede dòng nào, không phát `RecommendationChanged`. Chỉ lần chạy chuẩn (không override, không session, hành động mặc định) mới supersede.
- **Phản hồi:** `RecommendationOut` thêm `what_if` (mặc định `false`). Với what-if: `what_if = true`, `active = false`, `superseded_by = null`. `GET /recommendations?active=true`, `/skus`, `/campaigns` bỏ qua dòng what-if. `active=false` vẫn liệt kê chúng (có cờ). `active` và `superseded_by` trong `POST /scenarios/run` và `GET /scenarios/{id}` đọc lại từ DB: gửi lại request của một lần chạy chuẩn đã bị thay thì trúng cache nhưng trả `active = false`.
- **Khóa cache** thêm `session_id`. Trước đây lần chạy có `session_id` mà phiên chưa có tham số trúng cache của lần chạy chuẩn.
- **Migration 3** thêm `recommendations.what_if` và `scenarios.request`. Với DB cũ: đánh dấu `what_if` cho đề xuất có `overrides` khác rỗng, rồi trả lại cho mỗi chiến dịch đề xuất chuẩn mới nhất (`superseded_by = NULL`). DB demo đang lỗi sẽ tự đúng khi mở bằng code mới.
- AI (`app/ai/tools/compute.py`) giữ nguyên: what-if của AI chạy `persist=False`.

### S2. `request` echo đúng request đã chạy (lỗi 1)
- **Nguyên nhân:** log uvicorn lúc 14:52 cho thấy lần `POST /scenarios/run` với `leftover_value_per_unit = 9.000.000` trả 200, vì bản API khi đó chưa chặn khoảng giá trị. Lần chạy đó được lưu và, do lỗi 2, thay đề xuất chuẩn. `GET /scenarios/{id}` của đề xuất hiện hành vì thế echo đúng override 9 triệu của lần chạy đó. API bây giờ chặn giá trị này nên gửi lại → 400. Code hiện tại kiểm `_check_leftover` (và `parse_overrides`) **trước** khi mô phỏng và không ghi gì khi trả 400 (có test).
- **Sửa thêm:** read-back cũ dựng `request` từ các cột. `actions` khi đó là danh sách 6 hành động đã chạy, theo thứ tự API sắp. Gửi lại thì khóa cache khác (thứ tự hành động) → chạy lại và tạo đề xuất mới. Nay cột `scenarios.request` lưu nguyên request (đã điền `start` UTC và `horizon_hours`), và `GET /scenarios/{id}` echo đúng nó. Gửi lại trúng cache, cùng `scenario_id`.

### S3. Giờ VN trong `violation_details` (lỗi 3)
`engine.simulate` truyền nhãn giờ `HH:MM dd/mm` giờ VN cho `check_action`: "… (3 giờ, từ 01:00 11/11)". Chỉ đổi câu chữ.

### S4. Không còn thời điểm UTC lọt ra (lỗi 4)
- `CampaignDetail.recommendation` (và `/campaigns`) là model `CampaignRecRef`, với `created_at`, `data_as_of` kiểu `LocalDatetime`. `snapshot.active_recommendations` parse `inputs_ref.data_as_of` (JSON text dạng `Z`) thành datetime.
- Rà nhanh bằng test: `/campaigns`, `/campaigns/CD002`, `/skus`, `/skus/{sku}`, `/scenarios/{id}`, `/recommendations[/{id}]`, `/goals`, `/freshness` không còn chuỗi `…Z` hay `…+00:00`.
- `start` về UTC: khóa cache simulator đã chuẩn hóa từ đợt SIM-04 (`tests/unit/simulator/test_service_key.py`). API bỏ bước tự đổi. Test API: cùng thời điểm gửi dạng `Z` rồi `+07:00` trúng cùng một cache.

### S5. `meta.total` cho `/skus`, nhãn mục tiêu có hạn (lỗi 5)
- 09 §2.1 chỉ ghi "Toàn bộ SKU (300)", không quy định trường. Chọn `meta.total` (trường mới của `Meta`, `null` ở endpoint khác): số dòng khớp bộ lọc (`q`, `campaign_id`, …) trên mọi trang.
- `CampaignDetail.goal.label` dùng chung `goal_label` với `/goals`: "Lợi nhuận tới 00:00 12/11" ("Bán đủ số lượng 97 sp tới …" với `sell_target`). `deadline` vẫn có như trước.

### S6. `duplicate_of` trong OpenAPI (lỗi 6)
`ScenarioResult.duplicate_of` (`[DEV]`) là key của hành động xếp trên có kết quả trùng hệt (05 §9 bước 3, vd `pause_until_restock` ≡ `pause` khi không có hàng về), lấy từ `Selection.duplicate_of`. Không lưu DB: read-back trả `null`.

### Web
- `schema.d.ts` sinh lại từ OpenAPI. `ScenarioResult` = `ScenarioResultOut` (có `duplicate_of`). `Recommendation` có `what_if` (tùy chọn ở web). `CampaignDetail.recommendation` có kiểu.
- Bỏ chỗ né Q-W6: `buildSimRequest` giữ `param_overrides` của request echo, thanh trượt ghi đè khóa của nó. `goalText` dùng thẳng `goal.label`. Khi chọn đề xuất, bỏ dòng `what_if`.
- Tổng quan ca: "Toàn bộ SKU (N)" và "Đã tải n/N mã" khi có `meta.total`.

### Ngoài phạm vi
`tests/unit/test_demo_import.py` ghi/đọc file không chỉ định encoding nên hỏng trên Windows (cp1252). Đã thêm `encoding="utf-8"` để `tasks.py check` sạch.

## 7. Đề xuất cho đợt sau
1. **Orchestrator (ORC-01/04):** ghi `sku_compute_state` để API trả `queued`/`computing`. Dự báo trước ở mỗi mốc để lần chạy đầu `/scenarios/run` không mất 10–25 giây.
2. **Plan 06:** đưa ngưỡng stale vào danh mục tham số (Q2) nếu muốn sửa được trên UI.
3. **Simulator:** trả `reasons` có cấu trúc (`{text, source_kind, sku}`) để `reason_sources` không phải dựa vào câu chữ (Q6).
4. **Bảng `iroas_estimates`:** lưu thêm `iroas_p50` (Q về `p50` = mean).
5. **AI (M4):** dùng `GET /skus/{sku}` (`source`), `/campaigns/{id}/iroas`, `POST /scenarios/run` làm công cụ `get_sku_snapshot`, `get_iroas`, `run_scenario`. Các nguồn đã có `tool` và `href`.
