# Nhật ký quyết định · Quản trị (M4: GOV-01, GOV-02, GOV-04 sàn, GOV-07, API-06)

Code: `backend/app/governance/` (`roles.py`, `decisions.py`, `alerts.py`, `audit.py`, `middleware.py`), router `backend/app/api/{decisions,actions,alerts}.py`, phần quyền trong `backend/app/api/deps.py`. Test: `backend/tests/unit/governance/`, `backend/tests/api/test_governance_api.py`, `backend/tests/api/test_audit_middleware.py`.

## Endpoint (dưới `/api/v1`)

| Method | Đường dẫn | Quyền | Ghi chú |
|---|---|---|---|
| POST | `/decisions` | operator | 201. Cần `scenario_id` hoặc `recommendation_id`; thiếu `action` thì lấy hành động tốt nhất của đề xuất |
| GET | `/decisions` | viewer | lọc `state` (nhiều giá trị, phân tách bằng dấu phẩy), `campaign_id`, `created_by`, `kind`, `from`, `to`; phân trang cursor |
| GET | `/decisions/{id}` | viewer | thêm `actions[]` và `can[]` (các nút người gọi được bấm) |
| POST | `/decisions/{id}/submit` · `/reopen` · `/cancel` | operator, chỉ người tạo | |
| POST | `/decisions/{id}/approve` `{note?}` · `/reject` `{reason}` | lead, không phải người tạo | |
| POST | `/decisions/{id}/actions` `{mode:"simulated"}` | operator | 201, approved → sent |
| POST | `/decisions/{id}/verify` | operator | lối tắt: kiểm lại thao tác `sent` mới nhất |
| GET | `/actions`, `/actions/{id}` | viewer | |
| POST | `/actions/{id}/verify` | operator | `{result:"verified"\|"failed", note}` hoặc giá trị quan sát |
| GET | `/alerts` | viewer | `status=open\|live\|acknowledged\|snoozed\|resolved` (nhiều giá trị, phân tách bằng dấu phẩy), `kind`, `severity`, `sku`, `campaign_id` |
| GET | `/alerts/{id}` | viewer | |
| POST | `/alerts/{id}/ack` · `/snooze` `{minutes}` · `/resolve` `{note?}` | operator | resolve phát `AlertResolved` |
| GET | `/audit` | lead | lọc `target_kind`, `target_id`, `action` (lọc theo tiền tố) |

## Quyết định

### Q1. Ma trận quyền một chỗ: `governance/roles.py`
`PERMISSIONS` ánh xạ từng dòng của bảng 10 §1 sang vai trò tối thiểu. Có thêm hai luật về người tạo:
- `OWNER_ONLY` (submit, reopen, cancel): chỉ người tạo.
- `NOT_OWNER` (approve, reject): không bao giờ là người tạo, kể cả lead. Vi phạm trả 403 `FORBIDDEN`, `details.reason = "self_approval"`.

`deps.py` lấy lại `Role`, `ROLE_RANK`, `ROLE_LABEL_VI` từ đây và thêm `require_permission(name)`. `require_role` và `Viewer`/`Operator`/`Lead` giữ nguyên, nên các endpoint cũ không đổi. Phía service kiểm lại quyền bằng `roles.check(actor, perm, owner_id=...)`, nhờ vậy lối AI chịu cùng luật với UI.

Từ chối quyết định của chính mình cũng bị cấm (dòng "Duyệt / từ chối quyết định của người khác"). Muốn bỏ quyết định của mình thì dùng `cancel`.

### Q2. Cổng dữ liệu khi submit và approve
- Có issue `severity='error'` đang mở trên SKU của chiến dịch, hoặc trên chính chiến dịch: trả 422 `BLOCKING_DATA_ISSUE` (dùng `DataQualityError` có sẵn, không dùng 409).
- Trả 409 `STALE_INPUT` khi có một trong các điều kiện:
  - mốc `as_of` mới nhất của orders/inventory **mới hơn** `inputs_ref.data_as_of` của kịch bản;
  - một tham số trong `scenarios.param_version` đã có version khác;
  - mục tiêu của kịch bản không còn hiệu lực.

Không tính lại `input_hash`, vì việc đó cần chạy lại `load_inputs`. Ba so sánh trên dùng đúng các thành phần tạo nên khóa cache.

Hệ quả cho demo live: mỗi tick dữ liệu mới làm các bản nháp cũ "cũ dữ liệu". Lead phải duyệt trong cùng giờ dữ liệu, nếu không thì chạy lại mô phỏng. Làm vậy là đúng tinh thần 10 §1.

### Q3. Snapshot lưu lúc tạo
- `data_as_of` = `{"scenario": mốc kịch bản, "orders"/"inventory"/"ads"/...: as_of mới nhất từng nguồn}`.
- `parameters_snapshot` = `{tên: {version, value, scope[, current_version]}}`.
- `model_versions` = forecast_ids → `sku:model_id`, cùng iROAS estimate/model, `iroas_source`, `panel_version`.
- `expected_outcome` = kết quả mô phỏng của hành động được chọn (objective, contribution, spend, units, leftover; p10/p50/p90) kèm `samples`, `seed`, `start_at`, `horizon_hours`, đủ cho GOV-03 replay sau này.
- `goal_snapshot` = nguyên dòng `goals`.
- `open_issues` = issue error/warn đang mở.

`override_of_recommendation` do backend tự tính (hành động chọn khác `best_action`). Giá trị client gửi lên bị bỏ qua.

### Q4. Kịch bản có giả định không thành quyết định
Kịch bản có `overrides` khác rỗng bị từ chối với 400, giống `draft_decision` của AI. Hành động không có trong kịch bản trả 400. Hành động `feasible=false` trả 409 `CONSTRAINT_VIOLATION`.

### Q5. Hiệu lực và hết hạn
`valid_until = approved_at + 2 giờ`. Đây là hằng `DECISION_VALIDITY_HOURS`, vì danh mục tham số chưa có `decision_validity_hours` và file đó không thuộc phần này. `expire_overdue()` chạy lười khi đọc/list/chuyển trạng thái/gửi thao tác, theo đồng hồ thật (UTC).

### Q6. Thao tác chỉ mô phỏng; kiểm lại mức sàn
- `mode` khác `simulated` trả 400. `payload` có `simulated: true` và nhãn "Thao tác mô phỏng" (`mode_label`).
- Kiểm lại thủ công (`result`) ghi `verified_by_human = true` và `verified_by`. `failed` bắt buộc có `note`, được ghi vào `failure_reason`.
- Có thể gửi giá trị quan sát (`observed_status`, `observed_spend_vnd`, `observed_budget_vnd` + `expected_budget_vnd`) để `compare_observed` đối chiếu theo luật 10 §2 (dừng: "Tạm dừng" hoặc chi 0; giảm ngân sách: ≤ (1−pct)×kỳ vọng×1,15).
- Chưa làm việc tự kiểm theo `ad_hourly` (GOV-04 đầy đủ, P1).

### Q7. Ghi chú bàn giao ca
`kind='handoff_note'` không cần kịch bản. `action = null`, nội dung nằm trong `rationale`. Ghi chú đi qua cùng máy trạng thái (gửi, duyệt, hủy), nhưng không gửi thao tác được (409).

### Q8. Lớp cảnh báo dùng chung: `governance/alerts.py`

```python
create_alert(conn, kind, scope, severity, title, detail=None, dedupe_key=None, *,
             impact_vnd=None, group_key=None, source_event_id=None,
             shop_id="shop-demo", now=None) -> AlertUpsert(alert, created, raised)
```

- `scope` nhận `("sku", "X")`, `"campaign:CD002"`, `"shop"` hoặc `None`. `dedupe_key` mặc định là `f"{kind}:{target_kind}:{target_id}"`.
- Khi đã có cảnh báo cùng key còn sống (open/acknowledged/snoozed), hàm cập nhật tại chỗ (`occurrences += 1`, `last_seen_at`). `raised = True` chỉ trong ba trường hợp: cảnh báo mới; hết hạn tạm ẩn; mức độ tăng (cảnh báo đã xem quay lại `open`). Sau `resolved`, lần sau tạo dòng mới.
- Các hàm khác: `list_alerts`, `get_alert`, `ack_alert`, `snooze_alert`, `resolve_alert`, `resolve_by_key`, `set_status` (`hidden_by_budget`), `set_investigation`, `count_open`.
- Hàm **không phát event**: người gọi tự phát `AlertRaised` khi `raised`, `AlertResolved` cho các id đã resolve.

Migration 4 thêm `alerts.dedupe_key, updated_at, last_seen_at, occurrences, acknowledged_at, resolved_by` và `actions.note, verified_by`.

### Q9. Audit (GOV-07)
- Mỗi hàm governance tự ghi `audit_log` qua `write_audit` (`decision.create|submit|approve|reject|cancel|reopen|expire|send|verify|fail`, `alert.ack|snooze|resolve`), với `via` của actor (ui/ai/system).
- `request_id` lấy từ header, nếu không có thì lấy id mà middleware `request_context` đã bind.
- Lưới an toàn `install_audit_middleware(app)`: sau một lệnh ghi thành công dưới `/api/v1` mà chưa có dòng audit nào cùng `request_id`, middleware ghi một dòng `http.<method>`, `target_id = route template`. Không ghi body.
- **Chưa gắn vào `app/main.py`** vì file này không thuộc phần quản trị. Main agent thêm `install_audit_middleware(app)` ngay sau khối `@app.middleware("http") request_context`.

### Q10. Ngoài phạm vi
- `POST /alerts/{id}/investigate` do lớp AI làm (`/ai/investigate`).
- Chưa làm `Idempotency-Key` cho `POST /decisions`.
- Chưa làm GOV-03 replay.
