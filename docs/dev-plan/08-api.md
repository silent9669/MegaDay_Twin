# 08 · API

Backend phơi một REST API JSON dưới `/api/v1` và một luồng SSE `/api/v1/stream` cho cập nhật thời gian thực. Mọi kết quả có số đều trả kèm `data_as_of`, `computed_at`, `compute_state` và `confidence` để UI không bao giờ hiện số cũ như số mới.

Module: `backend/app/api/` (một router mỗi nhóm), schema Pydantic trong `backend/app/api/schemas/`. FastAPI tự sinh OpenAPI ở `/api/v1/openapi.json`; frontend sinh client TypeScript từ đó bằng `openapi-typescript` (INF-05).

---

## Bổ sung: nhãn tool tại Data Studio (31/10/2026)

Gateway Worker có `GET /api/product-labels` và `POST /api/product-labels` cho
workspace đã đăng nhập. Đây là API workspace, không thay thế endpoint Python
`/api/v1/skus/{sku}/demand-group`. POST chỉ nhận `sourceRevision`; server đọc sáu
nguồn đã lưu, rút candidates, gọi adapter và kiểm tra kết quả. DTO và giới hạn
ở `web/src/studio/product-labels.ts`; hướng nối adapter ở `web/README.md`.

Response chứa `configured`, `provider`, `sourceRevision`, `sourceFingerprint`,
`products`, `run`, `stale`. Run có ID, nguồn dữ liệu, provider, trạng thái
`running|completed|partial|failed`, thời gian, `results` và lỗi đã làm sạch.
Mỗi kết quả có `productId`, `group` (năm nhóm hiện tại hoặc null), `reason`,
`confidence` tùy chọn. Không nhận kết quả từ browser để ghi vào kho nhãn.
Mã chưa được canonical-SKU matching không được áp dụng thẳng vào forecast.

Chưa cấu hình trả trạng thái rõ ở GET và 503 ở POST; revision cũ hoặc job bị
thay thế trả 409; request/đầu vào không hợp lệ trả 422. Không có fallback nhãn
giả từ tool. Classifier Python cũ được giữ đến khi API thật và luồng áp dụng
nhãn đã hoàn thành. Hợp đồng chi tiết của provider ngoài chưa được chọn.

## 1. Quy ước chung

| Mục | Quy ước |
|---|---|
| Định dạng | JSON UTF-8. Tên trường snake_case tiếng Anh. Thông điệp cho người dùng (`message`) tiếng Việt |
| Thời gian | ISO 8601 có offset `+07:00` khi trả về. Nhận cả UTC (`Z`) và `+07:00` |
| Tiền | số nguyên VND (`int`), tên trường kết thúc `_vnd` |
| Số lượng | số nguyên hoặc thực, tên trường nói rõ đơn vị (`units`, `pct`) |
| Khoảng dự báo | object `{"p10":..,"p50":..,"p90":..}` |
| Định danh | `sku` = SKU sàn (`NOICHIEN2L`); `campaign_id` = mã chiến dịch (`CD002`); id nội bộ khác dạng `<tiền tố>-<số>` |
| Xác thực | Header `X-User: <user_id>` + `X-Role: operator|lead|viewer` (hackathon, không mật khẩu; xem 10-quan-tri-va-bao-mat). Thiếu header → 401 |
| Phân trang | Cursor: `?limit=50&cursor=<opaque>`; phản hồi có `next_cursor` (null khi hết). `limit` tối đa 500 |
| Lọc thời gian | `?from=...&to=...` (bao gồm `from`, không gồm `to`) |
| Idempotency | Các POST tạo tài nguyên nhận header `Idempotency-Key`; gửi lại cùng key trả lại kết quả cũ |
| Phiên bản | Đổi phá vỡ → `/api/v2`. Thêm trường không phá vỡ |

### Khung phản hồi có số

```json
{
  "data": { "...": "..." },
  "meta": {
    "data_as_of": "2026-11-11T14:59:00+07:00",
    "computed_at": "2026-11-11T15:01:12+07:00",
    "compute_state": "fresh",
    "confidence": "verified",
    "sources": [{"id": "fc-8812", "kind": "forecast", "label": "Dự báo seasonal_hourly v2026-11-11.1"}]
  }
}
```

`compute_state`: `fresh | queued | computing | stale | failed` (06-dieu-phoi-va-hieu-nang mục 4). `confidence`: `verified | reference | scenario`.

### Lỗi

```json
{
  "error": {
    "code": "PARAM_OUT_OF_RANGE",
    "message": "Giá trị hàng dư phải từ 0 đến 690.000đ.",
    "details": {"key": "leftover_value_per_unit", "min": 0, "max": 690000, "got": 900000}
  }
}
```

| HTTP | `code` | Khi nào |
|---|---|---|
| 400 | `VALIDATION_ERROR` | Body sai schema |
| 400 | `PARAM_OUT_OF_RANGE` | Tham số ngoài khoảng hợp lệ |
| 401 | `UNAUTHENTICATED` | Thiếu `X-User` |
| 403 | `FORBIDDEN` | Vai trò không đủ quyền |
| 404 | `NOT_FOUND` | Không có tài nguyên |
| 409 | `STATE_CONFLICT` | Chuyển trạng thái không hợp lệ (vd duyệt quyết định đã hủy) |
| 409 | `CONSTRAINT_VIOLATION` | Hành động vi phạm ràng buộc chương trình sale (vd tắt CD002 trong 12:00–14:00) |
| 409 | `STALE_INPUT` | Quyết định dựa trên kịch bản đã cũ (dữ liệu đổi sau khi tính) |
| 422 | `BLOCKING_DATA_ISSUE` | Thiếu dữ liệu bắt buộc (vd thiếu giá vốn) nên không tính được |
| 429 | `AI_BUDGET_EXCEEDED` | Hết ngân sách token |
| 503 | `AI_UNAVAILABLE` | OpenAI lỗi và không có bản ghi replay |

---

## 2. Danh sách endpoint

| Nhóm | Method | Path | Vai trò tối thiểu | Màn hình dùng |
|---|---|---|---|---|
| Hệ thống | GET | `/health` | không | — |
| | GET | `/me` | viewer | header |
| | GET | `/freshness` | viewer | mọi màn |
| Nhập dữ liệu | POST | `/ingest/files` | operator | Nhập dữ liệu |
| | GET | `/ingest/batches` | viewer | Nhập dữ liệu |
| | GET | `/ingest/batches/{batch_id}` | viewer | Nhập dữ liệu |
| | POST | `/ingest/batches/{batch_id}/mapping` | operator | Nhập dữ liệu |
| | GET | `/ingest/sku-queue` | operator | Nhập dữ liệu |
| | POST | `/ingest/sku-queue/{alias_id}` | operator | Nhập dữ liệu |
| | GET, POST | `/bundles` | operator | Nhập dữ liệu (khai báo combo, WH-02) |
| | POST | `/ingest/batches/{batch_id}/confirm` | operator | Nhập dữ liệu |
| | POST | `/ingest/demo-seed` | lead | Cài đặt |
| Chất lượng | GET | `/data-quality/issues` | viewer | Nhập dữ liệu, SKU |
| | POST | `/data-quality/issues/{id}/resolve` | operator | Nhập dữ liệu |
| SKU | GET | `/skus` | viewer | Tổng quan ca |
| | GET | `/skus/{sku}` | viewer | Chi tiết SKU |
| | GET | `/skus/{sku}/panel` | viewer | Chi tiết SKU |
| | PATCH | `/skus/{sku}/demand-group` | operator | Chi tiết SKU |
| Chiến dịch | GET | `/campaigns` | viewer | Tổng quan ca |
| | GET | `/campaigns/{campaign_id}` | viewer | So sánh |
| Dự báo | GET | `/skus/{sku}/forecast` | viewer | Chi tiết SKU, So sánh |
| | GET | `/models/scores` | viewer | Cài đặt (phiếu điểm) |
| | GET | `/models/registry` | viewer | Cài đặt |
| Hiệu quả ads | GET | `/campaigns/{campaign_id}/iroas` | viewer | So sánh |
| Mục tiêu, tham số | GET | `/goals` | viewer | Cài đặt |
| | PUT | `/goals/{scope}/{scope_id}` | lead | Cài đặt |
| | GET | `/parameters` | viewer | Cài đặt, So sánh |
| | PUT | `/parameters/{key}` | theo `editable_by` | Cài đặt, So sánh |
| Mô phỏng | POST | `/scenarios/run` | operator | So sánh |
| | GET | `/scenarios/{scenario_id}` | viewer | So sánh |
| | POST | `/scenarios/breakeven` | operator | So sánh |
| Đề xuất | GET | `/recommendations` | viewer | Tổng quan ca |
| | GET | `/recommendations/{id}` | viewer | So sánh |
| Quyết định | POST | `/decisions` | operator | So sánh |
| | GET | `/decisions` | viewer | Nhật ký |
| | GET | `/decisions/{id}` | viewer | Nhật ký |
| | POST | `/decisions/{id}/submit` | operator | So sánh |
| | POST | `/decisions/{id}/approve` | lead | Nhật ký, So sánh |
| | POST | `/decisions/{id}/reject` | lead | Nhật ký |
| | POST | `/decisions/{id}/cancel` | operator (người tạo) | Nhật ký |
| Hành động | POST | `/decisions/{id}/actions` | operator | So sánh |
| | POST | `/actions/{id}/verify` | operator | Nhật ký |
| Cảnh báo | GET | `/alerts` | viewer | Cảnh báo |
| | POST | `/alerts/{id}/ack` | operator | Cảnh báo |
| | POST | `/alerts/{id}/snooze` | operator | Cảnh báo |
| | POST | `/alerts/{id}/investigate` | operator | Cảnh báo |
| AI | POST | `/ai/chat` (SSE) | operator | Trợ lý |
| | GET | `/ai/sessions/{id}` | viewer | Trợ lý |
| | POST | `/ai/handoff` | operator | Nhật ký |
| Live | GET | `/live/status` | viewer | header |
| | POST | `/live/control` | lead | Cài đặt (chỉ demo) |
| Luồng | GET | `/stream` (SSE) | viewer | mọi màn |

---

## 3. Chi tiết từng nhóm

### 3.1 Hệ thống

`GET /freshness`

```json
{
  "data": {
    "now": "2026-11-11T15:03:00+07:00",
    "sources": [
      {"source": "orders", "as_of": "2026-11-11T14:59:00+07:00", "status": "fresh", "lag_minutes": 4, "threshold_minutes": 30},
      {"source": "ads", "as_of": "2026-11-11T12:59:00+07:00", "status": "fresh", "lag_minutes": 124, "threshold_minutes": 180},
      {"source": "inventory", "as_of": "2026-11-11T15:00:00+07:00", "status": "fresh", "lag_minutes": 3, "threshold_minutes": 60},
      {"source": "cost", "as_of": "2026-11-10T09:00:00+07:00", "status": "partial", "missing_skus": 17}
    ]
  }
}
```

`status`: `fresh | stale | partial | missing`.

### 3.2 Nhập dữ liệu

`POST /ingest/files` — `multipart/form-data`, trường `files[]` (CSV, XLSX, JSON, PNG/JPG). Tối đa 10 file, 50 MB/file.

```json
// 201
{
  "data": {
    "batches": [
      {"batch_id": "ib-41", "file_name": "gia_von_nhap_tay.csv", "detected_kind": "cost",
       "detect_confidence": 0.93, "rows": 283, "status": "needs_mapping"},
      {"batch_id": "ib-42", "file_name": "ton_kho_google_sheet.csv", "detected_kind": "inventory",
       "detect_confidence": 0.97, "rows": 35880, "status": "needs_mapping"}
    ]
  }
}
```

`status` của batch: `uploaded → needs_mapping → needs_sku_review → ready → loading → loaded | failed`.

`GET /ingest/batches/{batch_id}`

```json
{
  "data": {
    "batch_id": "ib-41", "detected_kind": "cost", "status": "needs_mapping",
    "columns": ["tên sp", "giá vốn", "phí đóng gói", "ghi chú"],
    "sample_rows": [["bình nước giữ nhiệt 500ml hồng", "92000", "3.000", ""]],
    "proposed_mapping": {
      "draft_id": "map-12", "by": "ai",
      "mappings": [
        {"source_column": "tên sp", "target_field": "cost_name", "transform": "none", "confidence": 0.95},
        {"source_column": "giá vốn", "target_field": "unit_cost", "transform": "parse_vnd", "confidence": 0.97},
        {"source_column": "phí đóng gói", "target_field": "packaging_cost", "transform": "parse_vnd", "confidence": 0.9},
        {"source_column": "ghi chú", "target_field": "cost_note", "transform": "none", "confidence": 0.8}
      ],
      "warnings": ["Cột giá vốn có 4 kiểu viết tiền khác nhau, đã chuẩn hóa"]
    },
    "pii_columns_dropped": [],
    "quality_preview": {"rows_ok": 279, "rows_with_issues": 4}
  }
}
```

`POST /ingest/batches/{batch_id}/mapping` — người dùng xác nhận hoặc sửa mapping.

```json
// request
{"draft_id": "map-12", "mappings": [{"source_column": "giá vốn", "target_field": "unit_cost", "transform": "parse_vnd"}]}
// 200 → batch chuyển needs_sku_review (nếu file có tên/mã SP) hoặc ready
{"data": {"batch_id": "ib-41", "status": "needs_sku_review"}}
```

`GET /ingest/sku-queue?batch_id=ib-41&status=review` — hàng chờ ghép SKU của ING-08

```json
{
  "data": {
    "summary": {"auto_matched": 266, "needs_review": 17, "unmatched": 0},
    "items": [
      {"alias_id": "al-203", "alias": "binh nuoc giu nhiet 500ml den", "alias_kind": "cost_name",
       "candidates": [{"sku": "BINHNUOC500-DEN", "score": 0.86}, {"sku": "BINHNUOC500-XANH", "score": 0.71}],
       "selected": "BINHNUOC500-DEN", "flag": "check"}
    ]
  },
  "next_cursor": null
}
```

`POST /ingest/sku-queue/{alias_id}` — chốt một alias: `{"sku": "BINHNUOC500-DEN"}`, hoặc `{"action": "ignore"}`. Trả `{"data": {"alias_id": "al-203", "sku": "BINHNUOC500-DEN", "decided_by": "u-ops-1"}}`.

`GET /bundles`, `POST /bundles` — khai báo thành phần combo (bảng `bundle_components`): `{"bundle_sku": "COMBO-NOICHIEN", "components": [{"sku": "NOICHIEN2L", "qty": 1}, {"sku": "GIAYLOT50", "qty": 1}]}`. Xong thì issue `BUNDLE_NO_STOCK_ROW` đóng lại.

`POST /ingest/batches/{batch_id}/confirm`

```json
// request
{"sku_matches": [{"alias": "binh nuoc giu nhiet 500ml den", "sku": "BINHNUOC500-DEN"}], "accept_remaining_auto": true}
// 202: nạp chạy nền, kết quả qua SSE event ingest.batch_loaded
{"data": {"batch_id": "ib-41", "status": "loading"}}
```

`POST /ingest/demo-seed` — nạp sẵn lịch sử từ `mock-data/out/<size>/` (dùng trước buổi demo). Body `{"size": "demo", "skip_files": ["gia_von_nhap_tay.csv", "ton_kho_google_sheet.csv"]}` để chừa hai file import live.

### 3.3 Chất lượng dữ liệu

`GET /data-quality/issues?status=open&sku=&severity=blocking`

```json
{
  "data": [
    {"id": "dq-17", "kind": "MISSING_COST", "severity": "blocking", "sku": "KHANLAU-G3",
     "message": "Thiếu giá vốn, chưa tính được lợi nhuận", "batch_id": "ib-41", "detected_at": "..."},
    {"id": "dq-18", "kind": "NEG_STOCK", "severity": "warning", "sku": "THAM-XANH",
     "message": "Tồn ngày 2026-09-03 là -7, đã bỏ qua dòng này", "batch_id": "ib-42"},
    {"id": "dq-19", "kind": "ADS_TZ_SHIFT", "severity": "info",
     "message": "File ads ghi giờ UTC, đã đổi về giờ Việt Nam (+7)", "batch_id": "ib-40"}
  ],
  "next_cursor": null
}
```

`kind` = `data_quality_issues.code`, đúng mã của [02 §3.1](02-lop-du-lieu.md): `MISSING_COST, NEG_STOCK, DUP_ORDER, ADS_TZ_SHIFT, ADS_GAP_DAY, ADS_LAG, UNMATCHED_SKU, PROVISIONAL_STATUS, BUNDLE_NO_STOCK_ROW, PII_DROPPED, SUSPICIOUS_NOTE, MONEY_FORMAT, STOCK_JUMP, STOCK_ADJUSTMENT, SPEND_OVER_BUDGET, ORDER_TOTAL_MISMATCH, COST_NOTE_REVIEW, UNKNOWN_SOURCE, UNPARSED_RULE` và 4 mã adapter `ROW_INVALID, UNKNOWN_STATUS, DUP_ROW, MISSING_COLUMNS` (docs/quyet-dinh/M1-adapter.md §4). `severity` của API: `error` trong DB → `blocking`, `warn` → `warning`, `info` → `info`.

### 3.4 SKU

`GET /skus?sort=priority&group=&has_alert=true&limit=50`

```json
{
  "data": [
    {
      "sku": "NOICHIEN2L", "name": "Nồi chiên không dầu mini 2L", "variant": "",
      "stock_available": 41, "hours_of_cover": {"p50": 6.5},
      "sold_today_units": 52, "deviation": "on_track",
      "campaign": {"id": "CD002", "status": "Đang chạy"},
      "recommendation": {"recommendation_id": "rec-551", "status": "recommend", "best": {"kind": "schedule_hours", "windows_local": [[0, 2], [12, 14]]}, "confidence": "reference"},
      "open_alerts": 1, "priority_score": 49.0,
      "compute_state": "fresh", "computed_at": "2026-11-11T15:01:12+07:00"
    }
  ],
  "meta": {"data_as_of": "2026-11-11T14:59:00+07:00"},
  "next_cursor": "eyJvIjo1MH0"
}
```

`GET /skus/{sku}` trả cùng dữ liệu như công cụ `get_sku_snapshot` (07-lop-ai mục 3).

`GET /skus/{sku}/panel?from=&to=&grain=hour`

```json
{
  "data": {
    "sku": "AOGIUNHIET-M", "grain": "hour",
    "points": [
      {"t": "2026-11-11T11:00:00+07:00", "units": 3, "stock_end": 9, "stockout": false,
       "ad_spend_vnd": 21000, "ad_spend_estimated": false, "price_effective_vnd": 119000, "event": "double_day"}
    ]
  }
}
```

`PATCH /skus/{sku}/demand-group` — `{"value": "mua_vu", "reason": "Hàng mùa đông"}`. Ghi người sửa và thời điểm.

### 3.5 Dự báo và phiếu điểm

`GET /skus/{sku}/forecast?horizon_hours=24&action=continue`

```json
{
  "data": {
    "sku": "NOICHIEN2L", "action": "continue", "horizon_hours": 24,
    "hourly": [{"t": "2026-11-11T15:00:00+07:00", "p10": 3, "p50": 5, "p90": 8}],
    "total": {"p10": 60, "p50": 82, "p90": 107},
    "model": {"id": "seasonal_hourly", "version": "2026-11-11.1"},
    "score": {"wape": 0.24, "coverage_80": 0.81, "window": "10.10", "n_points": 72}
  },
  "meta": {"confidence": "verified", "compute_state": "fresh", "data_as_of": "...", "computed_at": "..."}
}
```

`GET /models/scores?group=&window=10.10` → bảng phiếu điểm theo model × nhóm nhu cầu (03-lop-mo-hinh).

### 3.6 Hiệu quả ads

`GET /campaigns/{campaign_id}/iroas`

```json
{
  "data": {
    "campaign_id": "CD002", "iroas": {"p10": 3.6, "p50": 4.3, "p90": 5.1},
    "platform_roas": 6.7, "inflation": 1.56,
    "method": "did_onoff_stock_controlled", "n_switch_events": 41,
    "prior": {"source": "default", "value": 3.0},
    "window": {"from": "2026-07-14T00:00:00+07:00", "to": "2026-11-10T23:00:00+07:00"}
  },
  "meta": {"confidence": "reference"}
}
```

### 3.7 Mục tiêu và tham số

`PUT /goals/sku/NOICHIEN2L` (vai trò lead)

```json
// request
{"kind": "profit", "deadline": "2026-11-12T10:00:00+07:00",
 "constraints": {"max_ad_spend_vnd": 3000000, "min_margin_pct": 5}, "reason": "Giữ biên tới khi hàng về"}
// 200
{"data": {"scope": "sku", "scope_id": "NOICHIEN2L", "version": 8, "kind": "profit", "...": "..."}}
```

`kind`: `profit | sell_target | clear_stock | efficiency`. `sell_target` bắt buộc `constraints.target_units`; `clear_stock` bắt buộc tham số `leftover_value_per_unit`.

`GET /parameters?scope=sku&scope_id=NOICHIEN2L`

```json
{
  "data": [
    {"key": "leftover_value_per_unit", "label": "Giá trị hàng dư sau hạn", "unit": "VND/sp",
     "value": 0, "default": 0, "is_default": true, "min": 0, "max": 690000,
     "scope": "sku", "editable_by": "lead", "source": "seller", "version": 3},
    {"key": "ads_carryover_cost_per_hour_paused", "label": "Chi phí mất đà sau pause", "unit": "VND/giờ",
     "value": 0, "default": 0, "is_default": true, "min": 0, "max": 5000000, "scope": "campaign",
     "editable_by": "lead", "source": "assumption", "note": "Chưa đo được, mặc định là kịch bản"}
  ]
}
```

`PUT /parameters/{key}` — `{"scope":"sku","scope_id":"NOICHIEN2L","value":20000,"reason":"Thanh lý được 20k/sp"}`. Phát `ParameterChanged`.

### 3.8 Mô phỏng

Schema dùng đúng tên ở 05-kinh-te-va-mo-phong: `SimRequest`, `ActionSpec`, `ScenarioResult`, `Recommendation`, `ZoneBar`, `FlipCondition`. Mô phỏng theo **chiến dịch** vì ngân sách dùng chung.

`POST /scenarios/run` — body là `SimRequest`.

```json
// request
{
  "campaign_id": "CD002",
  "start": "2026-11-11T01:00:00+07:00",
  "horizon_hours": 23,
  "goal_id": "goal-cd002-1111",
  "actions": null,
  "param_overrides": {},
  "samples": 1000,
  "seed": 20261111
}
```

- `actions = null` → đủ 5 hành động mặc định. Kéo tham số trên UI gửi `samples=300`; thả tay gửi `samples=1000`.
- `param_overrides` chỉ áp cho lần chạy này, không ghi `parameters`. Key không có trong danh mục hoặc ngoài khoảng → 400 `PARAM_OUT_OF_RANGE`.
- Kết quả được cache theo `(campaign_id, forecast_ids, estimate_id, param_version, goal_id, as_of)` (05). Gọi lại cùng khóa trả ngay, `meta.cache_hit = true`.

```json
// 200
{
  "data": {
    "scenario_id": "scn-2291",
    "results": [
      {"action": {"kind": "continue", "pct": null, "windows_local": null}, "feasible": true, "violations": [],
       "objective": {"mean": -4480000, "p10": -4900000, "p50": -4500000, "p90": -4100000},
       "contribution": {"p50": 1070000}, "spend": {"p50": 5550000},
       "units_sold": {"p50": 83}, "leftover_units": {"p50": 0}, "leftover_value": {"p50": 0},
       "stockout_hour_p50": "2026-11-11T09:00:00+07:00", "per_sku": []},
      {"action": {"kind": "schedule_hours", "pct": null, "windows_local": [[0, 2], [12, 14]]}, "feasible": true, "violations": [],
       "objective": {"mean": -210000, "p10": -400000, "p50": -200000, "p90": -50000},
       "contribution": {"p50": 1060000}, "spend": {"p50": 1250000}, "units_sold": {"p50": 83},
       "stockout_hour_p50": "2026-11-11T13:00:00+07:00", "per_sku": []},
      {"action": {"kind": "pause", "pct": null, "windows_local": null}, "feasible": false,
       "violations": ["campaign_must_run"], "objective": {"p50": 900000}}
    ],
    "recommendation": {
      "recommendation_id": "rec-551", "campaign_id": "CD002", "status": "recommend",
      "best": {"kind": "schedule_hours", "windows_local": [[0, 2], [12, 14]]},
      "runner_up": {"kind": "continue"},
      "p_best_beats_runner_up": 0.99, "expected_gain_vnd": 4300000,
      "zone_bar": {"axis_label": "Lượng bán mỗi giờ nếu pause (sp)", "threshold": 3.1,
                   "interval": [4.2, 5.6, 7.0], "verdict": "pause_better", "alt_thresholds": []},
      "flip_conditions": [{"factor": "restock_delay_days", "flip_at": -1, "text": "Nếu hàng về trong hôm nay, chạy tiếp có lợi hơn"}],
      "confidence": "reference",
      "reasons": ["Hàng sẽ hết khoảng 13:00: ads không bán thêm được khi hết hàng",
                  "Ở giá 11.11 mỗi nồi chỉ lãi 9.280đ",
                  "Không được tắt hẳn vì đã cam kết với sàn trong khung flash sale"],
      "inputs_ref": {"forecast_ids": ["fc-8812", "fc-8813", "fc-8814"], "estimate_id": "ie-37", "param_version": 12, "goal_id": "goal-cd002-1111", "data_as_of": "2026-11-11T01:00:00+07:00"}
    }
  },
  "meta": {"confidence": "reference", "data_as_of": "...", "computed_at": "...", "cache_hit": false}
}
```

Các con số ví dụ là minh họa theo lập luận 05 mục 10 (cùng chiến dịch, nhu cầu vượt tồn, nồi chiên lãi 9.280đ/sp), không phải đầu ra cam kết. `zone_bar` ở đây là thanh continue-so-với-pause; vì `best` là `schedule_hours`, UI ghi thêm "Phương án tốt hơn cả hai: chỉ chạy khung flash sale" (05 mục 7).

`Recommendation.status`: `recommend | undetermined | none_meets_goal`. `none_meets_goal` → UI hiện "Các lựa chọn này chưa giải quyết được mục tiêu". `ZoneBar.verdict`: `pause_better | continue_better | undetermined`.

`POST /scenarios/breakeven` — tính nhanh ngưỡng và trả một `ZoneBar` (ECO-04). Ví dụ trang 10 dùng làm test:

```json
// request
{"units_left": 120, "hours_left": 4, "contribution_per_unit_vnd": 45000, "spend_per_hour_vnd": 450000,
 "rate_continue_per_hour": 40, "pause_rate_interval": [18, 24, 31], "leftover_value_per_unit_vnd": 0}
// 200
{"data": {"zone_bar": {"axis_label": "Lượng bán mỗi giờ nếu pause (sp)", "threshold": 22.5, "interval": [18, 24, 31],
          "verdict": "undetermined", "alt_thresholds": [{"label": "hàng dư 20.000đ", "threshold": 16.5}]},
          "flip_leftover_value_vnd": 16875}}
```

### 3.9 Đề xuất

`GET /recommendations?active=true&sort=priority` → danh sách `Recommendation` (05) hiện hành theo chiến dịch, thêm `scenario_id`, `created_at`, `superseded_by`. Đề xuất cũ bị thay khi có đề xuất mới (`superseded_by`).

### 3.10 Quyết định và hành động

`POST /decisions`

```json
// request
{"target": {"campaign_id": "CD002"}, "action": {"kind": "schedule_hours", "windows_local": [[0, 2], [12, 14]]},
 "scenario_id": "scn-2291", "recommendation_id": "rec-551",
 "rationale": "Hàng hết trong ngày, mỗi nồi chỉ lãi 9.280đ; vẫn chạy khung flash sale theo cam kết", "override_of_recommendation": false}
// 201
{"data": {"id": "dec-77", "state": "draft", "created_by": "u-ops-1", "input_hash": "a91c..."}}
```

Máy trạng thái ở 10-quan-tri-va-bao-mat. Các endpoint chuyển trạng thái:

| Endpoint | Từ → Tới | Kiểm |
|---|---|---|
| `POST /decisions/{id}/submit` | draft → pending_approval | `input_hash` còn khớp dữ liệu hiện tại, nếu không → 409 `STALE_INPUT` |
| `POST /decisions/{id}/approve` | pending_approval → approved | vai trò lead, không phải người tạo; body `{"note": "..."}` |
| `POST /decisions/{id}/reject` | pending_approval → rejected | lead; body `{"reason": "..."}` bắt buộc |
| `POST /decisions/{id}/cancel` | draft/pending_approval → cancelled | người tạo |
| `POST /decisions/{id}/actions` | approved → sent | tạo `actions` (chế độ `simulated`), body `{"mode":"simulated"}` |
| `POST /actions/{id}/verify` | sent → verified/failed | body `{"observed_status":"Đang chạy","observed_budget_vnd":...}` hoặc tự đối chiếu dữ liệu ads mới |

`POST /decisions/{id}/actions` với hành động vi phạm cam kết → 409 `CONSTRAINT_VIOLATION`.

### 3.11 Cảnh báo

`GET /alerts?status=open`

```json
{
  "data": [
    {"id": "al-31", "kind": "sales_below_forecast", "severity": "high",
     "target": {"sku": "AOGIUNHIET-M", "product": "Áo giữ nhiệt nam"},
     "title": "Áo giữ nhiệt bán thấp hơn dự báo 3 giờ liền",
     "impact_vnd": 1850000, "raised_at": "2026-11-11T13:04:00+07:00", "status": "open",
     "group_key": "AOGIUNHIET", "investigation": null}
  ]
}
```

`kind`: `stockout_risk, sales_below_forecast, sales_above_forecast, budget_exhausted, data_stale, data_blocking, recommendation_changed, compute_failed, ai_policy`. `POST /alerts/{id}/investigate` → chạy agent điều tra (07-lop-ai mục 4.3), trả 202, kết quả qua SSE `alert.investigated`.

### 3.12 AI

`POST /ai/chat` — trả về `text/event-stream`.

```json
// request
{"session_id": null, "flow": "whatif", "message": "Nếu hàng nồi chiên về sớm 1 ngày thì sao?",
 "context": {"campaign_id": "CD002", "sku": "NOICHIEN2L", "screen": "compare"}}
```

Các event SSE trả về theo thứ tự:

```
event: session      data: {"session_id":"ais-901"}
event: tool_call    data: {"tool":"get_sku_snapshot","args":{"sku":"NOICHIEN2L"}}
event: tool_result  data: {"tool":"get_sku_snapshot","source":{"id":"snap-NOICHIEN2L-1459","label":"..."}}
event: tool_call    data: {"tool":"run_scenario","args":{...}}
event: tool_result  data: {"tool":"run_scenario","source":{"id":"scn-2292","label":"..."}}
event: answer       data: {"text":"Nếu hàng về trong hôm nay thì chạy tiếp cả ngày có lợi hơn chỉ chạy khung flash sale [[src:scn-2292]]. Đây là giả định, hàng hiện dự kiến về 12/11 [[src:snap-NOICHIEN2L-0100]] ...",
                           "citations":[{"id":"scn-2292","kind":"scenario","label":"..."}],
                           "grounding":{"ok":true,"checked":6,"matched":6},
                           "drafts":[{"kind":"decision","id":null,"suggested":null}]}
event: done         data: {"input_tokens":4120,"output_tokens":380}
```

Câu trả lời không stream từng chữ, vì phải qua grounding check trước. UI hiện tiến trình bằng các event `tool_call`.

`POST /ai/handoff` — `{"from":"2026-11-11T06:00:00+07:00","to":"2026-11-11T14:00:00+07:00"}` → nháp ghi chú bàn giao.

### 3.13 Live

`GET /live/status`

```json
{"data": {"enabled": true, "day": "2026-11-11", "orders_until": "14:59", "ads_until": "12:59",
          "last_tick_at": "2026-11-11T15:02:40+07:00", "replay_running": true}}
```

`POST /live/control` (chỉ khi `DEMO_MODE=true`): `{"command":"start","from_hour":9,"interval_seconds":10}` | `{"command":"stop"}` | `{"command":"reset"}`. Backend chạy `node mock-data/replay.js` bằng subprocess. Dùng để người demo bấm nút trên UI thay vì gõ lệnh.

---

## 4. Luồng SSE `/stream`

Một kết nối cho cả app. Mỗi event có `id` tăng dần; client gửi lại `Last-Event-ID` khi kết nối lại để nhận phần bị lỡ (giữ 500 event gần nhất trong bộ nhớ).

| event | data | UI làm gì |
|---|---|---|
| `live.tick` | `{orders_until, ads_until}` | cập nhật đồng hồ dữ liệu |
| `freshness.changed` | như `/freshness` | đổi huy hiệu độ mới |
| `ingest.batch_loaded` | `{batch_id, rows, issues}` | thông báo "Đã nạp xong" |
| `compute.state` | `{sku, state, computed_at}` | nhãn "Đang cập nhật" |
| `recommendation.changed` | `{sku, recommendation}` | invalidate query SKU, nhấp nháy dòng |
| `alert.raised` / `alert.resolved` | bản ghi alert | toast + đếm cảnh báo |
| `alert.investigated` | `{alert_id, investigation}` | hiện kết quả điều tra |
| `decision.changed` | `{id, state}` | cập nhật nhật ký |
| `heartbeat` | `{}` mỗi 15 giây | giữ kết nối |

Frontend dùng `EventSource` + map event → `queryClient.invalidateQueries(...)` của TanStack Query (09-giao-dien).

---

## 5. Chạy và kiểm

- Chạy: `uvicorn app.main:app --reload --port 8000`. Frontend proxy `/api` → `localhost:8000`.
- CORS chỉ mở cho `http://localhost:5173` khi dev.
- Contract test: mỗi endpoint có test so phản hồi với schema Pydantic và ví dụ trong file này (11-kiem-thu-va-cham-diem).

---

## 6. Ghi chú triển khai (M3, API đợt 2)

Đã làm API-01, API-03 (phần sàn), API-04, API-05, ECO-02. Chi tiết, lý do và ví dụ phản hồi thật nằm ở [docs/quyet-dinh/M3-api.md](../quyet-dinh/M3-api.md). Những thay đổi so với mục 1–3 (đều là thêm, không phá vỡ):

- **Xác thực:** thiếu `X-Role` thì tính là `viewer`; `X-Role` lạ trả 401.
- **Phân trang:** danh sách trả `{data: [...], meta, next_cursor}`. `meta` có thêm `cache_hit` (chỉ dùng ở `/scenarios/run`). `Source` có thêm `data_as_of`, `tool`, `href`.
- **`/freshness`:** thêm `clock`. `DEMO_MODE` dùng đồng hồ dữ liệu, `?now=` để so với mốc khác.
- **Đường dẫn bí danh:** `PUT` và `PATCH /skus/{sku}/demand-group`; `/forecast/scores` = `/models/scores`; `POST /goals` cùng `PUT /goals/{scope}/{scope_id}`; `GET /goals/{goal_id}`; `POST /parameters/{key}/confirm` (xác nhận giá trị AI gợi ý); `/data-quality` (tóm tắt) cùng `/data-quality/issues`; panel nhận `start/end` hoặc `from/to`, và `grain=day`.
- **`/scenarios/run`:**
  - `goal_id` không bắt buộc: thiếu thì dùng mục tiêu đang hiệu lực, không có thì 422 `BLOCKING_DATA_ISSUE`.
  - Thêm `disable_skus` cho what-if "tắt riêng SKU": 409 `CONSTRAINT_VIOLATION` khi ngân sách dùng chung.
  - Thiếu giá vốn → 422.
  - Phản hồi `ScenarioRun` có `request` (đã điền `start`, `horizon_hours`), `results[].key/label`, `recommendation.reason_sources[]`, `best_label`, `active`.
  - Lần chạy có `param_overrides`, `session_id` hoặc `actions` riêng là what-if: vẫn lưu và trả kết quả, nhưng đề xuất mang `what_if = true` và không thay đề xuất đang hiệu lực. `results[].duplicate_of`. `/skus` có `meta.total`.
  - `zone_bar.inputs` (= body `/scenarios/breakeven`) chỉ có khi tái tạo đúng ngưỡng.
- **`/scenarios/breakeven`:** thêm `pi_continue_vnd`, `status`, `q_star_units`, `reason`. Body nhận `alt_leftover_values`.
- **`/campaigns/{id}`:** thêm `price_max_vnd` (max thanh trượt V), `sku_detail`, `goal`, `commitments`. `/campaigns/{id}/iroas` có `iroas.p50` = trung bình hậu nghiệm.
- **`/ingest/files`:** nạp trực tiếp (chưa có bước AI ghép cột), `status` ∈ `loaded | needs_sku_review | skipped | failed | unsupported`. `POST /ingest/batches/{id}/confirm` nạp lại file sau khi chốt alias.

## Tiêu chí hoàn thành

- [ ] Mọi endpoint ở bảng mục 2 có router, schema Pydantic, kiểm vai trò.
- [ ] Mọi phản hồi có số có `meta.data_as_of`, `computed_at`, `compute_state`, `confidence`.
- [ ] Mã lỗi đúng bảng; `CONSTRAINT_VIOLATION` chặn tắt CD002 trong 12:00–14:00 ngày 11.11.
- [ ] `/ai/chat` stream đúng thứ tự event; `/stream` hỗ trợ `Last-Event-ID`.
- [ ] OpenAPI sinh client TypeScript chạy được ở frontend.
- [ ] Contract test xanh cho mọi endpoint.

## Task gợi ý

Người, ước lượng và ưu tiên theo 12-lo-trinh-va-phan-viec. `GET /health` và sinh type OpenAPI cho frontend thuộc INF-01/INF-05.

| Mã | Việc | Người | Người-ngày | Ưu tiên | Phụ thuộc |
|---|---|---|---|---|---|
| API-01 | Khung router, middleware user/role, khung phản hồi + lỗi, freshness | D3 | 0,5 | P0 | INF-02 |
| API-03 | Router ingest (files, batches, sku-queue, bundles) + data-quality | D4 | 0,75 | P0 | ING-08, WH-02, API-01 |
| API-04 | Router skus, campaigns, panel, forecast, scores, iroas | D3 | 0,5 | P0 | FC-09, ADS-04 |
| API-05 | Router goals, parameters, scenarios, breakeven, recommendations | D3 | 0,5 | P0 | SIM-05, ECO-02, ECO-04 |
| API-06 | Router decisions, actions, alerts | D4 | 0,5 | P0 | GOV-01, ORC-05 |
| API-07 | SSE `/stream` + `/ai/chat` + live status/control (mức sàn: `/ai/chat` trả JSON một lần, không stream) | D3 | 0,5 | P0 | ORC-01, AI-01, LIVE-01 |
| EVAL-04 | Contract test mọi endpoint | D3 | 0,5 | P1 | API-03..07 |


## Hosted account workspace extension

The same-origin city adds `/api/auth/{login,logout,session}`, revisioned
`GET/PUT /api/workspace/{sources,settings,sandbox,chats}` and authenticated
WebSocket `/api/workspace/events`. These routes sit outside `/api/v1` and do not
change Python governance identities or permissions. The server assigns the
shared `admin` account; clients cannot choose a tenant. Each document returns
`{revision, updatedAt, data}`; PUT requires `{revision, data}` and returns 409 on
stale writes. WebSocket messages contain `{name, revision}` and trigger refetch.

Settings add `appearance: "morning" | "night"`. Omitted values default to `morning` for existing accounts. This saved view preference changes city lighting and UI colors only; it does not change the sandbox hour.

`GET /api/pm/models` returns models accessible to the configured key.
`POST /api/pm/chat` accepts `{sessionId, lastMessageId, changeReference?}`, loads saved messages,
and persists the reply before returning. It does not accept client-supplied conversation history. Failed generation leaves the saved user message available
for retry. See [account workspace](../city-demo/account-workspace.md) for schema
bounds, synchronization and frontend/backend boundaries.

### In-app workspace change context

`changeReference` is optional and strict: `{kind, sourceRevision, sandboxRevision, hour, source?, entity?, sourceName?, before, after}`. Allowed kinds are `source`, `stock-zero`, `stock-unknown`, `stock-recovered`, `diagnostics`, `diagnostics-resolved`, `orders`, `campaigns`, and `clock`. Revisions are nonnegative integers; hour is 0–24; source uses the six business source kinds or `general`; general tables are identified by `sourceName` (up to 1,000 characters). Entity and before/after string values are limited to 200 characters. Before/after accept a finite number, bounded string, or null. Unknown fields return 422; the complete request is limited to 3,000 characters (413 otherwise).

The authenticated server resolves current stock/counts/source diagnostics from account documents. Historical before/after values remain explicitly unverified; revision differences mark the reference stale. No client-supplied chat history is accepted. This adds no database schema, account document, or WebSocket event. Monitoring and Explain alone make no AI call; Send retains the existing rate-limited `/api/pm/chat` flow.
## Data Studio Worker APIs (2026-10-31)

These account-scoped routes run in the Cloudflare Worker, separately from the
Python `/api/v1` analytics API:

- `POST /api/studio/import`: raw ZIP, maximum 20 MiB compressed and expanded.
  Returns `{ tables: [{ file, source, rowCount, method, columns }], skipped, model }`.
  Analysis does not persist changes; the reviewed selection is saved through
  the existing revision-checked `PUT /api/workspace/sources` contract.
- `GET /api/product-labels`: inventory candidates and the latest persisted run.
- `POST /api/product-labels`: `{ sourceRevision, context: { market, asOf,
  evidence: [{ productCode, observedAt, source, observation }] } }`.
  Demand labels are `Trendy | Seasonal | Normal | null`. Null requires a reason
  explaining missing evidence. Context, provider/prompt version and source
  fingerprint accompany each run. Labels remain separate from forecast groups.

Authentication and same-origin checks apply. Model inputs are untrusted data.
See `web/README.md` for configuration, limits, failure semantics and validation.
## Sale goal planner extension (Task 1)

Worker routes require the existing authenticated account; POSTs require the
same-origin mutation guard. Backend identity remains server-configured through
`DATA_BACKEND_USER/ROLE`; it is never accepted from request JSON.

- `GET /api/sale-planner/context`: `{backendConfigured, canCreateGoals, canPreview, canApply,
  referenceTime, dataAsOf, campaigns:[{id,name}], notice}`. Checks backend
  capabilities and `me` permissions; reads up to 2,000 campaigns with pagination.
- `POST /api/sale-planner/interpret`: `{text}` (1–4,000 characters) returns
  `{draft, method:"ai"|"rules", notices}`. Uses the PM rate limit. No goal or
  scenario is created. AI failure produces an explicit rules fallback.
- `GET /api/sale-planner/active?campaignId=...`: returns
  `{campaignId,activeGoal,baseGoal:{goal_id,version}|null}` for review.
- `POST /api/sale-planner/run`: `{campaignId,draft,confirmed:true,
  acknowledgedUnresolved,expectedGoal:{goal_id,version}|null,focusSku?}` now
  creates only an isolated preview. The former save-and-run contract is rejected.
  Response: `{previewId,goalId,baseGoal,effectiveGoal,changes,notices,canApply,
  appliedGoalId,scenario:Envelope<ScenarioRun>}`. Here `goalId` is an inactive draft;
  the campaign's active goal and recommendation remain unchanged.
- `POST /api/sale-planner/apply`: `{previewId,confirmed:true,expectedGoal}` applies
  the stored preview, returning `{previewId,goal,alreadyApplied}`. No editable
  draft or client numerical results are accepted at apply time. A changed baseline
  or stale inputs returns 409 and requires a fresh preview. Uncertain network
  outcomes retain the preview ID for an idempotent retry.

Errors contain `{error:{code,message,details?},goalId:null,previewId:null|string,
stage:"validation"|"preview"|"apply"}`. Missing data or a failed preview cannot
activate its goal. Operators can preview; only leads can apply.

`GoalDraft` includes the original `text`, nullable `kind` (`profit`, `sell_target`,
`clear_stock`, `efficiency`), `targetUnits`, `start`, `deadline`, `maxSpendVnd`,
`maxLossVnd`, `sellTargetProbability`, `minMarginalReturn`, and `unresolved:string[]`.
Run requires absolute timezone-qualified start/deadline on whole hours, duration
1–72 hours, a selected kind, positive integer target for sell_target, and explicit
acknowledgement of excluded requirements. Probability/target apply only to
sell_target; marginal return only to efficiency. Inapplicable supplied fields are
rejected, never silently dropped. Numeric limits must be finite and nonnegative;
probability is in (0,1]. Null means not explicitly supplied; zero is meaningful.

Python adds authenticated `GET /api/v1/goal-planner/capabilities`, returning
`{data:{version:1,constraints:["max_spend_vnd","max_loss_vnd","sell_target_prob",
"min_marginal_return","focus_sku","calculation_trace","preview_apply"]},meta}`.
Worker refuses unsupported backends before writes. Migration 6 stores preview
snapshots; constraints continue to use existing goal `params`.
`max_spend_vnd` caps total simulated spend over the requested horizon, including
efficiency fallback candidates. `max_loss_vnd` bounds expected J for every kind;
clear_stock keeps its existing default when absent. Existing goal API validates
numeric parameters and rejects conflicting constraints/params values.

Goal params retain `planner_source_text`, `planner_excluded_requirements`,
`planner_start`, plus server-owned preview/parent provenance. Python endpoints:
`GET /api/v1/sale-plans/context?campaign_id=...`, `POST /sale-plans/preview`,
`GET /sale-plans/{preview_id}`, and `POST /sale-plans/{preview_id}/apply`.
Preview accepts `{campaign_id,goal:{kind,deadline,target_units,params},start,
horizon_hours,samples,seed,expected_goal,focus_sku?}`. Apply accepts only
`{confirmed:true,expected_goal}`. Responses use the standard Envelope and
snake_case fields. Preview uses an inactive goal and what-if session; apply checks
the stored base token under the same lock used by legacy goal writers, then changes
the active goal and records application atomically. Repeating a successful apply
returns that goal, unless it has since been replaced (409).

SKU previews inherit the tighter campaign budget/loss limits. Profit plus an SKU
quantity target retains J ranking subject to the quantity requirement; unsupported
priority/scope changes are visible but cannot be applied. No joint multi-SKU budget
optimization is implied. Input freshness checking is conservative: source/model
changes elsewhere in the warehouse can also require a fresh preview.
Scenario runs use 1,000 samples and seed 20261111. No fixture fallback or ad execution.

## SKU goal assistant and calculation evidence

The authenticated Worker adds:

- `GET /api/sale-assistant/skus?q=...&cursor=...`: real backend SKU search,
  `{items:[{sku,name,variant,stockAvailable}],nextCursor,dataAsOf}`.
- `GET /api/sale-assistant/context?sku=...`: `{sku,campaigns,dataAsOf,source,
  canPlan,notices,opening}`. SKU facts include stock, today's units, effective
  price, contribution per unit and demand group; unknown numeric values stay
  null. Campaigns contain all discovered memberships with `{id,name,skus}`.
- `POST /api/sale-assistant/turn`: `{sku,messages:[{role,content}]}` returns
  `{reply,interpretation:{draft,method,notices},context}`. Conversation is bounded;
  user requirements are interpreted into an editable draft. Assistant messages
  are not accepted as a source of facts or authorization. No goal or scenario
  is saved by this endpoint. Session, same-origin writes and PM rate limit apply.

`POST /api/sale-planner/run` now accepts optional `focusSku`. The gateway verifies
membership and backend `focus_sku`/`preview_apply` capabilities before previewing;
the selected SKU is stored as `params.focus_sku` in the inactive draft.
Python also validates membership, including direct API callers. For sell_target
and clear_stock the quantity metric is the selected SKU's sold units. Budgets,
loss limits and J remain campaign-wide; profit/efficiency optimize the campaign.
`ScenarioResult.goal_sku` and `goal_units_sold` identify this quantity separately
from the unchanged aggregate `units_sold`.

`ScenarioRun.calculation_trace` is a versioned snapshot made from the actual
simulation inputs and samples, persisted with its scenario. Legacy scenarios
without a snapshot return null; reading an old result must not rebuild evidence
from today's data. Trace version 1 includes:

- Scenario input hash, campaign/focus SKU, quantity/economic scopes, requested
  window, sample count and seed, data timestamp and panel version.
- Confirmed goal, parameter values/versions, forecast references, iROAS origin
  (estimate or prior), and import batch references where available.
- SKU prices, costs, per-unit economics, starting stock, restocks and demand
  inputs. Unknown stock is labelled as a modelling assumption, not real stock.
- Equations and each action's actual distributions; expected-value substitution
  for J = contribution − spend + terminal inventory value − shortage penalty,
  with a reconciliation residual. Quantiles are calculated from samples;
  adding component P10/P50/P90 values does not produce J's quantiles.
- Explicit limitations. Batch references identify source context and are not
  represented as complete lineage to every original CSV row.

Capability version 1 additionally advertises `focus_sku` and `calculation_trace`.
This is additive: old campaign-only planner callers remain valid.

### Milo conversation context and goal review

`POST /api/pm/chat` additionally accepts optional `context` containing identifiers
only: `{screen?, sku?, campaignId?, scenarioId?}`. Chat sessions persist the same
optional context. Existing sessions remain valid. SKU, campaign and scenario
scope must match the saved session; a different scope requires another session
(409 on mismatch). Unknown context properties are rejected (422). The server
always loads saved messages, never client-supplied history or numerical facts.

Milo resolves current account source checks and sandbox observations, selected
SKU facts and recent daily sales, campaign membership and budget, and matching
immutable scenario evidence when a scenario ID is supplied. Missing data yields
explicit notices, never fixture values. Sandbox observations remain separate
from backend facts. A trace must match the selected scope and scenario input
hash. Returned evidence is bounded and marks truncated results.

The goal interpreter considers the saved conversation (up to 200 messages),
including corrections to earlier requirements. Only user turns establish
requirements; assistant suggestions are not acceptance. AI output is validated
against a structured schema; unavailable or invalid interpretation falls back
to conservative rules with a visible notice. Recent 16 turns and the interpreted
draft inform Milo's natural-language reply. Absolute dates remain subject to
review; no relative sale date is silently guessed.

Successful replies can include `plan: {interpretation, context}` for the selected
SKU, or `campaignPlan: {campaignId,name,interpretation}` for a verified selected
campaign. Both open the same preview/review form. This is a draft, not an active goal or permission
to run a scenario. Chat performs no backend mutations. Review still requires a
campaign choice, valid time window, unresolved-item acknowledgement and explicit
confirmation. A stale conversation or changed scope during generation prevents
saving the late reply (409). Failed calls retain the user's saved message for retry.
