# Nhật ký quyết định · Kinh tế + mô phỏng quyết định (M3)

Task: ECO-01..04, SIM-01..06. Code: `backend/app/economics/` (`contribution.py`, `parameters.py`, `goals.py`, `breakeven.py`), `backend/app/simulator/` (`schemas.py`, `policies.py`, `inputs.py`, `engine.py`, `constraints.py`, `recommend.py`, `service.py`). Test: `backend/tests/unit/economics/`, `backend/tests/unit/simulator/`, `backend/tests/e2e/test_noichien_1111.py`.

## API công khai

| Hàm / schema | Ở đâu | Việc |
|---|---|---|
| `run_simulation(conn, SimRequest, *, requested_by, forecasts=None, iroas=None, persist=True, with_flips=True, use_cache=True, events=None) -> SimOutput` | `simulator/service.py` | Điểm vào cho `POST /scenarios/run`, orchestrator, công cụ AI. Đọc goal + tham số → `load_inputs` → `engine.simulate` → `build_recommendation` → ghi `scenarios`, `scenario_results`, `recommendations`. `events` nhận `RecommendationChanged` |
| `SimRequest`, `ActionSpec` (`pct`, `windows_local`, `.key`, `.label_vi()`), `Dist`, `SkuOutcome`, `ScenarioResult`, `Recommendation`, `ZoneBar`, `AltThreshold`, `FlipCondition`, `InputsRef`, `SimOutput` | `simulator/schemas.py` | Đúng tên 05 §1 / 08 §3.8. Trường thêm đánh dấu `[DEV]` |
| `IroasPosterior` (Protocol), `PriorIroas`, `EstimatePosterior`, `as_posterior` | `simulator/schemas.py` | Hợp đồng tối thiểu engine cần; `EstimatePosterior` bọc `app.ads_effect.schemas.IroasEstimate` |
| `engine.simulate(inputs, actions, samples, seed, perturb=None, draws=None)`, `simulate_many(jobs)` | `simulator/engine.py` | Lõi numpy, không đụng DB |
| `load_inputs(conn, campaign_id=…, goal=…, params=…, start=…, forecasts=…, iroas=…)` | `simulator/inputs.py` | Đọc DB một lần → `SimInputs` (numpy) |
| `uniform_breakeven(UniformCase)` → `zone_bar`, `flip_leftover_value_vnd` | `economics/breakeven.py` | Cho `POST /scenarios/breakeven` (08 §3.8) |
| `load_contributions`, `contribution`, `bundle_contribution` | `economics/contribution.py` | ECO-01 |
| `CATALOGUE`, `set_parameter`, `confirm_suggestion`, `history`, `load_param_set`, `parse_overrides`, `validate_value` | `economics/parameters.py` | ECO-02 (phần lưu; router để đợt sau) |
| `create_goal`, `get_goal`, `active_goal`, `select_actions` | `economics/goals.py` | ECO-03 |

## Quyết định

### Q1. Common random numbers bằng Poisson nghịch đảo (inverse CDF), không tạo lại `Generator(seed_i)`
Plan 05 §5.3 nói "tạo `Generator(seed_i)` mới cho mỗi hành động với cùng `seed_i`". Với numpy, khi λ đổi thì số uniform tiêu thụ cho mỗi lần rút Poisson cũng đổi, nên chuỗi bị lệch pha giữa các hành động, và nhiễu Poisson của hai hành động gần như độc lập. Engine rút **một** mảng uniform `U[s,t]` cho mỗi SKU và tính `demand = F⁻¹(U; λ)` cho mọi hành động. Cách này là phiên bản chính xác của cùng ý tưởng. Hệ quả có test: hành động chi nhiều hơn không bao giờ bán ít hơn trong cùng mẫu (khi còn hàng), `P(A>B)` dao động ≤ 0,04 giữa 4 seed. `Lmult`, `G`, `(κ, a)` cũng rút một lần. Với λ > 40 dùng xấp xỉ chuẩn có hiệu chỉnh liên tục.

### Q2. `profile_spend` = tỷ lệ chi theo giờ **trên ngân sách ngày**
"Tỷ trọng chi theo giờ" có thể hiểu là chi giờ h / tổng chi ngày. Engine dùng chi giờ h / ngân sách ngày đó, trung bình 7 ngày thường đủ 24 giờ gần nhất (ngày đôi: ngày đôi gần nhất, 10.10). Như vậy chiến dịch không tiêu hết ngân sách vẫn đúng mức. Ngày đôi 10.10 của CD002 tiêu hết 5,59 triệu lúc 20:00. Ngân sách ngày: `ad_hourly.daily_budget` của chính ngày đó nếu có; nếu chưa có (ads trễ 2 giờ lúc 01:00) thì lấy của ngày đôi trước, hoặc ngày thường gần nhất, hoặc `campaigns.daily_budget`. Chi trong ngày trước `start` mà chưa có số (do trễ ads) được ước bằng `budget·profile[h]`. `reduce_budget(pct)` giảm cả trần ngân sách ngày.

### Q3. `schedule_hours` mặc định
Nếu có `campaign_must_run`: khung giờ cam kết đầy đủ (CD002: `[(0,2),(12,14)]`, kể cả khi `start` = 01:00). Nếu không có: các giờ VN có nhu cầu nằm trong nửa cao nhất (`policies.peak_windows`). Plan chưa nói hành động này lấy khung giờ nào ở ngày thường. `pause_until_restock` chỉ có mặt khi chiến dịch có kế hoạch hàng về.

### Q4. Hạn, giờ bắt đầu, chỉ dùng dữ liệu trước `start`
`start` mặc định = giờ tròn kế tiếp sau `as_of` của đơn/tồn. `horizon` = tới `goal.deadline`, tối đa 72 giờ. Mọi đầu vào (tồn tại `start` từ panel, kế hoạch hàng về `seen_at < start`, ads, flash đã bán) đều lấy trước `start`, nên chạy lại ở một mốc quá khứ (ví dụ cốc meme 05/11) cũng đúng như lúc đó. Riêng giá vốn: `valid_from` là lúc nhận file. Nếu mọi dòng đều nhận sau `start` thì dùng dòng sớm nhất, nếu không thì mô phỏng quá khứ sẽ luôn bị chặn.

### Q5. Flash reserve
Phần giữ của mỗi SKU có tồn = phần giữ riêng + Σ `qty ×` phần giữ của combo chứa nó (9 combo giữ 9 nồi + 9 giấy lót), trừ đi lượng đã bán trong khung flash từ đầu ngày tới `start`. Phần giữ chỉ có hiệu lực trong ngày cam kết, trước khi khung flash cuối cùng kết thúc. Sau đó phần này được bán như hàng thường.

### Q6. Giá trị hàng dư V theo đúng mặc định plan (0,5 × giá vốn) → CD002 không có thanh vùng
> **Đã thay (sau M3-whatif):** V mặc định = 0, định nghĩa là giá trị ròng so với giá vốn. Xem mục "Sau sửa lỗi M3-whatif" cuối file.

Với V = 227.500đ/nồi, lớn hơn m = 9.280đ, công thức 05 §7.1 cho `m ≤ V`: "giữ hàng luôn lợi hơn bán", nên `zone_bar = null` kèm lý do "Giữ hàng lợi hơn bán: mỗi sp còn lại đáng 53.640đ, bán chỉ lãi 12.407đ" (tính bình quân theo chiến dịch). Ví dụ JSON ở 08 có thanh vùng cho CD002 nhưng đó chỉ là minh họa. Ngày thường của nồi chiên cũng vậy (m = 149.200đ < V = 227.500đ). Mặc định V = 0,5 × giá vốn không khớp với cách đọc "V = 0 nghĩa là hàng dư vô giá trị" của ví dụ trang 10. **Cần chủ plan chốt**: đặt V mặc định = 0 (cách đọc trang 10) hoặc theo kế hoạch bán tiếp. Kết luận CD002 không đổi vì mọi hành động đều bán hết nồi.

### Q7. Thanh vùng trong trường hợp thật (05 §7.2), nhiều SKU
`n` = tồn đầu + hàng về trong horizon của các SKU có tồn, `m̄` = E[đóng góp(continue)] / E[đơn vị tồn đã bán], `V̄` = V bình quân theo tồn, Π_c = E[J(continue)], tốc độ khi pause = đơn vị tồn bán được (combo tính theo thành phần) / H. Có thêm đường so sánh "hàng dư 0đ" khi V̄ > 0.

### Q8. Điều làm đổi kết luận
- **V:** J tuyến tính theo V nên tìm nghiệm đúng (không cần bisection), trong khoảng [0, giá bán].
- **Mức nhu cầu** và **hiệu quả ads:** bisection 6 bước, 300 mẫu, cùng seed, trong [p10, p90] (nhu cầu: `exp(±1,28σ)` của SKU chính; ads: p10/p90 của `a` đã rút).
- **`restock_delay_days`:** duyệt số nguyên từ "về trong hôm nay" (âm, như 05 §10) tới +7. Khoảng tham số người dùng vẫn là 0–7; riêng `param_overrides` nhận −7..7 cho what-if "hàng về sớm".
- Câu hiển thị ghi rõ bên nào thắng sau điểm đổi.
- Với `clear_stock`: thứ hạng theo số bán, nên so tiền với runner-up (cũng xả hàng) gần như không bao giờ đổi dấu. Điều kiện đổi tính giữa `best` và `pause` (giữ hàng), vì đây mới là đánh đổi tiền thật của việc xả hàng. Ví dụ LED: "Nếu hàng dư còn giá trị từ ~2–11 nghìn đ/sp trở xuống → Chạy tiếp có lợi hơn về tiền".

### Q9. Quy tắc đề xuất cho `clear_stock` và `efficiency`
- `clear_stock`: `p` = P(số bán best > runner-up). Đề xuất khi `p ≥ decision_min_prob` và bán thêm ≥ 1 sp. `expected_gain_vnd` vẫn là chênh E[J] (có thể âm).
- `efficiency`: "cho mọi phần chi thêm" = so với **mọi** hành động khả thi chi ít hơn: (ΔE[J]) / (Δchi) ≥ `min_marginal_return`. ΔJ theo đúng công thức plan (J đã trừ chi).
- Không hành động nào đạt thì chọn hành động khả thi chi ít nhất.

### Q10. Nhãn dữ liệu
- `scenario` khi: đơn/tồn cũ hơn 24 giờ, không có số liệu ads từ hôm trước, hoặc thiếu giá vốn/giá bán. Trễ ads 2 giờ trong live là bình thường nên không hạ nhãn.
- Không có số tồn hoặc combo chưa khai thành phần → tối đa `reference`.
- Tham số kịch bản ≠ 0 → tối đa `reference` (05 §3).
- Thiếu giá vốn → **không mô phỏng**: `SimOutput.blocked = true`, `results = []`, đề xuất `undetermined`, nhãn `scenario`, lý do "Thiếu giá vốn: …".

### Q11. Lưu và thay thế
- Mỗi lần chạy ghi một `scenarios` + một `scenario_results` cho mỗi `action.key`.
- Đề xuất cũ đang hiệu lực của chiến dịch nhận `superseded_by` (không xóa).
- `RecommendationChanged` chỉ phát khi best hoặc status đổi.
- `param_version` = `{key: version}`: `0` là mặc định danh mục, `-1` là override của lần chạy, `key@SKU` là giá trị riêng của SKU.
- Cache trong bộ nhớ (LRU 256) theo khóa 05/06 cộng thêm actions, overrides, samples, seed, goal.version. Trúng cache thì trả `cache_hit=true` và không ghi gì. Khóa cũng được ghi vào `scenarios.input_hash`.

### Q12. iROAS: **đang dùng thật**
Lúc bắt đầu, `app.ads_effect` chưa có gì, nên simulator viết theo `IroasPosterior` (Protocol) cộng `PriorIroas` (prior 04 §5.2 trên lưới 6 × 61, nhãn `scenario`). Agent ads đã công bố `IroasEstimate` (`sample_params`, `spend_ref`, `delta`) và `iroas.load_estimate`. `inputs.load_iroas` nay đọc ước lượng mới nhất qua `load_estimate` và bọc bằng `EstimatePosterior`. Chiến dịch chưa có ước lượng thì quay về prior. `run_simulation(iroas=…)` nhận cả `IroasEstimate` lẫn `IroasPosterior`. Adstock ban đầu tự tính từ `ad_hourly` 48 giờ trước `start`, dùng `delta` của ước lượng, không dùng `adstock_last` (mốc cuối giai đoạn ước lượng ≠ `start`). Giai đoạn ước lượng có thể kết thúc sau `start` khi mô phỏng quá khứ: chấp nhận, vì trong ca không ước lượng lại (04 §7).

### Q13. Hiệu năng
- Bỏ dòng lặp cho các hành động có cùng lịch chi (ví dụ pause ≡ pause_until_restock khi hàng về sau horizon).
- SKU thành phần ngoài chiến dịch chỉ rút cầu một lần.
- Poisson: bước k = 0 vector hóa, sau đó chỉ đi tiếp trên phần chưa xong.
- `simulate_many` mặc định 4 luồng: đo trên máy 16 luồng, 8 luồng chậm hơn vì mảng nhỏ bị khóa GIL.

## Kết quả (demo, seed 20261111, dữ liệu tới 11/11 01:00, iROAS thật)

**CD002 nồi chiên, 11.11, mục tiêu `profit`, horizon 23 giờ** (E[J], đã gồm V·tồn cuối ≈ 2,3–2,5 triệu đ, chủ yếu là giấy lót còn dư):

| Hành động | feasible | E[J] | Đóng góp | Chi ads | Bán (sp) |
|---|---|---|---|---|---|
| continue | ✓ | −1,57 tr | 1,40 tr | 5,30 tr | 106 |
| pause | ✗ `campaign_must_run` | 3,78 tr | 1,23 tr | 0 | 95 |
| reduce_budget 30% | ✓ | 0,10 tr | 1,39 tr | 3,62 tr | 105 |
| reduce_budget 50% | ✓ | 1,21 tr | 1,36 tr | 2,50 tr | 104 |
| **schedule_hours [(0,2),(12,14)]** | ✓ | **2,51 tr** | 1,27 tr | 1,18 tr (21% ngân sách) | 99 |
| pause_until_restock | ✗ `campaign_must_run` | 3,78 tr | 1,23 tr | 0 | 95 |

- Đề xuất: `recommend` `schedule_hours`. Runner-up là `reduce_budget 50%`: p = 1,00, lợi hơn 1,30 triệu đ.
- So với `continue`: lợi hơn ≈ 4,1 triệu đ. Ước tính tay 05 §10 là "khoảng 4 triệu", nên khớp.
- Nồi hết hàng khoảng 13:00 (nhu cầu p50 ~156 nồi so với tồn 76).
- Không có điều kiện đổi kết luận trong khoảng hợp lý; 05 §10.5 cho phép kết quả này. Hàng về sớm (11/11 10:00, +400 nồi) vẫn không cứu được `continue`, vì ở giá 11.11 ads cần iROAS ≥ 57 mới có lời.
- Khác với tay: runner-up là `reduce_budget 50%` chứ không phải `continue`. J tuyệt đối khác vì có số hạng V·tồn cuối.
- Nhãn `scenario`: forecast chưa backtest trong DB e2e (không chạy `run_backtests`), nên dự báo ở mức `scenario`.

**Cốc meme CD004, 05/11 09:00, `profit`:** best = `pause` (`undetermined`, p = 0,94 so với `reduce_budget 50%`, lợi ~33 nghìn đ trên 15 giờ, dưới ngưỡng 200 nghìn đ). Đạt nghiệm thu (`pause` hoặc `reduce_budget`).

**Đèn LED Tết CD003, 05/11 09:00, `clear_stock` hạn 31/01 (horizon bị chặn ở 72 giờ):** best = `continue` (không phải pause), `undetermined` (p = 0,55 so với `reduce_budget 30%`). Có điều kiện đổi theo V. Đạt nghiệm thu.

**Hiệu năng (e2e, máy dev đang chạy song song việc khác):**
- 1 chiến dịch, 6 hành động, 1.000 mẫu, 23 giờ: ~50 ms.
- `run_simulation` đầy đủ (đọc DB + mô phỏng + đề xuất + độ nhạy, dự báo có sẵn): ~0,2 s.
- 49 chiến dịch: 1,2–1,3 s.
- Lần chạy đầu của một mốc mới mất thêm ~10–25 s để dự báo (dựng FitContext). Orchestrator nên dự báo và lưu trước.

## Đề xuất ngoài ranh giới (chưa làm)
1. **API-05:**
   - `POST /scenarios/run` → `run_simulation(conn, req, requested_by="user")`. `ParamOutOfRange` → 400, `NotFound` → 404. `meta.cache_hit = out.cache_hit`, `meta.confidence = rec.confidence`.
   - `POST /scenarios/breakeven` → `uniform_breakeven(UniformCase(...))`.
   - `GET /recommendations` đọc bảng `recommendations WHERE superseded_by IS NULL`.
   - What-if "tắt riêng SKU" → `constraints.check_sku_toggle` (409 `CONSTRAINT_VIOLATION`).
2. **ECO-02 API:**
   - `GET /parameters` → `CATALOGUE` + `latest_effective`.
   - `PUT /parameters/{key}` → `set_parameter(..., set_by=<vai trò>)`, rồi phát `ParameterChanged`.
   - `POST /parameters/{key}/confirm` → `confirm_suggestion`.
   - Kiểm `leftover_value_per_unit` ≤ giá bán bằng `upper=`.
3. **Orchestrator:** dự báo trước cho các SKU của chiến dịch ở mốc `start` (simulator đọc bảng `forecasts` với `origin = start`), rồi `simulate_many` / `run_simulation` cho các chiến dịch bị chạm. Publish `events`.
4. **Plan 05 §3:** chốt V mặc định (Q6).
5. **AI-06** ("hàng về sớm 1 ngày lật kết luận CD002"): với số thật thì **không lật**. Hàng về 11/11 10:00 chỉ thêm đơn lời 9.280đ, mà mỗi 1 triệu đ chi ads chỉ mang về ~0,09 triệu đ lãi. Ca demo AI nên chọn ví dụ khác (ví dụ V hoặc mục tiêu `sell_target`).

## Ghi chú của agent chính khi ghép (30/10)

- **Test hiệu năng tách khỏi lượt chạy mặc định.** `test_performance` (05 §5.5: 50 chiến dịch < 2 giây) đo được 1,2–1,5 giây khi máy rảnh, nhưng 2,7–4,3 giây khi máy dev đang chạy các dự án khác. Giữ nguyên ngưỡng, gắn marker `perf` và loại khỏi `addopts` (`-m 'not perf'`) để CI không đỏ ngẫu nhiên theo tải máy. Đo bằng `uv run pytest -m perf` khi máy rảnh.
- **iROAS CD002 lệch phía an toàn.** Agent ads ước lượng 4,49 [3,39–5,59] so với thật 5,27 (hòa vốn ngày thường 4,96), nên ngày thường app có thể nói "ads lỗ nhẹ" ngược đáp án. Kết luận 11.11 (schedule_hours) không phụ thuộc điểm này vì biên lãi 11.11 chỉ 9.280đ. Ví dụ "ngày thường thì chạy tiếp thắng" phải kiểm lại bằng mô phỏng trước khi đưa vào kịch bản demo.

## Sau sửa lỗi M3-whatif (30/10)

Sửa 6 điểm của [M3-whatif.md](M3-whatif.md) §3 và §5. Mỗi điểm có test hồi quy.

### Q14. Các thay đổi

| # | Lỗi | Sửa | Test |
|---|---|---|---|
| 1 | V mặc định `0,5 × giá vốn` sai kinh tế (nồi chiên 227.500đ > lãi ngày thường 149.200đ) | `leftover_value_per_unit` mặc định **0**. Định nghĩa: giá trị **ròng** của 1 sp dư so với giá vốn (thu hồi sau hạn − giá vốn − chi phí bán tiếp), cùng gốc với `m`. Khoảng: **−giá vốn ≤ V ≤ giá bán**. Âm được, khi hàng dư thu hồi dưới giá vốn. `validate_value`/`set_parameter` thêm `lower=`. API `PUT /parameters` chặn dưới theo giá vốn SKU. `/scenarios/run` chặn override V ngoài [−giá vốn lớn nhất, giá bán]. `GET /parameters`: `min` thành `float \| null`, thêm `min_rule` (tùy chọn). `BreakevenRequest.leftover_value_per_unit_vnd` nhận số âm. Điều kiện đổi theo V tìm trong [−giá vốn, giá bán] | `test_leftover_value_is_net_of_cost_default_zero`, `test_parameters_list`, `test_put_parameter_roles_and_ranges`, `test_scenarios` (V = −9 tr bị chặn) |
| 2 | Runner-up trùng hệt best (pause ≡ pause_until_restock) → p = 0 giả | `select_actions` bỏ hành động có J, số bán, chi **trùng từng mẫu** với một hành động xếp trên. `Selection.duplicate_of` ghi lại | `test_identical_actions_never_runner_up`, `test_identical_twin_is_not_runner_up` |
| 3 | `sell_target`: runner-up là hành động trượt mục tiêu, vẫn tính p như cùng hạng | Có ≥ 2 hành động đạt: runner-up lấy trong số đạt. Chỉ 1 hành động đạt: `status = recommend`, `runner_up` = hành động tốt nhất **không** đạt (để tham chiếu), `p_best_beats_runner_up = null`, `expected_gain_vnd = null`. Thêm `runner_up_meets_goal = false` và `goal_cost_vnd = E[J(runner_up)] − E[J(best)]` (tùy chọn). Lý do ghi: "Chỉ X đạt mục tiêu bán đủ N sp (71%); phương án tốt nhất không đạt là Y (43%): đạt mục tiêu tốn thêm khoảng …". Không tính điều kiện đổi (kết luận do mục tiêu quyết). Áp dụng cả `clear_stock` | `test_sell_target_runner_up_from_goal_meeting_actions`, `test_sell_target_only_one_action_meets_goal` |
| 4 | Ngưỡng 5% × \|E[J(best)]\| phình vì V × tồn | Mẫu số = E[J(best) − V·tồn cuối(best)], tức đóng góp − chi − phạt. Lý do: phần V × tồn định giá hàng **không** bán trong horizon. Nó tỷ lệ với lượng tồn, không với cái đang được quyết. Chọn cách này thay cho chênh tuyệt đối để giữ nguyên ý nghĩa "5% của kết quả kinh doanh". Với V = 0 mặc định thì hai cách như nhau | `test_min_gain_ignores_leftover_term` |
| 5 | ZoneBar nói "pause tốt hơn" khi pause bị cấm | Thanh vẫn trả về. `verdict` giữ nghĩa cũ (so tiền continue vs pause). Thêm 3 trường tùy chọn: `pause_feasible` (mặc định `true`), `pause_violations`, `verdict_note`. Khi pause bị cấm: `pause_feasible = false`, `verdict_note` = "Tạm dừng bị cấm vì đã cam kết với sàn trong khung flash sale. Về tiền, tạm dừng lợi hơn chạy tiếp: thanh cho thấy cái giá của ràng buộc, không phải đề xuất tạm dừng". Câu này cũng được thêm vào `reasons` | `test_zone_bar_never_says_pause_when_pause_blocked` |
| 6 | Khóa cache phụ thuộc offset của `start` (API tự đổi sang UTC) | `service._key` chuẩn hóa `start` và `data_as_of` về UTC. Override lấy bản đã chuẩn hóa (`0` ≡ `0.0`). API vẫn đổi UTC, giờ thừa nhưng vô hại | `test_service_key.py` |

**Cho agent web:**
- Đọc `zone_bar.pause_feasible`. Nếu `false`, hiện `zone_bar.verdict_note` **thay cho** câu verdict, không hiện "Pause tốt hơn". `compareLogic.pauseBlockedLine` hiện dựa vào `verdict === 'pause_better'` + `results[pause].feasible`. Vẫn chạy được, nhưng nên dùng `pause_feasible` / `verdict_note`.
- `recommendation.runner_up_meets_goal === false`: không hiện "X lợi hơn Y …" hay xác suất. Hiện "Chỉ X đạt mục tiêu; Y không đạt: đạt mục tiêu tốn thêm `goal_cost_vnd`". `expected_gain_vnd` lúc này là `null`, nên headline cũ tự rơi về `recommendNoRunner`.
- Đọc lại từ DB: `runner_up_meets_goal` được suy ra (recommend + có runner-up + `p_best` null). `goal_cost_vnd` không được lưu (là `null` khi đọc lại), nhưng câu lý do vẫn có số.
- Tham số V: nhãn mới "Giá trị ròng 1 sp còn lại sau hạn, so với giá vốn (V)", mặc định 0, có thể âm. Gợi ý UI: "0đ = bán sau vẫn hòa vốn".

### Kết quả mới (V = 0 mặc định)

Chạy `run_simulation` trực tiếp trên DB riêng, dựng như `eval/demo_whatif.build_db`. Seed 20261111, 1.000 mẫu, `persist=False`, `use_cache=False`. "vs continue" = E[J(best)] − E[J(continue)]. "Cũ" là số trong M3-whatif.md (V mặc định cũ, trừ chỗ ghi khác).

| # | Biến thể | Best (status, p) | Runner-up, chênh | vs continue | ZoneBar (x*, p10–p90, verdict) | Điều kiện đổi | Cũ |
|---|---|---|---|---|---|---|---|
| 1 | CD002 11.11 01:00 `profit` | **schedule_hours** (recommend, 1,00) | reduce 50%, +1,23 tr | +3,99 tr | **Có:** x* = 0; 4,04–5,00; `pause_better`, `pause_feasible = false` + `verdict_note` | V ≤ −252k/sp → reduce 50% | schedule (recommend 1,00), +4,08 tr. Không có thanh |
| 2a | Hàng về sớm 1 ngày (−1) | schedule_hours (recommend, 1,00) | reduce 50%, +0,91 tr | +3,52 tr | x* = 0; 4,87–9,04; pause bị cấm | V ≤ −25k/sp | schedule, +12,69 tr (phồng vì V) |
| 3b | V = 20.000đ | schedule_hours (recommend, 1,00) | reduce 50%, +1,33 tr | +4,13 tr | Không ("giữ hàng lợi hơn bán": V 20k > m̄ 12,4k) | — | như cũ |
| 4f | `sell_target` N = 95 | **reduce 50%** (recommend, 1,00) | reduce 30% (đạt, 87%), +1,10 tr | +2,76 tr | thanh, pause bị cấm | không | reduce 50%. schedule 68% < 80% |
| 4g | `sell_target` N = 97 | **reduce 30%** (recommend, 1,00) | continue (đạt, 83%), +1,66 tr | +1,66 tr | thanh, pause bị cấm | không | reduce 30% |
| 4a | N = 100 | `none_meets_goal` (cao nhất continue 71%) | — | — | — | — | như cũ |
| 4h | N = 100, xác suất 70% | **continue** (**recommend**, p = null) | schedule (không đạt, 43%), `goal_cost` = **3,99 tr** | 0 | thanh, pause bị cấm | — | continue `undetermined`, p = 0,00 giả |
| 5 | Bỏ ràng buộc flash | **pause** (**recommend**, 1,00) | reduce 50%, +2,37 tr | +5,13 tr | x* = 0; `pause_better`, pause được phép | V ≤ −313k | pause `undetermined` (runner-up là bản sao) |
| 5c | Hạn tới hết 12/11 | schedule_hours (**recommend**, 0,99) | reduce 50%, +1,17 tr | +4,41 tr | thanh, pause bị cấm | V ≤ −104k | `undetermined` (ngưỡng 4,6 tr do V) |
| 6b | CD002 ngày thường 06/11 | reduce 50% (undetermined, 0,75) | schedule, +97k | +384k | x* = 1,44; 1,08–1,96; `undetermined` | V ≤ −40k → schedule | pause (undetermined), "giữ hàng lợi hơn" |
| 7b | CD004 cốc meme 06/11 | pause (undetermined, 0,81) | reduce 50%, +28k | +78k | x* = 0,23; 0,12–0,54; `undetermined` | nhu cầu +89% | pause (0,92), +96k |
| 8a | CD020 đèn lồng 06/11 | pause (undetermined, 0,91) | reduce 50%, +46k | +107k | x* = 0,65; 0,67–1,21; **`pause_better`** | V ≤ −24k | như cũ ở V = 0 |
| 9 | CD023 hộp quà 11.11 | reduce 50% (undetermined, 0,67) | reduce 30%, +62k | +362k | x* = 6,55; 3,74–5,87; **`continue_better`** | V ≤ −13k; nhu cầu +18% | như cũ ở V = 0 |

**Xác nhận:**
- **CD002 11.11:** vẫn `schedule_hours`, `recommend`, p = 1,00. Pause và pause_until_restock vẫn `campaign_must_run`, không khả thi.
- **CD002 11.11 giờ có ZoneBar:** x* = 0, khoảng 4,04–5,00 sp/giờ, `verdict = pause_better` (so tiền), `pause_feasible = false`, kèm `verdict_note` "Tạm dừng bị cấm…".
- **Giá của cam kết flash:** E[J] pause 1,23 tr − schedule 0,09 tr = **1,14 tr** (cũ 1,27 tr).
- **"Chỉ tiêu 97 sp" vẫn đổi kết luận:** schedule (57%) → **reduce 30%** (81%). Tốn E[J(schedule)] − E[J(reduce 30%)] = 0,09 − (−2,24) = **2,33 tr** (cũ 2,40 tr). Runner-up giờ là continue (cũng đạt), nên p = 1,00 có nghĩa thật.
- N = 95 → reduce 50%, tốn 0,09 − (−1,14) = **1,23 tr**.
- Số E[J] tuyệt đối giờ "sạch": không còn phần V × tồn. CD002 11.11 continue = −3,90 tr, schedule = +0,09 tr.

**Quét 49 chiến dịch, V = 0 mặc định** (18 bị chặn vì thiếu giá vốn; còn 31):

| Bối cảnh | pause | reduce | schedule | continue | `recommend` | Thanh |
|---|---|---|---|---|---|---|
| 06/11 cả ngày | **16** (cũ 27) | 9 | 1 | 5 | CD008, CD033 continue (xem dưới) | 27 `undetermined`, 1 `pause_better` (CD020) |
| 11.11 01:00 | 9 (cũ 25) | 17 | 2 | 3 | CD002 schedule, CD015 pause, CD007 / CD033 continue | 24 `undetermined`, 2 `pause_better`, 1 `pause_better` + pause bị cấm (CD002), 1 `continue_better` (CD023) |

- Ngày thường không còn "27/31 pause chỉ vì giữ hàng". Còn 16/31 pause, gần hết `undetermined` (chiến dịch nhỏ, chênh < 200k).
- CD007, CD008, CD033 không chi ads trong horizon, nên mọi hành động trùng hệt nhau. Sau khi bỏ bản sao (lỗi 2), lượt quét ra "recommend continue" không có runner-up. Đã sửa ngay sau lượt quét: không có runner-up vì mọi phương án trùng nhau → `undetermined`, lý do "Các phương án cho kết quả như nhau…" (`test_all_actions_identical_is_undetermined`). Quét không chạy lại sau sửa này.

### What-if cho sân khấu (số mới)

- **Nên dùng:** "Chỉ tiêu 97 sp": đổi từ "Chỉ chạy khung giờ" sang "Giảm ngân sách 30%". Đạt 81% so với 57%. Tốn **2,33 tr**. Runner-up là "Chạy tiếp", cũng đạt, nên p = 1,00 có nghĩa thật.
- **Nên dùng:** "Cam kết flash tốn bao nhiêu": pause 1,23 tr vs schedule 0,09 tr → **~1,1 tr**. Thanh vùng 11.11 giờ hiện, kèm câu "Tạm dừng bị cấm…".
- **Nên dùng:** "Hàng về sớm 1 ngày": kết luận không đổi, schedule vẫn hơn chạy tiếp 3,5 tr.
- **Nên dùng:** "Chỉ tiêu 100 sp, chấp nhận 70%": giờ ra `recommend` continue, "đạt mục tiêu tốn thêm ~4,0 tr".
- **Nên dùng:** CD020 cho thanh "nên dừng".
- **Không nên:** đọc thanh CD002 11.11 như "nên tạm dừng". Không nói "ngày thường app khuyên chạy tiếp nồi chiên": app ra giảm 50%, `undetermined`. Không dùng CD004 cho thanh (`undetermined`).
