# M3 · Quyết định phần web (WEB-02, WEB-05)

Ghi các quyết định tự đưa ra khi làm thành phần dùng chung, ZoneBar và màn So sánh lựa chọn. Mỗi mục: vấn đề, lựa chọn, lý do, ảnh hưởng. Mục 10 liệt kê những gì web cần từ API ở đợt sau.

## 1. `breakeven.ts` dùng chung công thức với backend (ECO-04)

- **Vấn đề:** 09 §2.3 cần kéo thanh trượt là ZoneBar đổi ngay (< 100 ms), không chờ mô phỏng. 12 (ECO-04) yêu cầu frontend và backend dùng chung một công thức.
- **Lựa chọn:** `web/src/lib/breakeven.ts` là bản TypeScript thuần của 05 §7.1. Đầu vào đúng tên trường của body `POST /scenarios/breakeven` (08 §3.8), đầu ra đúng `{zone_bar, flip_leftover_value_vnd}`. `continue` bán đều `rate_continue_per_hour` và chi `spend_per_hour_vnd` tới khi hết hàng hoặc hết giờ, nên `Π_c(V) = m·sold − spend + V·(n − sold)`. Điểm đổi `V*` tính tại p10 (từ giá trị này trở lên thì pause tốt hơn trên cả khoảng dự báo), dùng công thức tổng quát `V* = (P0 − m·q)/(n − q − L_c)`.
- **Ca biên:** `m ≤ V` → `zone_bar = null`; `q* ≤ 0` → ngưỡng 0, `pause_better`; `q* > n` → ngưỡng `n/H`, `continue_better`. Có test cho cả ba. Với chi ads ≥ 0 và m > V thì `q* ≤ n` luôn đúng, nên ca `q* > n` chỉ xảy ra khi chi âm (vd sàn trợ giá ads); vẫn giữ để khớp 05.
- **Test bắt buộc (vitest, xanh):** n=120, H=4, m=45.000, Π_c=4.050.000 → x* = 22,5 (V=0), 16,5 (V=20.000); V* = 16.875đ khi p10 = 18.
- **Ảnh hưởng:** backend ECO-04 nên chạy cùng bộ số. Nếu `breakeven.py` chọn khác (vd V* tại p90), sửa một hàm ở web.

## 2. Đề xuất thêm `ZoneBar.inputs` (tùy chọn)

- **Vấn đề:** `Recommendation` ở 05/08 không mang n, H, m, Π_c, nên trình duyệt không tự tính lại thanh vùng được.
- **Lựa chọn:** thêm trường tùy chọn `zone_bar.inputs: BreakevenRequest | null` (08 §1: thêm trường không phá vỡ). Có `inputs` thì kéo "giá trị hàng dư" vẽ lại ZoneBar ngay bằng `breakeven.ts`, ghi "Ước tính nhanh trên trình duyệt (công thức hòa vốn), chưa chạy lại mô phỏng". Không có thì chỉ chờ server.
- **Ca CD002 11.11 không có `inputs`:** thanh vùng của 08 (ngưỡng 3,1; khoảng 4,2–7,0) là số minh họa. Với Π_c = −4,48 triệu thì công thức đóng cho q* < 0 (pause thắng chắc), không ra 3,1. Bịa `inputs` cho khớp 3,1 là không trung thực, nên fixture CD002 không có `inputs`. Ở chế độ fixture, thanh "hàng dư" của ca này bị ẩn, chỉ còn thanh "hàng về sớm/trễ" (ước tính theo `flip_conditions`).

## 3. Ba ca fixture cho màn So sánh (minh họa)

| Ca | Đường dẫn | Kết luận | Mục đích |
|---|---|---|---|
| CD002 11.11 (`rec-551`, `scn-2291`) | `/sku/NOICHIEN2L/compare`, `/campaign/CD002/compare` | `schedule_hours` thắng; `pause` và `pause_until_restock` `feasible=false` vì `campaign_must_run` | Ca chính 05 §10, số đúng 08 §3.8 |
| CD002 ngày thường 05/11 (`rec-540`, `scn-2280`) | `/campaign/CD002/compare?rec=rec-540` | `continue` thắng, thanh vùng `continue_better` (ngưỡng 3,49; khoảng 1,8–3,1) | Cặp "cùng chiến dịch, kết luận ngược" của 05 §10 |
| Ví dụ trang 10 (`rec-510`, `scn-2210`, chiến dịch giả `VIDU-T10`) | `/campaign/VIDU-T10/compare?rec=rec-510` | `undetermined`, ngưỡng 22,5 với khoảng 18–31; kéo hàng dư lên 20.000đ → 16,5 → `pause_better` | Yêu cầu 09 §2.3 "màn này phải chạy đúng ví dụ trang 10" |

- Ca ngày thường và ca trang 10 có `zone_bar.inputs` khớp đúng `breakeven.ts` (test kiểm 3,4946 và 3,339).
- Mỗi file fixture mới có trường `_note` ghi rõ là minh họa. App bỏ qua trường này.
- Tham số `?rec=<id>` trên route `/campaign/:id/compare` để mở một đề xuất cụ thể (kể cả đề xuất cũ). Không có `?rec` thì lấy đề xuất đang hiệu lực của chiến dịch từ `GET /recommendations`.
- `/compare` (không tham số) thành trang chọn: danh sách đề xuất đang hiệu lực, và ở chế độ fixture thêm ba ca minh họa.

## 4. `ActionSpec.pct` là tỷ lệ

- **Vấn đề:** 05 §1 ghi `pct: 0.3 = giảm 30%`; fixture M0 ghi `30` và `50`.
- **Lựa chọn:** theo 05: fixture đổi về `0.3`, `0.5`. Hàm `pctOf` trong `i18n.ts` đọc `≤ 1` là tỷ lệ, `> 1` là phần trăm để không vỡ nếu backend gửi kiểu cũ.

## 5. Kiểu nullable theo 05

`Recommendation.best`, `p_best_beats_runner_up`, `expected_gain_vnd`, `zone_bar` đổi thành `| null` đúng 05 §1 (ca `none_meets_goal`). Tổng quan ca và test đã sửa theo. Thêm alias trong `types.ts`: `ZoneVerdict`, `AltThreshold`, `BreakevenRequest`, `BreakevenResponse`, `FlipCondition`, `InputsRef`, `SimRequest`, `DecisionCreate`, `DecisionCreated`.

## 6. Lý do có số dạng CitationChip

- **Vấn đề:** `reasons` ở 05/08 là chuỗi trần, không biết mỗi lý do lấy từ nguồn nào.
- **Lựa chọn:** đề xuất trường tùy chọn `reason_sources: (Source | null)[]` cùng thứ tự `reasons`; `Source` thêm tùy chọn `data_as_of`, `tool`, `href`. Thiếu thì chip trỏ về mô phỏng của đề xuất. Con số chính trong câu (ưu tiên số có đơn vị hoặc giờ: "9.280đ", "13:00", "18–31 sp/giờ", không lấy "11.11") là nút mở popover nguồn.

## 7. Thanh trượt và gọi lại mô phỏng

- Chế độ API thật: kéo → `POST /scenarios/run` với `param_overrides` (`leftover_value_per_unit`, `restock_delay_days`, đúng khóa 05 §3), `samples=300`, debounce 250 ms; thả tay (pointerup/keyup/blur) → `samples=1000`. Kết quả cũ chỉ được dùng nếu khớp giả định hiện tại; trong lúc chờ, tầng 1 mờ 60% kèm "Đang cập nhật · dữ liệu lúc …".
- Chế độ fixture: không gọi mạng, chỉ ước tính bằng `breakeven.ts` và so với `flip_conditions` ("Với giả định này đã chạm điểm đổi: …"). Ghi rõ là ước tính nhanh.
- Thanh "hàng về trễ" cho phép −3 tới 7 ngày (số âm = về sớm), vì ví dụ 08 có `flip_at: -1` trong khi 05 §3 ghi khoảng 0–7. Backend cần nhận giá trị âm, hoặc báo lại để web giới hạn từ 0.
- Thanh "giá trị hàng dư" có max = m (đóng góp mỗi sp) khi có `inputs`, vì từ m trở lên công thức không còn thanh vùng (hiện câu "Giữ hàng lợi hơn bán…"). Không có `inputs` thì max tạm 1.000.000đ; đúng ra là giá bán (05 §3), cần API trả.
- `SimRequest` khi gọi lại: dùng `ScenarioRun.request` nếu API trả (đề xuất thêm), nếu không thì dựng từ `inputs_ref` (`start` = giờ tròn kế tiếp sau `data_as_of`, `horizon_hours = null` để backend lấy mặc định theo goal).

## 8. Form "Tạo quyết định"

Form tối giản: chọn hành động (hành động vi phạm ràng buộc hiện nhưng không chọn được), lý do (điền sẵn từ `reasons`), báo "Khác với đề xuất" khi ghi đè, báo "đang dùng giả định" khi thanh trượt đã đổi. Nút "Lưu bản nháp quyết định" gọi `POST /decisions` có `Idempotency-Key`, sau đó "Gửi duyệt" gọi `/decisions/{id}/submit`. Nút bị khóa kèm lý do hiện ngay dưới: chế độ dữ liệu mẫu, vai trò Xem, hành động vi phạm, thiếu lý do. Nút "Báo lead" của ca `none_meets_goal` khóa kèm lý do (chưa có API).

## 9. Thành phần dùng chung (WEB-02)

- `ConfidenceBadge`, `ComputeStateTag` giữ từ M0. `FreshnessBadge` thêm tooltip "trễ 2 giờ 4 phút (ngưỡng 3 giờ)", "thiếu 17 mã" theo 09 §4.
- Mới: `CitationChip` (popover: loại nguồn, mã, công cụ, dữ liệu tới, tính lúc, link; Esc / bấm ngoài để đóng), `MoneyVND` (đủ / `tr` / `triệu`), `Range` (`(−4,9 … −4,1)`, có `aria-label`), `Banner` (vàng: dữ liệu cũ; xám: kịch bản), `UpdatingRegion` (mờ 60% + "Đang cập nhật · dữ liệu lúc 13:05"), `ZoneBar`.
- `ZoneBar` là SVG tự vẽ, không thêm thư viện. Màu vùng là hai token mới `--zone-continue` / `--zone-pause` (xanh nhạt / vàng nhạt, có bản tối), không mang nghĩa tốt/xấu. Ngưỡng chính vạch liền, ngưỡng phụ vạch đứt, khoảng p10–p90 là thanh dày, p50 là chấm rỗng viền đậm. `role="img"` + `aria-label` đọc đủ câu kết luận, ba con số và các ngưỡng phụ. Câu kết luận và số cũng in dưới thanh dạng chữ.
- Hàm phụ của ZoneBar nằm ở `lib/zoneBarText.ts` để file component chỉ export component (luật react-refresh).
- Không thêm gói npm nào.

## 10. Cần API ở đợt sau

| Endpoint | Trường / hành vi | Dùng cho |
|---|---|---|
| `GET /recommendations/{id}`, `POST /scenarios/run` | `zone_bar.inputs` (= body `POST /scenarios/breakeven`) | Vẽ lại ZoneBar ngay khi kéo |
| `GET /recommendations/{id}` | `reason_sources[]` cùng thứ tự `reasons`; `Source.data_as_of`, `tool`, `href` | CitationChip cho từng lý do |
| `GET /scenarios/{id}`, `POST /scenarios/run` | `request` (SimRequest đã chạy, gồm `samples`, `seed`, `horizon_hours`) | Gọi lại đúng tham số; tầng 3 |
| `POST /scenarios/run` | Nhận `restock_delay_days` âm (hàng về sớm), `meta.compute_state`, `meta.cache_hit` | Thanh trượt; trạng thái đang tính |
| `GET /recommendations?campaign_id=` | Lọc theo chiến dịch | `/campaign/:id/compare` khỏi tải cả danh sách |
| `GET /skus/{sku}` hoặc `GET /campaigns/{id}` | Tên chiến dịch, danh sách SKU, giá bán (max của thanh hàng dư) | Tiêu đề màn và giới hạn thanh trượt |
| `GET /goals` | Nhãn mục tiêu theo `goal_id` | "Mục tiêu: Lợi nhuận trong ngày 11.11" thay cho mã |
| `POST /decisions`, `/decisions/{id}/submit` | Như 08 §3.10 | Bật nút Lưu bản nháp / Gửi duyệt |
| `ScenarioResult` | `leftover_units`, `spend`, `units_sold` đủ cho mọi hành động khả thi | Bảng tầng 3 (hiện "—" khi thiếu) |

Đợt API M3 đã đáp ứng các dòng trên, trừ `/decisions` (M4). Xem mục 11–13.

---

# Đợt cuối M3 · Nối giao diện với API thật

Hai màn **Tổng quan ca** (`/`) và **So sánh lựa chọn** (`/sku/:sku/compare`, `/campaign/:id/compare[?rec=]`, `/compare`) đọc API thật khi `VITE_USE_FIXTURES=false`. Chế độ fixture vẫn chạy (dự phòng demo). Fixture đã đổi theo đúng hình dạng phản hồi thật.

## 11. Cách chạy web với API thật

```bash
# 1. Backend (DB demo đã ingest/backtest/fit/estimate-ads)
cd backend && uv run uvicorn app.main:app --port 8000
# 2. Web: VITE_USE_FIXTURES=false (trong .env.local hoặc file env theo mode, xem dưới)
cd web && npm run dev            # http://localhost:5173, proxy /api → VITE_PROXY_TARGET (mặc định :8000)
```

Chạy song song với dev server fixture mà không đụng `.env.local`: tạo `web/.env.e2e` gồm `VITE_USE_FIXTURES=false` và `VITE_PROXY_TARGET=http://localhost:8001`, rồi `npx vite --mode e2e --port 5174 --strictPort`. File `.env.[mode]` thắng `.env.local`. Backend: `DUCKDB_PATH=data/<bản sao>.duckdb uv run uvicorn app.main:app --port 8001`.

Sinh lại type: `OPENAPI_URL=http://localhost:8001/api/v1/openapi.json npm run gen:api`. Đã chạy, kể cả sau khi agent simulator thêm `pause_feasible`, `pause_violations`, `verdict_note`, `runner_up_meets_goal`, `goal_cost_vnd`, `min_rule`.

URL: `/` · `/compare` · `/sku/NOICHIEN2L/compare` · `/campaign/CD002/compare` · `/campaign/CD005/compare` · `/campaign/CD006/compare` (chưa có mục tiêu) · `/campaign/CD010/compare` (thiếu giá vốn) · `/campaign/<id>/compare?rec=<recommendation_id>`.

## 12. Quyết định

### Q-W1. Kiểu sinh từ OpenAPI thật, thu hẹp trong `types.ts`
- Bỏ schema viết tay. Alias theo tên Pydantic: `SkuSummary`, `RecommendationOut`, `ScenarioResultOut`, `ScenarioRunRequest` (body), `SimRequestOut` (`ScenarioRun.request`), `InputsRefOut`, `SourceRef`, `SourceFreshness`, `BreakevenOut`, `CampaignDetail`, `GoalIn/GoalOut/GoalBrief`, `Me`, `Health`.
- Backend khai vài trường là `str`/`dict` (`SkuSummary.confidence/compute_state/deviation`, `RecRef.best`, `CampaignDetail.commitments`). `types.ts` thu hẹp về đúng giá trị của M3-api §2 (`SkuListItem`, `SkuRecRef`, `Commitment`).
- openapi-typescript coi trường có mặc định Pydantic là bắt buộc (`pause_feasible`, `pause_violations`, `runner_up_meets_goal`). Ở web chúng là tùy chọn (`Defaulted<>`): thiếu = mặc định, để fixture và payload cũ vẫn hợp lệ.
- Endpoint M4 (decisions, alerts, live): kiểu viết tay ở `api/pending.ts`. `ApiErrorBody` viết tay vì FastAPI không đưa khung lỗi vào OpenAPI.

### Q-W2. Tổng quan ca
- "Cần xem ngay": trang đầu `GET /skus?sort=priority&limit=200`, lọc theo 09 §2.1 (cảnh báo mở, đề xuất khác "Tiếp tục", hoặc `none_meets_goal`). SKU hết hàng mà không có cảnh báo hay đề xuất **không** vào khối. Chúng vẫn đứng đầu bảng (`priority_score` 50). Khi có API cảnh báo (M4), chúng vào khối qua `open_alerts`.
- **Một thẻ cho một quyết định:** gom các dòng cùng `recommendation_id` (3 size áo = 1 thẻ "3 phân loại"; nồi chiên + giấy lót + combo = 1 thẻ "và 2 SKU cùng chiến dịch"). Không có đề xuất thì gom theo `product_id`. Lý do: với dữ liệu thật, gom theo sản phẩm làm CD002 hiện 3 thẻ trùng nội dung. Nút "So sánh" mở `/sku/<SKU dẫn đầu>/compare`.
- "Còn 83 sp · hết khoảng 17:00" = `meta.data_as_of` + `hours_of_cover.p50`. `p50 = null` → "chưa hết trong 72 giờ tới", cột "Đủ bán" ghi "> 72".
- Bảng toàn bộ: `useInfiniteQuery`, `limit=50`, nút "Tải thêm" theo `next_cursor`. Có ô tìm (`q`, debounce 300 ms) và sắp xếp (`priority|sku|cover|sold_today`). Tiêu đề "Toàn bộ SKU (N)" và "Đã tải n/N mã" theo `meta.total` (thêm ở M3-api §6 S5). Không có `meta.total` thì ghi "N mã đã tải, còn nữa".
- `/me` → "Vai trò: Vận hành (u-ops-1)". `/freshness` có nguồn `stale|missing` → băng vàng 09 §4. Dòng `computing|queued` mờ 60% + "Đang cập nhật". Lỗi → `ErrorState` (thông điệp, mã lỗi, "Thử lại"). Rỗng → "Chưa có dữ liệu SKU" + "Nhập dữ liệu".

### Q-W3. So sánh lựa chọn: chuỗi gọi
`/sku/:sku/compare` → `GET /skus/{sku}` (lấy chiến dịch) → `GET /campaigns/{id}` (tên, SKU, `price_max_vnd`, `goal`, `commitments`) → `GET /recommendations?campaign_id=&active=true` (bỏ dòng có `superseded_by`; fixture bỏ qua query nên lọc thêm ở client) → `GET /recommendations/{id}` → `GET /scenarios/{scenario_id}`. Có `?rec=` thì bỏ bước danh sách. Ở chế độ fixture, `GET /skus/{sku}` trả dòng tương ứng của `skus.json`; thêm fixture `campaign-CD002.json` (lấy từ phản hồi thật).

### Q-W4. Chưa có đề xuất: mục tiêu và lần chạy đầu
- Chiến dịch chưa có mục tiêu (hoặc `/scenarios/run` trả 422 `details.missing = "goal"`): vai trò có `can_edit_goals` (lead) thấy form "Đặt mục tiêu" (loại; hạn mặc định 00:00 hôm sau theo đồng hồ dữ liệu; số sp khi `sell_target`; lý do) → `POST /goals` → `POST /scenarios/run` (1.000 mẫu, seed 20261111) → mở `?rec=` mới. Vai trò khác đọc "Nhờ trưởng nhóm đặt mục tiêu…".
- Quyền lấy từ `/me`. Khi `/me` chưa trả thì tạm theo `VITE_DEV_ROLE`. Sửa lỗi thấy khi chạy thật: trước đó operator thấy câu dành cho vai trò Xem trong lúc `/me` đang tải.
- Có mục tiêu mà chưa có đề xuất: nút "Chạy mô phỏng (1.000 mẫu)".
- 422 `BLOCKING_DATA_ISSUE` có `details.reasons` (vd CD010 thiếu giá vốn): khung "Chưa tính được vì thiếu dữ liệu" liệt kê lý do + nút "Nhập dữ liệu".

### Q-W5. Thanh vùng khi tạm dừng bị cấm, khi không có thanh
- `zone_bar.pause_feasible === false`: `verdict_note` **thay** câu verdict, đặt trong khung vàng `role="note"` và trong `aria-label`. Vùng pause mờ 40% + gạch chéo, nhãn "Pause (bị cấm)" gạch ngang. Không thêm dòng "Pause tốt hơn tiếp tục, nhưng…" để khỏi lặp. Khi kéo V, ước tính trên trình duyệt giữ `pause_feasible=false` và viết lại câu theo verdict mới (cùng câu với `recommend.mark_pause_blocked`).
- `zone_bar = null`: câu theo lý do. Có lý do bắt đầu "Giữ hàng lợi hơn bán" → "Không vẽ thanh vùng vì giữ hàng lợi hơn bán ở giá sale (…): về tiền, tạm dừng luôn hơn tiếp tục." `best = null` → "chưa so sánh được các hành động". Còn lại dùng câu chung.
- `runner_up_meets_goal === false` + `goal_cost_vnd`: "X. Chỉ phương án này đạt mục tiêu; Y lợi hơn khoảng … nhưng không đạt."

### Q-W6. Thanh trượt và `POST /scenarios/run`
- Gửi lại `ScenarioRun.request` (đã có `start`, `horizon_hours`, `seed`), chỉ đổi `samples` (300 khi kéo, debounce 250 ms; 1.000 khi thả) và `param_overrides`. Đã kiểm: gửi cùng body lần hai trả cùng `scenario_id`, `meta.cache_hit = true`, 0,18 s.
- **(Đã bỏ chỗ né)** Trước đây web bỏ `leftover_value_per_unit` và `restock_delay_days` có sẵn trong `request` echo (§13 mục 1–2). Hai lỗi đó đã sửa ở backend: `request` echo là đúng request đã chạy. Nay web giữ `param_overrides` của nó, thanh trượt ghi đè khóa của mình.
- V: min = −(giá vốn lớn nhất của SKU không phải combo), max = `price_max_vnd`, kẹp ở client. 400 `PARAM_OUT_OF_RANGE` vẫn hiện thông điệp của API. Nhãn mới "Giá trị ròng 1 sp còn lại sau hạn, so với giá vốn (V)", ghi "0đ = bán sau vẫn hòa vốn". Chưa chạm thì hiện "mặc định theo từng SKU", hoặc V của `zone_bar.inputs` nếu có. Override không có `@SKU` nên áp cho mọi SKU của chiến dịch; ghi rõ dưới thanh.
- Hàng về trễ: −7..7 ngày. Fixture giữ hành vi cũ (chỉ ước tính, max V = m khi có `inputs`).

### Q-W7. Nguồn trích dẫn
`Source.href` thật là đường API. `webHref` đổi `/api/v1/skus/X[/…]` → `/sku/X`; `/api/v1/scenarios|campaigns|recommendations/…` → `#tier-3`. Tầng 3 có thêm mục "Cam kết chương trình sale" lấy từ `commitments`. Thêm nhãn loại `commitment`, `snapshot`, `panel`. Khi tách số chính trong câu, khoảng giờ "00:00–02:00" giữ nguyên một cụm.

### Q-W8. Tầng 3 và thẻ hành động
`param_version` là object → "N tham số, đều là mặc định (v0)", hoặc liệt kê các khóa khác 0 (`-1` = giả định của lần chạy). Thêm dòng "Bắt đầu · kỳ" lấy từ `request`. Thẻ hành động không khả thi hiện `violation_details` và "Nếu bỏ ràng buộc: <E[J]>" (cái giá của ràng buộc). `duplicate_of` (đã có trong `ScenarioResult`) hiện "Kết quả trùng hệt …".

## 13. Chạy thử end-to-end (30/10)

Backend `:8001` trên bản sao DB demo (dữ liệu tới 11/11 00:00; bản sao đã xóa sau khi chạy), web `:5174` mode `e2e`. Mọi lệnh đi qua proxy `http://localhost:5174/api/v1`.

**Kiểm bằng curl qua proxy:**
- Tổng quan: `/me`; `/freshness` (6 nguồn, giá vốn `partial`); `/skus?sort=priority&limit=200` (200 dòng + `next_cursor`); trang 2 bằng `cursor`; `q=noi`; `/recommendations?active=true` (5 đề xuất).
- So sánh: `/skus/NOICHIEN2L`; `/campaigns/CD002` (`price_max_vnd` 690.000, mục tiêu, 2 cam kết); `/recommendations?campaign_id=CD002&active=true`; `/recommendations/{id}` (8 `reason_sources`); `/scenarios/{id}` (6 kết quả, pause và pause_until_restock `feasible=false`).
- `POST /scenarios/run`: 300 mẫu 0,76 s; 1.000 mẫu 0,83 s; gửi lại 0,18 s `cache_hit=true`. V = 900.000 → 400 `details.max = 690000`.
- CD006 không có mục tiêu → 422 `missing=goal`. Operator `POST /goals` → 403. Lead `POST /goals` → 201, rồi run → đề xuất mới (0,40 s). CD010 → 422 kèm 2 lý do. Viewer run → 403. `/alerts` → 404 (M4).

**Kiểm bằng render thật trong jsdom.** Một test tạm chuyển `api.get/post` sang server thật qua proxy; đã xóa sau khi chạy.
- Tổng quan ca hiện 5 thẻ (CD002 gộp 3 SKU, áo 3 phân loại, bình nước, cốc meme, đèn LED), "Lợi hơn ~1,2 tr". Bảng có SKU hết hàng đứng đầu. Trang gọi đúng `/freshness`, `/skus?sort=priority&limit=200`, `/recommendations?active=true`, `/me`, `/skus?sort=priority&limit=50`.
- So sánh CD002 qua route SKU: "Chỉ chạy 0–2h, 12–14h. Lợi hơn giảm ngân sách 50% khoảng 1,2 triệu đ."; thanh vùng x* = 0 kèm `verdict_note`; 2 thẻ "Không làm được" kèm "Nếu bỏ ràng buộc"; chip nguồn; thanh V từ −455.000 tới 690.000. Kéo V → 2 lần `POST /scenarios/run` (300 rồi 1.000 mẫu, đúng `start/horizon/seed` của request echo) → "Đã chạy lại mô phỏng … (1.000 mẫu)".
- CD010: bấm "Chạy mô phỏng" → khung thiếu giá vốn.

**Chưa kiểm:** không có trình duyệt, nên **chưa nhìn bằng mắt** bố cục, màu, gạch chéo vùng pause, popover, chế độ tối, màn hẹp. Chưa đo thời gian vẽ lại khi kéo trên trình duyệt thật. Form mục tiêu chỉ kiểm bằng test (API giả) và curl, chưa bấm trên server thật. "Lưu bản nháp/Gửi duyệt" chưa có API (M4).

**Vấn đề API phát hiện** (mục 1–6 đã sửa, xem M3-api §6 "Sửa lỗi nhỏ M3"):
1. **[Đã sửa]** `GET /scenarios/{id}` (dựng lại từ DB) của đề xuất CD002 đang hiệu lực trong DB demo trả `request.param_overrides = {"leftover_value_per_unit": 9000000}` và `actions` đủ 6 hành động. Gửi lại nguyên văn → 400. Có vẻ `request` được dựng từ một lần chạy khác, hoặc override được lưu trước khi kiểm hợp lệ. Web đã né (Q-W6).
2. **[Đã sửa]** Mỗi `POST /scenarios/run` có `param_overrides` (kéo thanh trượt) **tạo đề xuất mới và thay đề xuất đang hiệu lực** của chiến dịch. Mở lại màn thì "đề xuất" là kết quả what-if của người vừa kéo. Nên: lần chạy có override (hoặc có `session_id`) không supersede, hoặc API có cờ không lưu.
3. **[Đã sửa]** `violation_details` chứa giờ ISO UTC thô ("từ 2026-11-10T17:00:00+00:00"); nên là giờ VN ("00:00 11/11").
4. **[Đã sửa]** `CampaignDetail.recommendation.data_as_of` trả dạng `Z`, khác quy ước `+07:00`.
5. **[Đã sửa]** `/skus` không có tổng số dòng (09 §2.1 "Toàn bộ SKU (300)"). `CampaignDetail.goal.label` chỉ là "Lợi nhuận" (không có hạn như `/goals`); web tự ghép hạn.
6. **[Đã sửa]** `ScenarioResult.duplicate_of` chưa có trong OpenAPI (chỉ có ở `Selection` nội bộ).
7. Chưa có `/alerts`, `/decisions` (M4): khi chạy API thật, số cảnh báo trên thanh trên cùng là 0.
