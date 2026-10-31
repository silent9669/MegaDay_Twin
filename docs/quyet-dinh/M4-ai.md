# Nhật ký quyết định · Nền tảng lớp AI (M4: AI-01, AI-02, AI-03, AI-04, AI-06, AI-07, AI-09, AI-10, khung EVAL-05)

Code: `backend/app/ai/` (endpoint: mục 6 "AI ra API"). Test: `backend/tests/unit/ai/` (143 test, gồm 4 test e2e trên `demo`).

| File | Nội dung |
|---|---|
| `client.py` | Giao diện `LlmClient`; `FakeClient` (kịch bản cho test/eval), `OpenAIClient` (Responses API + function calling strict, model lấy từ env), `RecordReplayClient` (`AI_MODE=record/replay`), `make_client(settings)` |
| `tools/registry.py` | `ToolSpec`, `@tool`, `execute()` (kiểm JSON, schema, vai trò, cổng nháp/xác nhận, ghi `ai_tool_calls`), cắt kết quả ≤ 4.000 token |
| `tools/schemas.py` | Pydantic tham số của 26 công cụ + `strict_schema()` |
| `tools/read.py`, `compute.py`, `draft.py` | 17 công cụ đọc, 3 công cụ tính, 6 công cụ nháp/ghi (07 §3) |
| `grounding.py` | Kiểm số (07 §6) |
| `guard.py` | Chống prompt injection (07 §7) |
| `agent.py` | Vòng function calling, ngân sách token, viết lại khi sai số, log `ai_sessions` |
| `flows/__init__.py` | 6 luồng (whatif, investigate, explain, handoff, ingest, chat): công cụ + model theo việc |
| `flows/whatif.py` | `WhatIfIntent` (structured output) → `RunScenarioArgs` → `SimRequest`; bộ tách ý theo luật |
| `flows/investigate.py` | Checklist 6 bước chạy công cụ thật + tóm tắt |
| `prompts/*.vi.md` | System prompt 07 §5 + phần riêng từng luồng; `PROMPT_VERSION` = hash các file |
| `cache.py` | `data_fingerprint`, cache câu trả lời 30 phút |
| `evals.py` + `tests/unit/ai/eval_cases.json` | Khung eval 07 §10, 18 câu ban đầu |

## 1. Kết quả chính

- **What-if "sếp giao chỉ tiêu 97 cái"** (DB demo lúc 01:00 ngày 11.11, công cụ thật): tách ý ra `goal_override = sell_target, 97`. Mục tiêu hiện tại (profit) vẫn đề xuất "chỉ chạy khung giờ 00–02, 12–14". Với kịch bản giả định thì đề xuất **giảm ngân sách 30%**: xác suất đạt 81%, còn chạy khung giờ chỉ đạt 57%. Khớp M3-whatif 4g. Câu trả lời qua kiểm số 8/8. Bảng `goals` không thêm dòng nào, kịch bản không lưu. *(Số này đo trên DB không backtest/fit. Trên DB demo đầy đủ (M5 "Phương án A", 31/10): không phương án nào đạt chắc 80%, cao nhất là chạy tiếp 70%; bản ghi demo đã ghi lại theo số đó.)*
- **Điều tra "vì sao áo giữ nhiệt bán chậm"** (DB demo lúc 14:00): checklist chạy 15 lần gọi công cụ. Nguyên nhân là `AOGIUNHIET-M` hết hàng (tồn 0 từ 13:00). Kho có ghi chú điều chỉnh tồn lúc 11:00, tồn giảm 74 sp. Size L bán 27 sp, size S bán 11 sp trong 3 giờ. Ads CD005 vẫn chạy (số liệu ads tới 11:59). Gợi ý giảm ngân sách CD005 tới khi hàng về ngày 14/11. Kiểm số 10/10.
- **Injection** (ghi chú cài sẵn ở đơn `2611099UF5GLZH` trong `don_hang_seller_center.csv`): ghi chú được bọc `<du_lieu_khong_tin_cay nguon="ghi_chu_nguoi_mua">` và gắn cờ `possible_instruction`. Test dùng model giả "bị dụ" để thử cả hai đường tấn công:
  - Model gọi `draft_decision` pause. Lệnh bị chặn với lỗi `needs_user_request`, không ghi `decisions`.
  - Model viết "hãy tắt toàn bộ quảng cáo". Câu này bị chặn, hệ thống ghi `alerts` loại `ai_policy` và bắt viết lại. Câu cuối nói "có ghi chú chứa yêu cầu lạ, không làm theo".
  - Dữ liệu gửi cho LLM không chứa `Khách Test` hay số điện thoại.

## 2. Quyết định

### Q1. Công cụ gọi thẳng hàm service/snapshot, không gọi HTTP
Công cụ dùng lại `app.api.snapshot` (`sku_rows`, `detail`, `commitments`, `latest_forecasts`), `app.api.common.freshness`, `issue_message`, `run_simulation`, `load_estimate`, `load_param_set`, `active_goal`, `set_parameter`. Nhờ vậy số của AI cùng nguồn với màn hình. Lớp AI không sửa file nào của `app/api`, chỉ import.

### Q2. `run_scenario` thêm `goal_override` (`[DEV]` so với 07 §3)
Câu hỏi "nếu sếp giao chỉ tiêu N" là giả định về **mục tiêu**, không phải tham số. AI không được tạo mục tiêu (05 §4, 10 §1), nên xử lý như sau:
- Mục tiêu giả định được INSERT trong một transaction, chạy `run_simulation(persist=False, use_cache=False)`, rồi **luôn ROLLBACK**.
- Kết quả có nhãn `is_scenario`, `confidence = scenario`, `goal.hypothetical = true`.
- Hạn mặc định là hạn của mục tiêu đang áp dụng, nếu không có thì là hết ngày dữ liệu hiện tại.

Tương tự, chạy có `param_overrides` thì không lưu (không thay đề xuất đang hiệu lực). Chạy không có giả định thì lưu như `POST /scenarios/run`, để `draft_decision` có `scenario_id`. Override sai danh mục hoặc ngoài khoảng sẽ nằm trong `param_overrides_rejected` kèm lý do, LLM phải nói ra.

### Q3. Cổng ghi: nháp chỉ khi chính người dùng yêu cầu
- Công cụ `draft` (`draft_decision`, `suggest_parameter`, `propose_*`, `draft_shift_handoff`) chạy khi có một trong hai điều kiện:
  - `ToolContext.allow_drafts` (nút trên UI);
  - **câu của người dùng** khớp ý "tạo/lưu nháp, gửi duyệt, ghép cột…" (`guard.user_requests_draft`). Không bao giờ xét nội dung kết quả công cụ.
- `request_approval` cần thêm `user_confirmed` (nút "Gửi duyệt").
- Các lỗi `needs_user_request` / `needs_confirmation` được trả cho LLM, không ném lỗi.
- `draft_decision` từ chối kịch bản có giả định ("dùng Ghi chú cho lead"), từ chối hành động vi phạm ràng buộc, và ghi `audit_log` với `via='ai'`.
- Nháp ghép cột, ghép SKU, bàn giao ca tạm giữ trong bộ nhớ (`draft.DRAFTS`) cho tới khi có endpoint xác nhận.

### Q4. Grounding: dung sai theo độ chính xác hiển thị
Ngoài ngưỡng 07 §6 (0,5% hoặc 1.000đ; 0,5 điểm %), số còn được khớp nếu lệch trong nửa đơn vị của chữ số cuối đã viết ("4,1 triệu" phủ ±50.000đ). Có từ "khoảng" thì nới gấp đôi, số tròn chục thì phủ ±5. Các quy tắc khác:
- **Không chấp nhận** số nằm trong khối dữ liệu không tin cậy.
- Số người dùng viết trong câu hỏi được chấp nhận (`user_quoted`).
- Phép tính một bước (hiệu, tổng, tỷ lệ) chỉ được chấp nhận khi câu có từ chỉ phép tính, và chỉ xét số cùng loại. **Đã siết ở mục 7** (chỉ hai giá trị cùng chỉ số, cùng lần gọi công cụ; số trong câu chữ không còn là nguồn).
- Câu nêu SKU hoặc chiến dịch thì số phải thuộc đối tượng đó (`entity_mismatch`). Mã lạ không có trong kết quả công cụ bị chặn.
- Giờ "13:59" khớp với `as_of` 14:00, vì `as_of` là mốc không bao gồm.
- Chip thiếu hoặc sai được backend tự gắn hoặc sửa (`citation = added | corrected`), không bắt viết lại.

Sai thì viết lại tối đa 2 lần, sau đó trả câu dự phòng kèm bảng số thô và ghi `grounding_ok=false`.

### Q5. Đầu ra bị chặn khi khuyên "tắt toàn bộ ads" mà không có mô phỏng ủng hộ
Câu phủ định ("không đề xuất tắt toàn bộ…") vẫn được phép. Lần vi phạm đầu tiên trong lượt ghi một dòng `alerts` (`kind='ai_policy'`).

### Q6. Record/replay theo lượt
Khóa = sha256 của (luồng, câu hỏi chuẩn hóa, `data_fingerprint`, `PROMPT_VERSION`, hash lịch sử phiên). `data_fingerprint` dùng (nguồn, `as_of`, số dòng) của batch, mục tiêu đang hiệu lực và tham số hiệu lực. Không dùng ULID, nên dựng lại cùng dữ liệu vẫn trúng bản ghi.
- **Replay** chỉ phát lại phản hồi của LLM. **Công cụ vẫn chạy thật** trên dữ liệu hiện tại, và grounding kiểm lại. Câu trả lời được gắn nhãn "Câu trả lời đã lưu (chế độ demo ngoại tuyến)".
- Không có bản ghi thì trả `outcome=offline` ("Trợ lý đang ngoại tuyến").
- Bản ghi nằm ở `backend/eval/ai_recordings/<flow>/<key>.json`. File chỉ lưu tóm tắt request (model, số item, tên công cụ), không lưu prompt hay dữ liệu thô.

### Q7. Agent điều tra theo mức sàn của 12
Checklist (`run_checklist`) chạy công cụ thật và không cần LLM. Kết quả được đưa cho LLM như kết quả của công cụ giả `investigation_checklist`. LLM chỉ viết tóm tắt, và tóm tắt phải qua grounding. `summarize_payload` viết cùng tóm tắt khi AI tắt; FakeClient trong test cũng dùng hàm này. `investigate(..., alert_id=)` lưu vào `alerts.investigation`.

### Q8. Vòng lặp đồng bộ (sync)
Endpoint hiện tại là sync, dùng threadpool. `Agent.run_turn` là sync để dùng chung cursor DuckDB. SSE ở đợt sau có thể chạy trong threadpool và đẩy sự kiện theo từng bước.

### Q9. Eval dùng JSON, chạy offline
Dependency không có parser YAML nên câu eval viết bằng JSON. Mỗi câu có `script` (các bước LLM giả) và `tool_fixtures` (kết quả công cụ mẫu đi qua cùng `execute`, kể cả cổng quyền và nháp). Metric theo 07 §10. Offline, 18/18 câu đạt mọi ngưỡng. Lưu ý: với FakeClient, metric "đúng công cụ / đúng tham số" chỉ kiểm khung chấm, chưa đo chất lượng model.

### Q10. Khác
- `get_forecast(action="pause")` trả `available=false` kèm lời dặn dùng `run_scenario`/`compare_actions`, vì dự báo lưu trong DB đi theo kế hoạch ads hiện tại.
- `suggest_sku_matches` chưa dùng embeddings (cần `OPENAI_MODEL_EMBED`); hiện chấm điểm bằng rapidfuzz.
- `compare_similar_skus`: nếu có dự báo phủ đủ khung giờ thì so với dự báo, nếu không thì so với trung bình cùng giờ của 7 ngày trước.
- `guard.py` import `app.ingest.adapters` trước `app.ingest.pii` vì hai module import vòng (lỗi sẵn có ở ingest, không sửa).
- `cost_estimate_usd` để NULL vì chưa có bảng giá model.

## 3. Endpoint cần thêm ở đợt sau (API-07; đã làm phần lớn, xem mục 6)

| Method | Path | Ghi chú |
|---|---|---|
| POST | `/ai/chat` | body `{session_id?, flow, message, context{campaign_id?, sku?, scenario_id?}, allow_drafts?}`. Sàn: trả JSON một lần (`TurnResult`: text, citations, grounding badge + bảng đối chiếu, tool_calls, outcome, replayed/label) |
| GET | `/ai/chat/stream` hoặc SSE trên `/ai/chat` | sự kiện `tool_call`, `tool_result`, `rewrite`, `answer` |
| POST | `/ai/whatif/parse` | câu hỏi → `WhatIfIntent` / `RunScenarioArgs` hoặc câu hỏi lại |
| POST | `/alerts/{id}/investigate` | gọi `flows.investigate.investigate(..., alert_id=)` |
| POST | `/ai/drafts/{draft_id}/confirm` | xác nhận nháp ghép cột / ghép SKU / bàn giao (`draft.DRAFTS`) |
| POST | `/decisions/{id}/submit` | phía UI của `request_approval` (`user_confirmed=true`) |
| GET | `/ai/sessions/{id}` | lịch sử + tool calls (màn Nhật ký) |

Phụ thuộc: `deps.require_role(operator)`; dùng `make_client(settings)`; `AI_ENABLED=false` trả 503 `AI_UNAVAILABLE`, các màn hình khác vẫn chạy.

## 4. Việc cần làm khi có OPENAI_API_KEY

1. Chọn model, điền `OPENAI_MODEL_REASONING / FAST / EMBED` vào `.env.example` kèm ngày kiểm bảng giá. Thêm bảng giá để tính `cost_estimate_usd`.
2. Chạy thật `OpenAIClient` và kiểm:
   - strict schema của 26 công cụ được API chấp nhận (`strict_schema` đã đưa min/max/format vào description);
   - cách trả lại `output_items` (reasoning + function_call) giữa các bước;
   - model reasoning không nhận `temperature` (client đã có nhánh thử lại bỏ tham số này).
3. Chạy `tests/unit/ai/eval_cases.json` ở chế độ live (`run_case(..., offline=False, client_factory=…)`) trên DB demo. Mở rộng lên khoảng 40 câu, viết `eval/eval_ai.py` xuất `eval/reports/ai_eval_<thời gian>.md`.
4. `AI_MODE=record` khi diễn tập kịch bản demo 3 phút để có đủ bản ghi; GOV-06 quét PII trong bản ghi.
5. Embeddings cho `suggest_sku_matches` (lưu `sku_aliases.embedding`, cần migration); đọc ảnh chụp màn hình ở luồng nhập dữ liệu.

## 5. Kiểm tra chất lượng

- `ruff check` / `ruff format --check`: sạch trên `app/ai`, `tests/unit/ai`.
- `mypy app`: sạch (110 file).
- `pytest tests/unit/ai tests/unit/test_no_ground_truth_leak.py`: 143 đạt, khoảng 1 phút (dựng DB demo khoảng 44 giây).
- Prompt và code trong `app/ai` không nhắc tới thư mục đáp án.

---

## 6. AI ra API (API-07 phần AI, AI-01, AI-07 sàn, AI-10)

Code: `backend/app/api/ai.py` (router `/api/v1/ai`), sửa trong `app/ai/` (client, agent, investigate, grounding, prompt). Test: `tests/api/test_ai_api.py` (10), `tests/unit/ai/test_openai_client.py` (5), `tests/unit/ai/test_e2e_ai_demo.py::test_demo_recordings_record_and_replay`. Không test nào gọi mạng.

### Endpoint

| Method | Path | Vai trò | Ghi chú |
|---|---|---|---|
| POST | `/ai/chat` | operator | body `{session_id?, flow, message, context{campaign_id?, sku?, product_id?, scenario_id?, alert_id?, screen?}, allow_drafts?}`. Mặc định trả JSON một lần (`Envelope[ChatTurn]`). `Accept: text/event-stream` hoặc `?stream=true` → SSE `session → tool_call/tool_result/rewrite… → answer → done` (08 §3.12; lỗi giữa chừng → event `error`) |
| POST | `/ai/investigate` | operator | `{sku?, product_id?, question?, alert_id?, window_hours}` → checklist 6 bước + tóm tắt |
| GET | `/ai/sessions/{id}` | viewer (operator chỉ xem phiên của mình) | các lượt (câu hỏi + `ChatTurn`), `ai_sessions`, `ai_tool_calls`, nháp |
| GET | `/ai/drafts?session_id=&status=` | operator | nháp AI chờ xác nhận |
| POST | `/ai/drafts/{id}/confirm` · `/reject` | operator (người tạo) | xem Q14 |
| GET | `/ai/status` | viewer | bật/tắt, mode, `has_key` (không bao giờ trả key), model, số bản ghi |

`ChatTurn`: `text` (còn `[[src:id]]`), `sources[]` (chip: id, kind, label, data_as_of, tool, mentions), `grounding` (`ok, checked, passed, badge, rewrite_count, violations, numbers[]`), `tool_calls[]`, `drafts[]`, `outcome`, `policy_violations`, `untrusted_flags`, `replayed`, `replay_match`, `label`, `model`, `usage`, `ai_mode`, `investigation` (checklist khi `flow=investigate`). `meta.sources` = cùng chip ở dạng `SourceRef`.

### Quyết định

- **Q11. Lỗi rõ ràng.** `AI_ENABLED=false` → 503 `AI_UNAVAILABLE` "AI đang tắt". `AI_MODE=live|record` không có key → 503, message "Chưa có OPENAI_API_KEY (đặt trong biến môi trường hoặc backend/.env), hoặc chạy AI_MODE=replay…". Lượt kết thúc `offline` (OpenAI lỗi, replay không có bản ghi) → 503 như 08 §1 (details có `session_id`). SSE kiểm client trước khi mở luồng nên vẫn trả JSON 503. Riêng **`investigate` không bao giờ 503**: checklist chạy không cần LLM, tóm tắt viết bằng `summarize_payload` (`ChecklistSummaryClient`, `model="checklist"`, nhãn "Tóm tắt tự động từ checklist (không dùng AI)"); cũng dùng khi LLM trả `offline`/`gave_up` (`investigate(..., fallback_summary=True)`).
- **Q12. Model mặc định** (khi `OPENAI_MODEL_*` trống, kiểm 30/10/2026): `reasoning = gpt-5`, `fast = gpt-5-mini` (`client.DEFAULT_MODELS`); embed không có mặc định. Đổi bằng `OPENAI_MODEL_REASONING / OPENAI_MODEL_FAST` trong `.env`. Model GPT-5/o-series: không gửi `temperature`, gửi `reasoning.effort` (`low` cho reasoning, `minimal` cho fast), `max_output_tokens` +4.000 cho token suy luận. Chưa có bảng giá → `cost_estimate_usd` vẫn NULL.
- **Q13. OpenAIClient.** Responses API, `store=false` (OpenAI không lưu), `include=["reasoning.encrypted_content"]` để trả lại reasoning item giữa các bước mà không cần lưu phía server. Lỗi 400 nhắc tới `temperature/reasoning/include` → bỏ tham số đó và thử lại một lần; 401 → "OPENAI_API_KEY không hợp lệ". Key chỉ đọc từ `Settings` (env / `backend/.env`), không log. **PII:** câu hỏi người dùng (và tin `developer`) được che sđt/email (`ingest.pii.mask_contacts`) ngay đầu `run_turn`, nên log, bản ghi và payload đều sạch; kết quả công cụ vốn không có cột người mua và ghi chú đã che + bọc `<du_lieu_khong_tin_cay>`. Ngữ cảnh màn hình chỉ nhận mã đã kiểm regex, đưa vào system prompt (`Người dùng đang xem: …`, `PROMPT_VERSION` đổi).
- **Q14. Nháp.** Nháp được đăng ký từ các lần gọi công cụ nháp thành công trong phiên (bộ nhớ tiến trình). Xác nhận: `decision` → `app.governance.decisions.submit` (draft → pending_approval, kiểm `input_hash`; lead vẫn phải duyệt), từ chối → `cancel`; `parameter` → `confirm_suggestion` (thành `ai_suggested_confirmed`, kiểm `editable_by`); ghép cột / ghép SKU / bàn giao → đổi trạng thái `confirmed|rejected` và trả payload cho màn Nhập dữ liệu / Nhật ký (chưa tự áp mapping, chưa lưu `handoff_note`). Đã xác nhận/từ chối rồi → 409. Cảnh báo `ai_policy` ghi qua `app.governance.alerts.create_alert` (dedupe theo phiên). Cả hai có nhánh dự phòng khi module quản trị vắng.
- **Q15. Phiên.** Phiên do server tạo; `session_id` lạ → 404 (phiên mất khi khởi động lại), phiên của người khác → 403. Cache câu trả lời (`AI_DEMO_CACHE`) chỉ bật ở `AI_MODE=live`.
- **Q16. Replay chịu được đồng hồ dữ liệu chạy.** Khóa chính xác (Q6) hầu như không trùng lúc demo vì `as_of` thay đổi, nên replay **lùi về bản ghi mới nhất cùng luồng + cùng câu hỏi chuẩn hóa** (`replay_match = exact | question`). Công cụ vẫn chạy thật và grounding kiểm lại mọi số: dữ liệu khác bản ghi thì lượt thành `gave_up`, không bao giờ nói số sai. Mã chip trong bản ghi được ánh xạ sang nguồn của lần chạy mới theo (công cụ, thứ tự gọi) (`remap_sources`, cần vì `scenario_id` của kịch bản giả định đổi mỗi lần), và grounding bỏ chip trỏ tới nguồn không có trong lượt. Bản ghi bỏ `encrypted_content`.
- **Q17. SSE** chạy lượt trong một thread riêng với cursor riêng (`db.reader()`), đẩy sự kiện qua hàng đợi; `Agent(on_event=…)` phát `tool_call`, `tool_result`, `rewrite`.

### Bản ghi demo offline (AI-10)

`backend/eval/ai_recordings/` có sẵn 3 bản ghi, tạo bằng FakeClient theo kịch bản cố định trên DB demo dựng tạm (công cụ thật, grounding đạt): 

| Luồng (`flow`) | Câu hỏi (gõ đúng câu này) | Mốc dữ liệu | Kết quả |
|---|---|---|---|
| `whatif` | Nếu sếp giao chỉ tiêu 97 cái hôm nay thì sao? | 01:00 ngày 11.11, mục tiêu profit CD002 | không phương án nào đạt chắc 80%; cao nhất chạy tiếp 70% (khung giờ 50%); chạy tiếp lời kém khung giờ 3,97 triệu, 17/17 số |
| `explain` | Vì sao nồi chiên chỉ chạy khung giờ? | 01:00 | 3 lý do + điều làm đổi kết luận (hàng dư ≤ −199.969đ/sp), 9/9 số |
| `investigate` | Vì sao áo giữ nhiệt bán chậm? | 14:00 | AOGIUNHIET-M hết hàng, 10/10 số |

Tạo lại bản ghi FakeClient: `cd backend && AI_WRITE_RECORDINGS=1 AI_RECORD_DB=<DB đầy đủ của demo.mjs>/demo.duckdb uv run pytest tests/unit/ai/test_e2e_ai_demo.py -k recordings` (mặc định test ghi vào thư mục tạm, trên DB nhẹ của fixture). Bản ghi hiện tại (31/10) ghi trên DB đầy đủ `app.demo_import --replay-hour 0 --models`; xóa bản cũ trước để mỗi luồng chỉ còn 1 file. `AI_RECORD_DB` chép cả thư mục `live_stream` bên cạnh DB và chuyển con trỏ live sang bản chép, nên mốc 14:00 nối tiếp đúng như `jump 13` của server.

Ghi lại bằng key thật: đặt `OPENAI_API_KEY=…` (và tùy chọn `OPENAI_MODEL_*`) trong `backend/.env`, `AI_MODE=record`, chạy server ở đúng mốc dữ liệu demo, hỏi 3 câu trên qua UI hoặc `POST /api/v1/ai/chat` với đúng `flow`. Mỗi lượt trả lời đạt sẽ ghi `eval/ai_recordings/<flow>/<key>.json` (bản ghi mới nhất thắng khi replay). Xóa bản ghi FakeClient cũ nếu muốn chỉ dùng bản thật. Khi demo: `AI_MODE=replay` (không cần key, không cần mạng).

### Kết quả

- Không có `OPENAI_API_KEY` trong `backend/.env` hay môi trường lúc làm, nên **chưa chạy thật** với OpenAI; phần gửi request được test với SDK bị thay hàm `responses.create`.
- `uv run python tasks.py check`: ruff + format + mypy (132 file) sạch; pytest 675 đạt, 1 bỏ qua, 3 e2e bỏ chọn (chạy riêng `pytest tests/unit/ai/test_e2e_ai_demo.py`: 5 đạt).
- Còn lại: `/alerts/{id}/investigate` (router của nhóm quản trị) có thể gọi thẳng `flows.investigate.investigate(..., alert_id=, fallback_summary=True)`; `/ai/handoff` và `/ai/whatif/parse` chưa làm (dùng `/ai/chat` với `flow=handoff|whatif`); nháp ghép cột chưa tự áp vào `POST /ingest/batches/{id}/mapping`.

### Nối công cụ nháp vào lớp quản trị (sau M4-quan-tri)

- `draft_decision` gọi `governance.decisions.create_decision(conn, Actor(user, role, via="ai", request_id=session_id), …)`: snapshot mục tiêu/tham số/model, `input_hash`, `audit_log` (`decision.create`, `via=ai`) do service ghi. Lỗi vẫn là `NotFound` (kịch bản không lưu, giữ lời dặn "dùng ghi chú cho lead") / `ValidationFailed` / `ConstraintViolation`.
- `request_approval` gọi `governance.decisions.submit`: không phải người tạo → `FORBIDDEN` (trước là STATE_CONFLICT), issue chặn → 422 `BLOCKING_DATA_ISSUE`, dữ liệu đổi → 409 `STALE_INPUT`, sai trạng thái → `STATE_CONFLICT`. Mã lỗi đi thẳng vào `ToolResult.error` cho LLM.
- Bàn giao ca: `POST /ai/drafts/{id}/confirm` với body `{note?}` lưu `decisions` `kind='handoff_note'` qua `create_decision` (mặc định nội dung = nháp dựng thành văn bản); `DraftOut.saved_decision_id` trả id.
- `investigate(..., alert_id=)` lưu qua `governance.alerts.set_investigation` (thêm `label`, `session_id`).
- Mọi audit của lớp AI và router `/ai` dùng `governance.audit.write_audit` (AI: `via=ai`, request = phiên AI; nút trên UI: `via=ui`).
- `POST /alerts/{id}/investigate` (quyền `alert.investigate` = operator) thêm vào `app/api/alerts.py`, gọi `app.api.ai.investigate_alert`: SKU của cảnh báo (hoặc `group_key` = sản phẩm), checklist + tóm tắt (LLM nếu có, không thì tự động), kết quả lưu vào cảnh báo. Trả 200 đồng bộ (08 ghi 202 + SSE `alert.investigated`: chưa làm).

## 7. Siết grounding (M5, sau diễn tập)

Lỗi (M5-chuan-bi-demo.md Q5): trên DB có backtest/fit, công cụ trả 67% (giảm 30%) và 51% (khung giờ), vậy mà câu "đạt 81%" vẫn qua vì luật cũ coi 81 = "0.5 + 80" (cộng `action.pct` 0,5 với một số 80), còn "57%" qua vì khớp con số trong chuỗi `recommendation.reasons`. Kiểm số "8/8" khi đó là sai.

**Luật cũ** (`grounding.py` trước M5):
- Nguồn số = mọi số JSON **và mọi số tách ra từ chuỗi chữ** của kết quả công cụ (`reasons`, `label`, `note`, `goal_note`…), trừ khối `<du_lieu_khong_tin_cay>`.
- `%` khớp mọi số ≤ 1,5 (×100) hoặc bất kỳ số nào bằng nó, kể cả số lượng / tiền (98% khớp `units_sold` 98,4; "500đ" khớp số 97 nhờ sàn 1.000đ).
- Phép tính một bước: hiệu / tổng / tỷ lệ của **bất kỳ hai số** "cùng loại" (gần như mọi số) trong lượt, chỉ cần câu có từ như "giảm", "hơn", "tổng". Ra 0.5 + 80, 97 + 98,4 sp, 375.915 + 3.621.635đ, 0,8 / 0,62.

**Luật mới:**
1. **Chỉ giá trị có cấu trúc là nguồn**: số JSON, mốc giờ/ngày ISO, chuỗi chỉ gồm một số hoặc một giờ `HH:MM`. Số nằm trong câu chữ (lý do, ghi chú, nhãn) **không** được tính. Chuỗi do hệ thống sinh từ một giá trị (vd nhãn "Giảm ngân sách 30%" từ `action.pct = 0.3`) thì câu trả lời khớp thẳng vào giá trị đó; số chỉ có trong chữ (vd "nhu cầu ~156 sp", "lãi 9.280đ", "iROAS ≥ 57.2", "80%" trong `goal_note`) bị chặn.
2. **Đơn vị theo tên khóa** (`key_unit`): đọc các từ của khóa từ cuối về, từ đầu tiên gọi tên đơn vị thắng (`p_target`, `*_prob`, `*_pct`, `share`, `rate` → %; `spend`, `budget`, `price`, `objective`, `*_vnd`… → tiền; `units`, `qty`, `stock`, `sold`, `n_*`… → sp; `hours`, `window` → giờ; `minutes` → phút; từ ngay sau `per` bỏ qua: `contribution_per_unit_vnd` → tiền). Khóa thống kê (`mean`, `p50`, `interval`…) lấy đơn vị của khóa cha. Khóa không gọi tên gì là "chung".
3. **Khớp theo đơn vị**: `%` chỉ khớp khóa xác suất/tỉ lệ/% (giá trị 0–1 → ×100, hoặc 0–100), hoặc khóa chung có giá trị 0–1 (×100); **không bao giờ** khớp tiền, số lượng, thời lượng. Tiền / sp / giờ / phút / ngày khớp khóa cùng đơn vị hoặc khóa chung. Số trần (không đơn vị) khớp giá trị thô. Dung sai giữ như Q4; sàn 1.000đ chỉ áp cho giá trị ≥ 10.000đ (số nhỏ như 0,5 hay 97 không còn "là" 500đ).
4. **Phép suy dẫn được phép** (câu vẫn phải có từ chỉ phép tính): hiệu, tổng, hoặc tỷ lệ ("gấp … lần", số trần) của **hai giá trị cùng một chỉ số trong cùng một lần gọi công cụ**: cùng đường dẫn JSON trừ chỉ số mảng, ví dụ `results[1].objective.mean − results[0].objective.mean` (hai phương án cùng lần mô phỏng), chênh `p_target` theo điểm %, tổng `spend` hai phương án. Không toán hạng 0. Đổi đơn vị (tr ↔ đ, tỉ lệ ↔ %) không phải phép tính: khớp trực tiếp đã lo. Bỏ hẳn: cộng/trừ hai chỉ số khác nhau (kể cả cùng đơn vị), tỷ lệ ra %, số nhỏ (0,5) làm toán hạng cho số khác loại.
5. Câu nhắc lại cho LLM khi bị chặn (`rewrite_instruction`) nói thêm: chỉ lấy số ở trường số, số trong câu chữ không được tính. Prompt hệ thống không đổi (giữ `PROMPT_VERSION`, bản ghi vẫn trúng khóa).

**Test mới** (`tests/unit/ai/test_grounding.py`, ca `WIF`: `p_target` 0,67 / 0,62 / 0,51, `action.pct` 0,3 / 0,5, `sell_target_prob` 0,8, `threshold_pct` 80, `target_units` 97, `goal_note` "…67% < 80%", `reasons` có "9.280đ", "57.2"): "đạt 81%" bị chặn (cả khi câu có "hơn ngưỡng 80%" / "tổng"); "57%" và "9.280đ" chỉ có trong chữ bị chặn; "67%" đạt và trỏ `results[0].p_target`; câu đầy đủ 30% / 67% / 51% / 80% / 97 sp đạt; `%` không khớp 98,4 sp hay 97 sp; ngoài dung sai (68%, 3,7 triệu) bị chặn; cộng hai chỉ số khác nhau (chi + ngân sách còn lại, `units_sold` + `target_units`, 80 + 50 → 130%) bị chặn; hiệu `p_target` (16 điểm %) và tổng `spend` hai phương án đạt; "500đ" không khớp số nhỏ. Hai test cũ dựa vào số trong chữ được sửa cho đúng luật mới (`test_missing_chip_is_added` thêm `contribution_per_unit_vnd`; `test_unit_mismatch_pct_vs_units` dùng `p_target`).

**Eval** (`tests/unit/ai/eval_cases.json`, 18 ca, offline FakeClient): trước 18/18. Luật mới với bộ ca cũ: 16/18, `invest-002` ("trễ khoảng 2 giờ" chỉ có trong `note`) và `explain-001` ("14:00 ngày 11/11", "lãi 9.280đ" chỉ có trong `why`) bị chặn đúng luật. Sửa hai ca cho giống công cụ thật (`threshold_minutes: 180`; `results[].spend`, `stockout_hour_p50` có cấu trúc; câu trả lời dùng các số đó): sau 18/18, mọi ngưỡng 07 §10 đạt (grounding 100%, bịa số bị chặn 100%).

**3 bản ghi demo** (`eval/ai_recordings`) trên DB dựng như `scripts/demo.mjs` (`app.demo_import --replay-hour 0 --estimate-ads`, mục tiêu profit CD002, AI_MODE=replay; investigate sau `jump 13`): vẫn đạt, explain 9/9, whatif 8/8, investigate 10/10. Hai số đổi nguồn sang đúng chỗ: "97 cái" trước khớp chuỗi `goal_note`, giờ khớp `goal.target_units`; "3 giờ" trước khớp nhầm `variants[2].expected_units` (3,1 sp), giờ khớp `window_hours`. "81%"/"57%" của whatif khớp `results[].p_target` thật (0,81 / 0,573).

**Ghi lại trên DB đầy đủ (M5 "Phương án A", 31/10).** Demo giờ chạy trên DB có backtest + fit (`scripts/demo.mjs` mặc định). Whatif "chỉ tiêu 97" ra `none_meets_goal`, nên câu trả lời kịch bản viết lại: không phương án nào đạt chắc 80%, cao nhất là chạy tiếp (`p_target` 0,698 → 70%), khung giờ 0,505 → 50%, chạy tiếp lời kém khung giờ 3,97 triệu (hiệu `results[].objective.mean` trong cùng lần gọi), gợi ý hạ chỉ tiêu hoặc chấp nhận rủi ro; kiểm số 17/17. Ngưỡng 80% trước chỉ có trong chữ `goal_note` (bị chặn đúng luật), nên `run_scenario` / `compare_actions` trả thêm trường số `goal.sell_target_prob` (tham số của mục tiêu, không có thì lấy từ bảng tham số; chỉ với `sell_target`). Luật grounding không đổi. explain 9/9, investigate 10/10 ghi lại trên cùng DB. Test: `tests/unit/test_rehearsal.py::test_demo_recordings_say_what_the_rehearsal_checks` (mỗi luồng đúng 1 bản ghi, câu trả lời có các cụm `eval.rehearsal` kiểm).

**Hệ quả khi chạy LLM thật:** các con số chỉ có trong `reasons` của mô phỏng (giá 11.11, lãi mỗi sp, ngưỡng iROAS, nhu cầu, tồn) và ngưỡng xác suất của mục tiêu (80% trong `goal_note`) hiện không có trường số riêng, nên LLM nói ra sẽ bị yêu cầu viết lại. Việc tiếp: đưa các giá trị này vào payload công cụ dạng trường số (cần lớp mô phỏng trả chúng ra cùng `reasons`); không nới luật.

**Khóa ghi của công cụ AI.** `run_scenario`, `compare_actions`, `compute_breakeven` (cùng đi qua `compute.simulate`) giờ lấy khóa ghi live của runtime **rồi** `RUN_LOCK`, cùng thứ tự với `Orchestrator._simulate` và `POST /scenarios/run`, nên hỏi AI đúng lúc tick đang tính lại sẽ chờ thay vì "Conflict on update". Khóa chuyển sang `app/simulator/locks.py` (`RUN_LOCK`, `live_write_lock`, `simulation_locks`); runtime đăng ký khóa của nó khi `start()` (gỡ khi `stop()`) để công cụ AI (không có `Request`) tìm được; không có runtime (unit test, script) thì khóa live là no-op. `ToolContext.live_lock` cho phép truyền khóa khác. `api/scenarios.py` và `orchestrator/recompute.py` import từ module mới. Công cụ nháp (`draft_decision`…) ghi `decisions`/`audit_log` giống các endpoint quyết định, không đụng hàng của orchestrator, nên không lấy khóa (như API). Test: `tests/api/test_scenarios.py::test_ai_compute_tools_wait_for_the_simulation_locks[live|run]` (giữ từng khóa 0,3 s; mô phỏng chỉ bắt đầu sau khi nhả, và lúc chạy giữ cả hai khóa; bỏ khóa trong `compute.py` thì test đỏ).
