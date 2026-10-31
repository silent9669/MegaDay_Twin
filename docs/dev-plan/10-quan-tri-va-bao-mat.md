# 10 · Quản trị, duyệt và bảo mật

Mọi quyết định đi qua một máy trạng thái có người duyệt, được ghi lại đủ để tái lập: ai chọn, dựa trên dữ liệu nào, model và tham số phiên bản nào. Bản hackathon **chỉ mô phỏng thao tác trên sàn** (chế độ `simulated`), luôn gắn nhãn rõ. Dữ liệu cá nhân người mua bị bỏ ngay khi nhập và không bao giờ gửi cho OpenAI.

Module: `backend/app/governance/` (roles, decisions, actions, versions, audit) và phần bảo mật trong `backend/app/core/`.

---

## 1. Vai trò và quyền

Hackathon dùng 3 người dùng cố định, chọn ở màn đăng nhập giả (không mật khẩu). Gửi `X-User`, `X-Role` theo mọi request (08-api).

| Người dùng mẫu | Vai trò | Ghi chú |
|---|---|---|
| `u-ops-1` Lan | operator (vận hành) | người demo chính |
| `u-lead-1` Hà | lead | duyệt quyết định, sửa mục tiêu |
| `u-view-1` Minh | viewer | chỉ xem |

| Quyền | viewer | operator | lead |
|---|---|---|---|
| Xem mọi màn hình, số liệu | ✓ | ✓ | ✓ |
| Tải file, xác nhận ghép cột/SKU | | ✓ | ✓ |
| Chạy mô phỏng, hỏi AI | | ✓ | ✓ |
| Tạo, gửi duyệt, hủy quyết định của mình | | ✓ | ✓ |
| Duyệt / từ chối quyết định của người khác | | | ✓ |
| Tự duyệt quyết định của chính mình | | | Không (kể cả lead) |
| Sửa mục tiêu | | | ✓ |
| Sửa tham số `editable_by = operator` | | ✓ | ✓ |
| Sửa tham số `editable_by = lead` | | | ✓ |
| Gửi thao tác (chế độ simulated) sau khi đã duyệt | | ✓ | ✓ |
| Điều khiển demo, nạp sẵn dữ liệu | | | ✓ (chỉ khi `DEMO_MODE=true`) |

Kiểm quyền ở **backend** (dependency FastAPI `require_role(...)`), frontend chỉ ẩn/xám nút cho dễ dùng.

Tham số và mục tiêu luôn do người chốt (05 mục 3, 4): tham số AI gợi ý có `set_by='ai_suggested'` và chưa có hiệu lực cho tới khi operator/lead xác nhận; `goals.confirmed_by` bắt buộc là một người dùng thật.

Nguyên tắc "lead chỉ duyệt đánh đổi hợp lệ" (tài liệu gốc trang 11): nút Duyệt bị khóa khi quyết định dựa trên dữ liệu có issue `blocking` chưa giải quyết, hoặc `input_hash` đã cũ. Lead phải yêu cầu tính lại trước.

---

## 2. Máy trạng thái quyết định

```
                 submit (input_hash còn khớp)
   ┌───────┐ ─────────────────────────────▶ ┌──────────────────┐
   │ draft │                                │ pending_approval │
   └───┬───┘ ◀──── reopen (người tạo) ───── └──┬──────┬────────┘
       │ cancel                       approve │      │ reject (bắt buộc lý do)
       ▼                              (lead,  │      ▼
  ┌───────────┐                       ≠ người │  ┌──────────┐
  │ cancelled │                         tạo)  │  │ rejected │
  └───────────┘                               ▼  └──────────┘
                                        ┌──────────┐
                     expire (quá hạn ───│ approved │
                     hiệu lực, vd 2 giờ)└────┬─────┘
                          ▼                  │ send action (mode = simulated)
                    ┌─────────┐              ▼
                    │ expired │         ┌────────┐
                    └─────────┘         │  sent  │
                                        └──┬──┬──┘
                         verify khớp       │  │  verify không khớp / lỗi gửi
                                           ▼  ▼
                                  ┌──────────┐ ┌────────┐
                                  │ verified │ │ failed │
                                  └──────────┘ └────────┘
```

| Chuyển | Ai | Điều kiện | Ghi |
|---|---|---|---|
| (tạo) → `draft` | operator, lead, AI (qua `draft_decision`, `created_via='ai'`) | có `scenario_id` hợp lệ | — |
| `draft` → `pending_approval` | người tạo | `input_hash` khớp dữ liệu hiện tại; không có issue `blocking` | `submitted_at` |
| `pending_approval` → `approved` | lead ≠ người tạo | như trên + hành động không vi phạm ràng buộc | `approved_by`, `approval_note` |
| `pending_approval` → `rejected` | lead | lý do bắt buộc | `reject_reason` |
| `pending_approval` → `draft` | người tạo | — | (reopen) |
| `draft`/`pending_approval` → `cancelled` | người tạo | — | — |
| `approved` → `expired` | hệ thống | quá `valid_until` (mặc định 2 giờ, tham số `decision_validity_hours`) | — |
| `approved` → `sent` | operator, lead | còn hiệu lực | tạo bản ghi `actions` với `mode='simulated'` |
| `sent` → `verified` | hệ thống hoặc người | dữ liệu ads giờ sau cho thấy trạng thái/ngân sách khớp hành động | `verified_at`, `observed` |
| `sent` → `failed` | hệ thống hoặc người | không khớp sau 3 giờ dữ liệu ads, hoặc lỗi gửi | `failure_reason` |

Ba trạng thái **đã duyệt, đã gửi, đã kiểm lại** luôn tách riêng trên UI (tài liệu gốc trang 11). Lỗi giữa chừng giữ trạng thái chưa xác nhận, không tự đẩy sang `verified`.

Cài đặt: `governance/decisions.py` với hàm `transition(decision_id, event, actor) -> Decision`, bảng chuyển hợp lệ khai báo dạng dict; chuyển không hợp lệ ném `StateConflict` (→ HTTP 409).

### Kiểm lại thao tác (`verify`)

Với `mode='simulated'`, backend tự kiểm khi có dữ liệu ads mới:
- `pause` / `pause_until_restock`: các giờ sau `sent_at` có `Trạng thái = Tạm dừng` hoặc chi = 0.
- `reduce_budget(x)`: chi các giờ sau ≤ (1 − x%) × mức chi kỳ vọng (dung sai 15%).
- `schedule_hours`: chi > 0 chỉ trong khung giờ đã chọn.

Vì mock data không phản ứng theo quyết định của app, bản demo cho phép **người bấm "Đánh dấu đã kiểm lại"** với ghi chú, ghi `verified_by_human=true`. Báo cáo luôn tách hai loại kiểm.

---

## 3. Bản ghi quyết định

```sql
CREATE TABLE decisions (
  id VARCHAR PRIMARY KEY,                -- dec-77
  kind VARCHAR,                          -- ad_action | goal_change | handoff_note
  target_kind VARCHAR, target_id VARCHAR,  -- campaign / sku
  action JSON,                           -- ActionSpec, vd {"kind":"schedule_hours","windows_local":[[0,2],[12,14]]}
  state VARCHAR,
  rationale VARCHAR,
  override_of_recommendation BOOLEAN,    -- người chọn khác đề xuất?
  recommendation_id VARCHAR, recommended_action JSON,
  scenario_id VARCHAR,
  -- bối cảnh tại thời điểm chọn (để tái lập)
  goal_snapshot JSON,                    -- mục tiêu + version
  parameters_snapshot JSON,              -- {key: {value, version}}
  model_versions JSON,                   -- {"forecast":"seasonal_hourly@2026-11-11.1","iroas":"did@3"}
  data_as_of JSON,                       -- {"orders":"...","ads":"...","inventory":"..."}
  input_hash VARCHAR,
  expected_outcome JSON,                 -- kết quả mô phỏng của hành động được chọn (p10/p50/p90)
  open_issues JSON,                      -- issue dữ liệu đang mở lúc chọn
  created_by VARCHAR, created_via VARCHAR,   -- ui | ai
  created_at TIMESTAMPTZ, submitted_at TIMESTAMPTZ,
  approved_by VARCHAR, approved_at TIMESTAMPTZ, approval_note VARCHAR,
  reject_reason VARCHAR, valid_until TIMESTAMPTZ,
  -- sau ca
  observed_outcome JSON,                 -- điền khi đánh giá lại
  evaluation JSON                        -- sai số dự báo, regret ước lượng (11-kiem-thu)
);

CREATE TABLE actions (
  id VARCHAR PRIMARY KEY, decision_id VARCHAR,
  mode VARCHAR,                          -- simulated (hackathon) | live (sau này)
  payload JSON, sent_by VARCHAR, sent_at TIMESTAMPTZ,
  state VARCHAR,                         -- sent | verified | failed
  verified_at TIMESTAMPTZ, verified_by_human BOOLEAN, observed JSON, failure_reason VARCHAR
);

CREATE TABLE alerts (
  id VARCHAR PRIMARY KEY,                -- al-31
  shop_id VARCHAR, kind VARCHAR,         -- stockout_risk | sales_below_forecast | sales_above_forecast | drift | budget_exhausted | data_stale | data_blocking | recommendation_changed | compute_failed | ai_policy
  severity VARCHAR,                      -- high | medium | low
  target_kind VARCHAR, target_id VARCHAR, group_key VARCHAR,
  title VARCHAR, detail JSON, impact_vnd BIGINT,
  status VARCHAR,                        -- open | acknowledged | snoozed | resolved | hidden_by_budget
  raised_at TIMESTAMPTZ, resolved_at TIMESTAMPTZ, snoozed_until TIMESTAMPTZ,
  acknowledged_by VARCHAR, source_event_id VARCHAR,
  investigation JSON                     -- kết quả agent điều tra (07 mục 4.3)
);
```

Ghi chú bàn giao ca (`kind = 'handoff_note'`) dùng cùng bảng, `action = null`, nội dung trong `rationale`.

---

## 4. Phiên bản và tái lập

| Đối tượng | Cách đánh phiên bản | Lưu ở |
|---|---|---|
| Dữ liệu | `ingest_batches.id` tăng dần; mỗi bảng nghiệp vụ có `batch_id`; snapshot theo `as_of` | 02-lop-du-lieu |
| Model dự báo | `model_registry(model_id, version, trained_at, train_window, params, scores)`; version = `YYYY-MM-DD.n` | 03-lop-mo-hinh |
| Ước lượng iROAS | `iroas_estimates(version, method, window, ...)` | 04-hieu-qua-ads |
| Mục tiêu | `goals.version` tăng mỗi lần sửa, giữ bản cũ (`valid_from`, `valid_to`) | 05-kinh-te-va-mo-phong |
| Tham số | `parameters.version` tăng mỗi lần sửa, giữ lịch sử, có `set_by` | 05-kinh-te-va-mo-phong |
| Kịch bản | khóa cache `(campaign_id, forecast_ids, estimate_id, param_version, goal_id, as_of)` + seed | 05-kinh-te-va-mo-phong |

Yêu cầu: với một `decision_id`, chạy `python -m app.governance.replay dec-77` phải tính lại được `expected_outcome` giống hệt (cùng seed). Đây là test tự động (11-kiem-thu-va-cham-diem).

---

## 5. Shadow mode

Chế độ app chỉ đề xuất, người vẫn làm theo cách cũ, để so sánh mà không rủi ro.

- Bật bằng tham số shop `shadow_mode=true`.
- Mỗi đề xuất được ghi vào `recommendations` như thường. Không tạo quyết định.
- Sau ca, job `evaluate_shift` so: hành động thật seller đã làm (đọc từ trạng thái ads) với hành động app đề xuất, và kết quả quan sát với `expected_outcome` của cả hai nhánh (nhánh không xảy ra chỉ có giá trị mô phỏng, ghi rõ).
- Với mock data, "hành động thật" = hành vi seller có sẵn trong `bao_cao_ads.csv`. Báo cáo shadow trên đợt 10.10 là một phần của slide chứng minh (11-kiem-thu-va-cham-diem).

---

## 6. Dữ liệu cá nhân (PII)

| Bước | Quy tắc |
|---|---|
| Nhập | Adapter bỏ cột `Người mua`, `Số điện thoại`, `Địa chỉ nhận hàng` **trước khi ghi DuckDB**. Ghi issue `PII_DROPPED` (thông tin, không phải lỗi) |
| Ghi chú người mua | Giữ (cần cho agent điều tra), nhưng chạy bộ lọc che số điện thoại `0\d{9,10}`, email, chuỗi giống địa chỉ (`Số \d+ Đường`) → `[đã che]` |
| Gửi OpenAI | Chỉ gửi: tên cột, dòng mẫu đã che, số liệu tổng hợp, ghi chú đã che và bọc untrusted. Không gửi file thô |
| Log | `ai_tool_calls.args/result_summary` không chứa PII (đã che từ nguồn) |
| Test | Test tự động: quét DuckDB và mọi payload gửi OpenAI (bản ghi `AI_MODE=record`) không chứa chuỗi `Khách Test` hoặc mẫu `0900\d{6}` |

Ghi chú pháp lý: Việt Nam có quy định về bảo vệ dữ liệu cá nhân. Đội nên nêu trong pitch rằng hệ thống không lưu thông tin người mua. Phần pháp lý chi tiết cần người phụ trách tự kiểm.

---

## 7. Bí mật và cấu hình

- `OPENAI_API_KEY` chỉ ở `.env` của backend. Không bao giờ gửi xuống frontend. `.env` trong `.gitignore`; repo có `.env.example` không có giá trị thật.
- Frontend gọi AI qua backend (`/api/v1/ai/chat`), không gọi OpenAI trực tiếp.
- Nếu lỡ commit key: thu hồi key ngay trên trang OpenAI, tạo key mới. Xóa khỏi lịch sử git là chưa đủ.
- Biến môi trường chính:

```
OPENAI_API_KEY=
OPENAI_MODEL_REASONING=
OPENAI_MODEL_FAST=
OPENAI_MODEL_EMBED=
AI_ENABLED=true
AI_MODE=live            # live | record | replay
AI_DAILY_TOKEN_BUDGET=2000000
AI_SESSION_TOKEN_BUDGET=50000
DUCKDB_PATH=data/megaday.duckdb
DEMO_MODE=true
LIVE_ENABLED=true
LIVE_ROOT=../mock-data/out/demo/live_stream
MOCK_DATA_ROOT=../mock-data/out/demo
```

---

## 8. Cách ly dữ liệu

Hackathon chỉ có một shop, nhưng thiết kế sẵn cho nhiều shop để khỏi viết lại:
- Mọi bảng nghiệp vụ có cột `shop_id` (mặc định `shop-demo`).
- Mọi truy vấn đi qua repository nhận `shop_id` từ ngữ cảnh request; không truy vấn thô từ router.
- Cache (StageCache, AI cache) có `shop_id` trong khóa.
- Bộ nhớ hội thoại AI theo `session_id` gắn `shop_id` và `user_id`.

---

## 9. Log audit

```sql
CREATE TABLE audit_log (
  id VARCHAR PRIMARY KEY, at TIMESTAMPTZ, shop_id VARCHAR,
  actor VARCHAR, actor_role VARCHAR, via VARCHAR,     -- ui | ai | system
  action VARCHAR,          -- decision.submit, decision.approve, goal.update, parameter.update, ingest.confirm, live.control...
  target_kind VARCHAR, target_id VARCHAR,
  before JSON, after JSON, request_id VARCHAR
);
```

- Ghi mọi thao tác ghi (POST/PUT/PATCH thành công), mọi chuyển trạng thái quyết định, mọi lần AI tạo bản nháp.
- Không ghi PII, không ghi API key.
- Màn Nhật ký quyết định đọc `decisions`; bảng `audit_log` xem qua `GET /api/v1/audit` (lead) nếu còn thời gian.

---

## Tiêu chí hoàn thành

- [ ] `require_role` chặn đúng ma trận quyền; test cho mỗi ô "không".
- [ ] Máy trạng thái đúng bảng; chuyển không hợp lệ trả 409; lead không tự duyệt được.
- [ ] Submit/approve bị chặn khi `input_hash` cũ hoặc có issue `blocking`.
- [ ] `decisions` lưu đủ snapshot; `governance.replay` tái lập `expected_outcome` giống hệt.
- [ ] Hành động chỉ có `mode='simulated'`, UI luôn ghi "Thao tác mô phỏng".
- [ ] Test PII: DuckDB và payload OpenAI không chứa thông tin người mua.
- [ ] `audit_log` có bản ghi cho mọi thao tác ghi.
- [ ] Không có API key trong repo hoặc trong bundle frontend.

## Task gợi ý

Người, ước lượng và ưu tiên theo 12-lo-trinh-va-phan-viec. Lọc PII nằm trong ING-01 (02-lop-du-lieu).

| Mã | Việc | Người | Người-ngày | Ưu tiên | Phụ thuộc |
|---|---|---|---|---|---|
| GOV-01 | Bảng decisions/actions/alerts + máy trạng thái (mức sàn: lưu + submit + approve) | D3 | 0,75 (0,5) | P0 | INF-03 |
| GOV-02 | Ma trận quyền `require_role`, người dùng mẫu, test quyền | D4 | 0,25 | P0 | API-01 |
| GOV-03 | Snapshot bối cảnh quyết định + `governance.replay` | D2 | 1 | P1 | GOV-01, SIM-05 |
| GOV-04 | Kiểm lại thao tác tự động + đánh dấu thủ công | D3 | 0,5 | P1 | GOV-01, LIVE-01 |
| GOV-05 | Shadow mode + evaluate_shift | D2 | 1 | P2 | GOV-03, EVAL-03 |
| GOV-06 | Test quét PII trên DuckDB và payload gửi OpenAI | D1 | 0,25 | P1 | ING-01, AI-10 |
| GOV-07 | `audit_log` + middleware | D3 | 0,5 | P1 | API-01 |
