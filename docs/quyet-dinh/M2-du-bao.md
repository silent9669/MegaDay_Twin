# Nhật ký quyết định · Dự báo nhu cầu + chấm điểm (M2)

Task: FC-01..FC-09 (FC-10 DDL đã có ở M0), EVF-01 (phần dự báo của EVAL-02). Code: `backend/app/forecast/`, `backend/eval/eval_forecast.py`. Test: `backend/tests/unit/forecast/`.

## API công khai (cho simulator / AI / API / orchestrator)

| Hàm | Ở đâu | Việc |
|---|---|---|
| `ForecastRequest`, `ForecastResult`, `ValidatedScope`, `ForecastModel`, `FittedState`, `nb_quantiles`, `nb_samples` | `forecast/base.py` (mypy strict) | Hợp đồng 03 §2. Simulator lấy mẫu lại từ `mean`, `dispersion_k`, `level_sigma` bằng đúng `nb_samples` |
| `service.run_backtests(conn, specs=None)` | `forecast/service.py` | Backtest rolling-origin → `model_registry` + `model_scores` (+ `is_champion`) |
| `service.fit_all(conn, origin=None, drift_active=…)` | | FitContext tại mốc, ghi `skus.demand_group` (trừ nhóm người dùng đặt), fit champion từng SKU |
| `service.forecast_skus(conn, sku_ids=None, origin=None, horizon_hours=72, drift_active=…, persist=True)` / `forecast_sku(...)` | | `ForecastResult` có nhãn + lý do, ghi bảng `forecasts` (kèm `input_hash`); tự áp giá cam kết (`sale_commitments.committed_price`) |
| `service.predict_many(jobs)` | | Predict song song (thread, numpy nhả GIL) |
| `service.scan_drift(matrix, results, until)` → `DriftAlarm` (`.event()` = `DriftDetected`), `drift.active_up(alarms, at)` | `forecast/drift.py` | CUSUM 03 §9 (+ detector sản phẩm cho `up`, `pooled=True` mặc định, `DriftAlarm.scope`, xem H4); `up` → truyền SKU vào `drift_active` để dùng `nowcast` 24 h |
| `service.set_demand_group(conn, sku, group | None)` | | Người dùng sửa nhóm (`source='user'`), `None` trả về tự động |
| `service.load_matrix(conn)`, `context.build_fit_context(matrix, origin)` | | Panel → mảng dày `[sku, giờ]`; FitContext chỉ chứa dữ liệu < origin |

`ForecastResult` thêm (đánh dấu `[DEV]`): `demand_group`, `context`, `history_days`, `phase`.

## Quyết định

### C1. Dữ liệu dày theo mảng thay vì DataFrame theo SKU
Backtest = ~6 model × 300 SKU × 20 mốc. Panel được pivot một lần thành `[S, T]` (lưới giờ bắt đầu nửa đêm VN, M1-panel P1); mọi đại lượng shop/SKU của FitContext tính vector hóa. `ForecastModel.fit(panel, sku, origin, ctx)` giữ chữ ký plan, dữ liệu thật đọc từ `ctx` (đã cắt < origin, nên model không thể nhìn tương lai). `test_no_future_leak` thay dữ liệu sau origin bằng NaN / ×10 / đảo cờ hết hàng: dự báo mọi model không đổi.

### C2. Phân nhóm tính đặc trưng trên **sản phẩm** (cộng các biến thể), tuổi tính theo SKU
Tính theo từng biến thể: `demo` chỉ đúng 56% vì biến thể 0,3 sp/ngày của hàng ổn định có ADI > 1,32 (thành `ban_thua`) và `peak_ratio` bị nhiễu. Biến thể chung một kiểu nhu cầu, nên gộp theo `product_id`; luật 1 (`age < 14 → moi`) vẫn theo SKU (biến thể mới vẫn là `moi`). `peak_ratio` dùng **trung bình trượt 7 ngày cao nhất** / trung vị 56 ngày (đọc "trung bình 7 ngày cao nhất" theo nghĩa cửa sổ 7 ngày; bản "7 ngày lẻ cao nhất" chỉ đo nhiễu Poisson). Đặc trưng tính trên `x` = nhu cầu đã hiệu chỉnh censoring rồi chia hệ số thứ/ngày đôi/ngày lương/giá, để ngày đôi không trông như trend.

### C3. Tên sản phẩm làm tiên nghiệm mùa vụ
Tết cách dữ liệu ~3 tháng nên lịch sử hàng Tết gần phẳng; luật 3 (tương quan ≥ 0,6 và peak ≥ 2) không thể ra `mua_vu` cho `DENLEDTET-*`. Thêm `NAME_HINTS`: tên có "Tết/lì xì/câu đối…" → `mua_vu/tet`; tên mùa đông/quà tặng chỉ hạ ngưỡng tương quan của đúng loại đó xuống 0,3 (dữ liệu vẫn phải đồng ý). Đường mẫu để tính tương quan: quà tặng = bướu quanh 20/10, 20/11; Tết = `exp(−days_to_tet/25)`; mùa đông = logistic quanh 25/10. Người dùng vẫn sửa được nhóm.

### C4. Hệ số ngày đôi theo SKU co về hệ số nhóm
`E(h)` gộp theo nhóm (plan) cộng một hệ số SKU `(Σ bán thật + 20) / (Σ kỳ vọng với E nhóm + 20)` trên các ngày đôi đã qua, kẹp [0,5; 2]. Hàng mà ads/giảm giá phản ứng mạnh hơn nhóm (NOICHIEN2L) giữ phản ứng riêng; SKU nhỏ gần như dùng hệ số nhóm. Chưa có ngày đôi nào (dd_0808): `E = 2,5` phẳng, giả định giảm giá ngày đôi 15%, `level_sigma +0,30` và lý do ghi rõ. Hệ số ngày đôi đã chứa mức giảm giá điển hình; `price_plan` chỉ dịch theo `((1−giảm_kế_hoạch)/(1−giảm_điển_hình))^β`.

### C5. Pha trend: hồi quy 14 ngày trên sản phẩm
Plan ghi "độ dốc 7 ngày". Trên một biến thể 5 sp/ngày, độ dốc 7 điểm có sai số ≈ ±7%/ngày so với ngưỡng −3%/ngày: pha nhảy ngày này qua ngày khác trên `demo`. Dùng OLS của `log(1+x)` 14 ngày, cộng mọi biến thể: sai số ≈ ±2%. Kết quả: COCMEME-* `nguội` liên tục từ 23–24/10. Sau 05/11 độ dốc thật của bộ sinh tiến về −3,5%/ngày (trend tắt dần về mức nền), nên trên `demo` một số ngày 06–11/11 ra `đỉnh` (ghi trong báo cáo).

### C6. Nhãn khi không có điểm
03 §7.3 có code (`scope is None → scenario`) và câu văn ("không có điểm đúng bối cảnh → reference + 'chưa kiểm ở bối cảnh này'"). Giữ cả hai: nhóm chưa từng được backtest → `scenario`; nhóm có điểm nhưng không ở bối cảnh này → `reference` kèm lý do. `nowcast` tối đa `reference`. Scope = dòng của champion ở đợt gần nhất cùng bối cảnh có ≥ 30 SKU-ngày, không có thì dòng gộp `pooled_<context>` (cũng ghi vào `model_scores`).

### C7. CUSUM bỏ giờ có λ < 0,25
Với λ ≈ 0,05 (đêm), một đơn cho z ≈ 4–6, xấp xỉ chuẩn của phần dư không còn đúng; trên `nho` BINHNUOC500-XANH báo `up` lúc 01:00 vì 1 đơn 3 cái. Giờ quá vắng bị bỏ khỏi CUSUM giống giờ hết hàng. Công thức, ngưỡng 5, slack 0,5 giữ như plan.

### C8. `level_sigma` "cộng sàn 0,1" = `max(se, 0,1)`
Hiểu "sàn" là giá trị nhỏ nhất. Ngày đôi `+0,15` như plan.

### C9. Chấm điểm WAPE theo trung vị tổng ngày
WAPE ở hạt SKU × ngày dùng **p50 của tổng ngày** (lấy từ mẫu), không cộng p50 từng giờ (với SKU < 1 sp/giờ, p50 giờ = 0 làm tổng ngày = 0). Coverage cũng trên tổng ngày. Trong app: giờ hết hàng bị bỏ khỏi cả thực tế lẫn dự báo; SKU-ngày không có giờ còn hàng nào bị bỏ. Backtest dùng 500 mẫu/SKU, dự báo thật 2000.

### C10. Champion/challenger
Luật 1 (WAPE thấp nhất, chênh < 2 điểm thì pinball_50) chạy ở đợt đầu của mỗi (nhóm, bối cảnh); đổi champion chỉ khi challenger tốt hơn ≥ 5% ở 2 đợt liên tiếp; champion của lần chạy trước (bảng `model_scores` mới nhất) là đương nhiệm. `nowcast` không thi (chỉ chạy khi drift). `normal_roll` (14 mốc) là một đợt. `tsb` áp cho cả `on_dinh` và `trend_damped` cho `on_dinh/mua_vu` để backtest quyết định (03 "không do người viết code chọn") — trên mock `tsb` thắng `on_dinh` sát nút.

### C11. Khoảng `tsb`
Dùng chung `nb_quantiles` (k của nhóm) thay cho hỗn hợp Bernoulli × Poisson riêng, để mọi model lấy mẫu một cách (03 §2).

### C12. Nguồn dữ liệu test / eval
Fixture tạm `mock_loader.py` đã bị agent M1 xóa; test dùng `tests.mockdata.load_mock` (pipeline thật) và `loaded_db` dùng chung cho test chỉ đọc. Test cần ghi và cần ngày 11.11 dựng DB riêng (`nho_live`, `demo_live` = lịch sử + `ingest_live_delta` cả ngày 11.11). `eval_forecast.py` nhận `--db` (DuckDB do pipeline dựng) hoặc tự nạp mock vào bộ nhớ qua `tests.mockdata`.

## D. Đo lại cho đúng: coverage PIT + WAPE so với mức sai không tránh được (user chọn "hướng A")

**Vấn đề.** Hai ngưỡng của nhãn tin cậy cho sai kết luận trên mock: (1) coverage `y ∈ [p10, p90]` của số đếm nhỏ tính trọn khối xác suất ở hai mép, nên dự báo bằng **phân phối thật** cũng phủ 86–91% (> 0,88); (2) "WAPE ≤ 30%" thấp hơn nhiễu không tránh được (dự báo bằng λ thật: 35% ngày thường trên `demo`). Kết quả cũ: hầu như không SKU nào `verified` dù model gần sàn nhiễu.

**Cách đo mới** (`metrics.py`, `labels.py`, 03 §7.2–7.3):
- Coverage = tỷ lệ (SKU, ngày) có PIT ngẫu nhiên hóa `u = F(y−1) + V·(F(y) − F(y−1))`, `V ~ U(0,1)` (seed theo SKU/mốc/model) nằm trong `[0,1; 0,9]`; `F` = phân phối mẫu của tổng ngày (giờ còn hàng trong app). Phân phối đúng → đúng 80%.
- `WAPE_floor = Σ E|Y − p50_Y| / Σ E[Y]`, `Y` = tổng ngày của NegBin(mean dự báo, k) theo giờ, Monte Carlo 500 mẫu có seed, cùng tập SKU-ngày (≥ 1 sp/ngày) với WAPE. Lưu ở `model_scores.wape_floor`.
- `verified` khi `WAPE ≤ (1 + WAPE_FLOOR_TOL)·WAPE_floor` (`WAPE_FLOOR_TOL = 0,10`, xem đoạn "Chốt" bên dưới), coverage PIT ∈ [0,72; 0,88], `n_sku_days ≥ 30`; drift → tối đa `reference`; < 14 ngày → `scenario` (giữ nguyên).
- **Floor không gồm `level_sigma`** (lệch với gợi ý "mean + k + level_sigma"). Lý do bằng phép thử: tính có `level_sigma` thì floor của app 39% (ngày thường) và 42% (10.10) so với WAPE oracle 29% và 20%; floor đo độ mơ hồ của chính model, và dự báo ×1,5 *qua được* 10.10. Bỏ `level_sigma` thì floor = nhiễu đếm quanh mean của model: khớp oracle và dự báo kém trượt. Mở rộng khoảng cũng không giúp: floor không đổi mà coverage PIT vượt 0,88.

**Phép thử oracle** (`eval_forecast.new_measure`, SKU ≥ 1 sp/ngày; unit test tổng hợp có seed ở `test_pure.py`). `demo`:

| Phạm vi | Dự báo | WAPE | WAPE_floor | WAPE/floor | PIT | cũ [p10,p90] | Đạt |
|---|---|---|---|---|---|---|---|
| normal_roll on_dinh | oracle (λ thật, k=3) | 34,9% | 34,1% | 1,02 | 79,8% | 89,0% | ✓ |
| | champion | 37,2% | 34,7% | 1,07 | 84,5% | 92,1% | ✓ |
| | champion ×1,5 | 53,2% | 29,2% | 1,82 | 78,0% | 87,3% | ✗ |
| | baseline 7 ngày cùng giờ | 39,4% | 34,8% | 1,13 | 74,8% | 84,5% | **✓** |
| dd_1010 on_dinh+mua_vu | oracle | 25,7% | 24,5% | 1,05 | 79,1% | 86,1% | ✓ |
| | champion | 34,9% | 24,4% | 1,43 | 85,9% | 89,7% | ✗ |
| | champion ×1,5 / baseline | 56,7% / 67,1% | | 2,73 / 1,88 | | | ✗ / ✗ |

(a) oracle đạt ở cả hai phạm vi; (b) floor app tự tính 34,7% vs WAPE oracle 34,9% (ngày thường), 24,4% vs 25,7% (10.10); (c) ×1,5 trượt ở cả hai, baseline ngây thơ trượt ở 10.10 nhưng **qua được ở ngày thường on_dinh** (WAPE chỉ hơn sàn nhiễu 13% < 15%): trên ngày thường của mock, trung bình 7 ngày cùng giờ thật sự gần sàn nhiễu, và champion chỉ tốt hơn nó ~6%. Không chỉnh `tol` cho baseline trượt; nếu muốn khắt khe hơn thì `tol = 0,10` (champion 1,07 vẫn qua, baseline 1,13 trượt) — để chủ plan quyết.

**Nhãn trên `demo` sau khi chạy lại** (backtest + dự báo 11/11 00:00, 24 h): verified 44, reference 242, scenario 14 (trước: 0 / 286 / 14). Nhân vật demo: NOICHIEN2L, BINHNUOC500-* (tsb, on_dinh, bối cảnh double_day) `reference`; AOGIUNHIET-*, DENLEDTET-* (mua_vu), COCMEME-* (trend) `reference`; MAYHUTMINI `scenario`. Mọi nhân vật đều dự báo cho 11.11 nên dùng điểm `double_day`, mà champion ngày đôi trượt (WAPE/floor 1,43 so với nhu cầu thật; trong app cũng trượt) — đúng là dự báo ngày đôi chưa đủ tốt để gắn "đã kiểm".

**Chốt `WAPE_FLOOR_TOL = 0,10` (agent chính, 30/10, user giao quyết).** Nhãn "Đã kiểm" phải phân biệt được model với cách đoán ngây thơ; với 0,15 baseline 7 ngày cùng giờ (1,13) vẫn qua ở ngày thường. Với 0,10: oracle (1,02–1,05) và champion ngày thường (1,07) vẫn qua, baseline (1,13) và ×1,5 trượt. Đây là siết chặt, không nới. Đo lại trên demo với 0,10 (ingest → backtest → fit 300 SKU): verified 44, reference 242, scenario 14, giống hệt khi dùng 0,15, vì các nhóm đạt đều có champion WAPE/floor ≤ 1,07; nhân vật demo không đổi (NOICHIEN2L, BINHNUOC500-*, AOGIUNHIET-*, COCMEME-*, DENLEDTET-* reference; MAYHUTMINI scenario).

**Ảnh hưởng.** Schema: thêm cột nullable `model_scores.wape_floor` (schema.sql `[DEV]` + migration 2 trong `warehouse/db.py`; `tests/unit/test_migrate.py` theo `MIGRATIONS`). Backtest lấy thêm một bộ mẫu không có `level_sigma` cho mỗi (SKU, model) (~+30% thời gian). Các số đo cũ vẫn in trong báo cáo eval, ghi "(cách đo cũ)", không chấm đạt/trượt.

## Kết quả (`uv run python -m eval.eval_forecast --size demo`, ~3 phút)

| Kiểm tra | demo | Ngưỡng | |
|---|---|---|---|
| **M2** WAPE 10.10 champion / baseline 7 ngày cùng giờ | 37,0% / 68,5% (tốt hơn 46%) | ≥ 15% | ✓ |
| **M2** … / baseline cùng giờ tuần trước | 37,0% / 71,0% (tốt hơn 48%) | ≥ 15% | ✓ |
| **M2** Coverage 80% theo ngày, normal_roll | 88,1% (10.10: 88,4%) | 0,72–0,88 | ✗ (sát) |
| **M2** Mọi SKU có nhãn | 300/300 (reference 286, scenario 14) | 100% | ✓ |
| WAPE ngày thường on_dinh | 37,2% (oracle λ thật 35,4%) | ≤ 30% | ✗ |
| WAPE 10.10 on_dinh + mua_vu | 34,9% | ≤ 40% | ✓ |
| Censoring 10.10, 24 SKU ≥ 5 giờ hết | bias −37% (hiệu chỉnh) vs −69% (không) | \|bias\| ≤ 10% | ✗ |
| Phân nhóm SKU ≥ 28 ngày / nhân vật demo | 88,3% / 12/12 | ≥ 80% / 100% | ✓ |
| COCMEME-* nguội | từ 24/10 | ≈ 25/10 | ✓ |
| MAYHUTMINI | scenario (analog) | scenario | ✓ |
| NOICHIEN2L 11.11 | p50 158 vs tồn 83 | > tồn | ✓ |
| BINHNUOC500-* drift up → nowcast | báo 10:00, 10:00, 11:00; viral (λ thật ≥ 2× dự báo) từ 7–8:00 | 2–3 giờ sau viral; plan đoán 12–15h | ✗ (XANH trễ 4 giờ; 0/3 trong 12–15h) |
| AOGIUNHIET-M không báo down sai | 0 | 0 | ✓ |
| Fit + predict 72 h, 300 SKU | 0,8 s + 1,5 s khi máy rảnh; 2,4 s + 3,2 s khi máy đang chạy việc khác | < 30 s, < 2 s | ✓ khi rảnh / ✗ khi tải |

`nho` (30 SKU): M2 WAPE 33,6% / 71,2% (tốt hơn 53%), coverage 77%, phân nhóm 75% (7 SKU trend thật gần như không bán trong 56 ngày → `ban_thua`).

### Vì sao chưa đạt (số thật, không nới ngưỡng)
- **Coverage 88,1%:** chính phân phối thật của bộ sinh (NegBin k = 3 theo giờ) cho khoảng p10–p90 *bao gồm hai đầu* phủ **90,7%** tổng ngày vì số đếm nhỏ là rời rạc (SKU 0–1 sp/ngày: 91%). Model đang hẹp hơn phân phối thật một chút; muốn vào băng phải làm khoảng hẹp hơn sự thật. Đề xuất: chấm coverage bằng PIT ngẫu nhiên hóa hoặc chỉ trên SKU ≥ 3 sp/ngày (cần quyết định của chủ plan).
- **WAPE ngày thường ≤ 30%:** sàn nhiễu (dự báo bằng chính λ của bộ sinh) đã là 35,4%; champion 37,2%. Ngưỡng 30% không đạt được trên mock này.
- **Censoring:** phần lớn sai lệch còn lại do SKU trend hết hàng kéo dài nhiều tuần (ví dụ COCKHUAY-*: 0 giờ còn hàng trong 14 ngày trước mốc, nhu cầu thật đỉnh đúng 10.10) — không có dữ liệu nào để biết nhu cầu. Hiệu chỉnh vẫn giảm |bias| một nửa (−69% → −37%), gift_1020: −78% → +9%.
- **Drift:** bộ sinh cho viral tăng dần từ sáng (×2 lúc 7–8h), nên báo động 10–11h là phát hiện thật; mốc 12–15h trong plan là ước đoán. XANH trễ 4 giờ do dao động sáng.

## Đề xuất ngoài ranh giới (chưa làm)
1. **CLI** (`app/cli.py`): nối `backtest` → `service.run_backtests(conn)`, `fit` → `service.forecast_skus(conn)` (thay stub thoát mã 3); `eval` → `eval.eval_forecast.main`.
2. **Orchestrator (ORC-05):** mỗi giờ live: `scan_drift(matrix, forecasts_của_ngày, now)` → publish `alarm.event()`; SKU `up` đưa vào `drift_active` khi gọi `forecast_skus` trong 24 h.
3. **API-04:** `PUT /skus/{id}/demand-group` gọi `service.set_demand_group`.
4. **pyproject:** `filterwarnings` cho `Pandas4Warning` của DuckDB `.df()`; marker `e2e` đã có, `test_demo_e2e.py` dùng nó (~1 phút).
5. **Plan 03 §10:** xem lại định nghĩa coverage cho số đếm nhỏ và ngưỡng WAPE ngày thường (sàn nhiễu 35%).
6. `run_all.py` (EVAL-02) gọi `eval_forecast.run(size, db)`.

## Hoàn thiện M2 (30/10)

Hai việc còn dở của 03 §10: censoring (|bias| ≤ 10%) và phát hiện đột biến bình nước. Code: `forecast/censoring.py`, `forecast/drift.py`, `service.scan_drift`, `context.py` (truyền mã sản phẩm và cờ nhóm `trend` vào censoring). Test hồi quy: `test_pure.py` (4 test censoring, 3 test drift); `test_service.py` / `test_demo_e2e.py` siết giờ báo động. Định nghĩa phép đo trong `eval_forecast.py` **không đổi**. Mục này thay cho các dòng censoring/drift trong "Vì sao chưa đạt" ở trên.

### H1. Censoring: phân tích nguyên nhân bằng số

Tập chấm: 24 SKU có ≥ 5 giờ hết hàng trong cửa sổ 09–11/10, `profile_ewma`, p50 tổng ngày so với `nhu_cau_that`. Tổng nhu cầu thật 708, dự báo (trước) 444,5: thiếu 263,5.

| Nhóm (theo đáp án) | Nhu cầu thật | Dự báo trước | bias | Ghi chú |
|---|---|---|---|---|
| on_dinh | 119 | 98 | −18% | lịch sử không hết hàng; hết hàng **trong** cửa sổ |
| mua_vu | 86 | 46 | −47% | như trên; `profile_ewma` không có mùa vụ |
| trend | 503 | 300,5 | −40% | |
| trong đó 9 SKU **0 giờ còn hàng trong 14 ngày trước mốc** | 329 | 150,5 | −54% | COCKHUAY-XANH, MOCKHOA3-A, MOCKHOA2-A, GUONGLED*-XANH, LEDCUON2-5M, COCKHUAY3-* |

Hai nguồn sai khác nhau:
1. **Chọn mẫu theo kết quả (~61 sp, 23% phần thiếu).** Điều kiện "≥ 5 giờ hết hàng trong cửa sổ" chọn đúng những SKU mà nhu cầu 10.10 vượt tồn, tức là dự báo vốn đã thấp. Với các SKU on_dinh/mua_vu này lịch sử không bị chặn, censoring không có gì để sửa (bản hiệu chỉnh và không hiệu chỉnh gần bằng nhau: 98 vs 97, 46 vs 45).
2. **Hết hàng nhiều tuần ở SKU trend (178,5 sp, 68%).** COCKHUAY-XANH: bán 3–4 sp/ngày tới 15/09 rồi hết hàng 24 ngày; biến thể HONG còn hàng tới 27/09, sau đó cả sản phẩm hết 12 ngày. Trong thời gian đó nhu cầu thật tăng từ ~4 lên 13–26 sp/ngày (73 đúng ngày 10.10). MOCKHOA3-A: ~2 sp/ngày trước khi hết, 70 sp ngày 10.10. App không có dữ liệu nào quan sát được trong khoảng này (không có lượt xem/giỏ hàng; mọi biến thể đều hết).

**Phép thử "lịch sử hoàn hảo"** (chỉ trong eval, script tạm): thay giờ hết hàng trong lịch sử bằng nhu cầu bị mất thật (chia đều cho các giờ hết hàng của ngày, tắt cờ hết hàng), giữ nguyên model và nhóm. Bias −6,4% (trend +3%; on_dinh/mua_vu vẫn −18%/−47%). Nghĩa là: (a) nếu *biết* nhu cầu lúc hết hàng thì đạt ≤ 10%; (b) phần còn thiếu nằm ở nhu cầu không quan sát được, không phải ở công thức hiệu chỉnh trên giờ còn hàng.

### H2. Censoring: đổi phương pháp (03 §4)

Khi cửa sổ 14 ngày còn < 3 ngày "phần kỳ vọng" còn hàng (`REF_SHARE`), bản cũ tính mức trên vài giờ cuối trước khi hết (nhiễu, thường thấp vì kệ sắp trống) rồi kéo phẳng. Thay bằng `L = L_pre · g_sib · g_trend`:
- `L_pre` = mức trên 7 ngày phần kỳ vọng còn hàng gần nhất, lùi xa bao nhiêu cũng được (mức trước khi hết);
- `g_sib` = tăng trưởng của các **biến thể cùng sản phẩm** còn quan sát được, mờ dần theo tuổi thông tin: mũ `φ^(d − ℓ)`, `φ = 0,85` (như `trend_damped`), `ℓ` = ngày cuối còn quan sát được;
- `g_trend` = đường trend tắt dần (độ dốc OLS của log mức trước khi hết, `φ = 0,85`, kẹp ±10%/ngày) cho SKU nhóm `trend`;
- giờ hết hàng có `stockout_conf < 0,7` chỉ cộng một nửa phần hiệu chỉnh (giờ còn hàng conf thấp vẫn nửa trọng số như cũ); phần hiệu chỉnh `max(y_t, L̂·…)` tính theo **từng giờ** đúng công thức 03 §4 (trước: theo ngày).

Không dùng `ground_truth` trong app; tham số dùng chung cho mọi SKU.

**Đã thử và bỏ (có số):**
- *`g_sib` không mờ dần*: dd_1010 −32,9%, nhưng tháng 11 các SKU trend đã hết sóng mà vẫn hết hàng bị dự báo cao: `ban_thua` normal_roll bias +16% → +73%, WAPE champion toàn shop normal_roll 41,8% → 43,0%, live_1111 39,9% → 41,8%. Cùng một tín hiệu đúng cho 10.10 lại sai cho 11/11: hướng thay đổi của nhu cầu khi không quan sát được là không biết được.
- *ADI và slope28 chỉ tính trên ngày còn hàng* (để SKU trend hết hàng nhiều tuần không thành `ban_thua`): độ chính xác phân nhóm 88,3% → 88,7%, nhưng các SKU này thành `on_dinh`/`mua_vu`, champion on_dinh ngày thường WAPE/floor 1,07 → 1,18 và `verified` 44 → 0. Bỏ.
- *Ép `g_trend` cho mọi SKU trend thật (dùng đáp án, chỉ để đo trần)*: bias −33,9%; COCKHUAY3-* (nhu cầu đã giảm trong lúc hết hàng) bị dự báo gấp ~5 lần. Kéo dài đường trend không cứu được.

### H3. Censoring: kết quả (bias p50 so với `nhu_cau_that`, SKU ≥ 5 giờ hết hàng, `eval_forecast`)

| Đợt | SKU | Trước | Sau | Không hiệu chỉnh |
|---|---|---|---|---|
| dd_0909 | 6 | −38,6% | −41,6% | −55,0% |
| **dd_1010** | 24 | **−37,2%** | **−34,7%** | −68,6% |
| gift_1020 | 32 | −35,8% | −32,2% | −67,2% |
| live_1111 | 22 | −14,3% | −19,3% | −47,2% |
| normal_roll | 35 | −23,8% | −22,6% | −65,5% |

WAPE trên cùng tập (trước → sau): dd_0909 48,5% → 51,5%, dd_1010 64,2% → 63,0%, gift 68,1% → 66,6%, live 44,1% → 41,7%, normal_roll 70,3% → 69,2%. 9 SKU hết hàng trọn 14 ngày: 150,5 → 162 sp (thật 329).

**Không đạt ≤ 10%: đạt −34,7%** (trước −37,2%; không hiệu chỉnh −68,6%). Lý do là giới hạn thông tin, có bằng chứng: lịch sử hoàn hảo cho −6,4%; 68% phần thiếu nằm ở 9 SKU không có một giờ còn hàng nào trong 14 ngày trước mốc; 23% là hiệu ứng chọn mẫu, không liên quan censoring. Đề xuất cho chủ plan (chưa làm, không đổi phép đo): chấm thêm trên SKU có ≥ 1 ngày còn hàng trong 7 ngày trước mốc, hoặc ghi trên slide "hiệu chỉnh lấy lại một nửa phần thiếu; phần còn lại là nhu cầu không quan sát được".

### H4. Drift bình nước: detector đột biến theo sản phẩm (03 §9)

Nguyên nhân trễ: mỗi biến thể chỉ 1–2 sp/giờ buổi sáng; z-CUSUM một biến thể cần 3–4 giờ vượt mới qua ngưỡng 5 (XANH: giờ 9 bán 0 làm S⁺ tụt). Đã thử **thêm z-CUSUM gộp sản phẩm** (hợp với per-SKU): báo sớm, nhưng báo động giả ngày thường 1,59 → 3,71 / 100 SKU-ngày, ngày đôi 4,39 → 12,07 (gộp thì bắt cả lệch 20–50% của dự báo). Bỏ.

Thay bằng: sản phẩm ≥ 2 biến thể, `up` = CUSUM của **log-likelihood ratio NegBin "nhu cầu = 2× dự báo"** cộng trên các biến thể còn hàng (giờ đông trọng số lớn, giờ vắng một đơn ít); `down` giữ z-CUSUM từng SKU (giờ hết hàng bị loại); sản phẩm một biến thể giữ nguyên plan. Ngưỡng 6 = giá trị nhỏ nhất trong {4, 5, …, 10} mà **tổng số báo động** trên ngày quá khứ không vượt bản per-SKU ở mọi loại ngày — chọn không cần đáp án.

Báo động giả (đo bằng đáp án: `up` khi λ thật 6 giờ gần nhất < 1,5× dự báo, `down` khi > 0,67×), dự báo champion của backtest, CUSUM chạy theo ngày:

| Loại ngày | SKU-ngày | Báo động / 100: trước → sau | Báo động giả / 100: trước → sau |
|---|---|---|---|
| Ngày thường (28/10–10/11) | 4093 | 1,95 → 0,95 | 1,59 → 0,68 |
| Ngày đôi 8.8/9.9/10.10 | 820 | 18,54 → 17,80 | 4,39 → 3,41 |
| ±1 ngày đôi | 1640 | 1,65 → 0,85 | 1,59 → 0,67 |

11.11 (`demo`, `eval_forecast`): DEN viral 8:00 → báo 10:00 (2 giờ, như trước); HONG 8:00 → 10:00 (2 giờ, như trước); **XANH 7:00 → 10:00 (3 giờ; trước 11:00, 4 giờ)**; cả ba chuyển `nowcast`. Ngưỡng 4 báo 9:00 cả ba nhưng báo động ngày đôi lên 29,4/100, nên không dùng. `nho`: XANH 15:00 → 10:00. AOGIUNHIET-M: 0 báo `down` trước và sau khi hết hàng (down vẫn per-SKU, giờ hết hàng bị loại; detector sản phẩm không phát `down`).

`nowcast` khi báo sớm có ít giờ viral hơn: trên `nho` mức nowcast giờ đầu chỉ ~1,3× dự báo sáng (XANH), sau 2 giờ ≥ 1,5× (nowcast được fit lại mỗi giờ khi drift bật). `test_binhnuoc_drift_up_then_nowcast` kiểm cả hai mốc.

### H5. Tiêu chí M2 trước/sau (`eval_forecast --size demo`, DB dựng bằng pipeline, cùng máy, cùng lúc)

| Kiểm tra | Trước | Sau |
|---|---|---|
| WAPE 10.10 champion / baseline 7 ngày (tốt hơn) | 37,0% / 68,5% (46,0%) | 37,0% / 68,6% (46,0%) ✓ |
| … / baseline tuần trước | 37,0% / 71,0% (47,9%) | 37,0% / 71,0% (47,9%) ✓ |
| Champion ngày thường on_dinh: WAPE/floor; PIT | 1,07; 84,5% | 1,07; 84,5% ✓ |
| Oracle / floor ≈ oracle / dự báo kém trượt | ✓ / ✓ / ✓ | ✓ / ✓ / ✓ |
| Nhãn 300/300 | verified 44, reference 242, scenario 14 | giống hệt ✓ |
| WAPE 10.10 on_dinh + mua_vu | 34,9% | 34,7% ✓ |
| WAPE champion toàn shop dd_1010 / gift / live / normal | 37,0 / 46,7 / 39,9 / 41,8% | 37,0 / 46,5 / 40,1 / 41,5% |
| Phân nhóm / nhân vật demo | 88,3% / 12/12 | 88,3% / 12/12 ✓ |
| Censoring 10.10 | −37,2% | −34,7% ✗ (H3) |
| Drift BINHNUOC500-* | 2/2/4 giờ ✗ | 2/2/3 giờ ✓ |
| Hiệu năng fit / predict | 2,8 s / 4,5 s | 2,4 s / 3,6 s (hai lượt eval chạy song song; máy rảnh trước đây 0,8 s + 1,5 s) |

### H6. Ảnh hưởng tới số demo M3 (what-if)

`demo_whatif --only 1,4g,5,8a` chạy với forecast HEAD và forecast mới (cùng dữ liệu, cùng lúc):
- [1] `schedule_hours:0-2,12-14`, +3,99 tr so với continue — giống nhau;
- [4g] `recommend reduce_budget:0.3` (P đạt 0,81; continue 0,83; +1,66 tr so với continue) — giống nhau;
- [5] `pause`, +5,13 tr so với continue — giống nhau;
- [8a] `undetermined`, best `pause`, p = 0,91 — giống nhau; chỉ khác một dòng lý do (nhu cầu COCKHUAY-HONG, đang hết hàng, 5 → 3 sp).
- iROAS CD002 = 4,42 ở **cả hai** bản (ước lượng ads không đọc dự báo), nên 4,49 → 4,42 không do M2.

Bản quét `whatif_full.*` (~17:30) chạy giữa lúc code censoring/phân nhóm đang sửa dở (khi đó đang thử ADI theo ngày còn hàng, H2, làm đổi nhóm một số SKU); `none_meets_goal` của [4g] không tái hiện với code cuối. Không sửa tài liệu pitch.

## I. FC-09 · Giữ điểm sai số bằng 0 khi chọn mô hình (31/10/2026)

`registry._best` trước đây dùng `pinball_50 or inf` cho xếp hạng và phân xử hai
mô hình có WAPE gần nhau. Điểm pinball bằng 0 bị coi như thiếu dữ liệu, khiến
mô hình có sai số cao hơn được chọn; với điểm thiếu, kết quả còn có thể phụ thuộc
thứ tự đầu vào.

Chỉ `None` được thay bằng vô cực. Điểm 0 được giữ nguyên trong cả hai bước.
Giữ nguyên ngưỡng chênh WAPE, quy tắc thay champion sau hai backtest liên tiếp,
schema và API. Kết quả đã lưu không tự tính lại; thay đổi áp dụng khi chạy backtest
và chọn mô hình tiếp theo.

Test hồi quy chạy qua `select_champions`: WAPE bằng nhau/gần nhau, WAPE bằng 0,
pinball bằng 0 hoặc thiếu, nhóm dùng pinball thay WAPE, và trường hợp ngoài
ngưỡng phân xử. Mỗi trường hợp chạy cả hai thứ tự đầu vào (20 ca). Trước sửa:
11 lỗi / 9 đạt; sau sửa: toàn bộ 43 test trong `test_pure.py` đạt.

## J. Hướng chuyển phân nhóm nhu cầu sang tool bên ngoài (31/10/2026)

Theo yêu cầu người dùng, đích đến là import sáu nguồn, nhận diện sản phẩm và
gửi sang tool bên ngoài để nhận năm nhóm hiện có: `moi`, `ban_thua`, `mua_vu`,
`trend`, `on_dinh`. Đây là nhóm nhu cầu, không phải ngành hàng.

**Chưa triển khai kết nối:** người dùng sẽ cung cấp API sau và đã chọn giữ tạm
cơ chế phân nhóm hiện tại. Bản sửa Diagnostics không tắt classifier, không gọi
dịch vụ ngoài và không ghi nhãn giả là kết quả của tool.

Khi có API, chốt hợp đồng rồi mới thực hiện adapter phía server: lấy tên kèm
SKU/product ID đã đối soát (không ghép chỉ theo tên), gắn yêu cầu với phiên bản
dữ liệu import, kiểm tra năm nhãn hợp lệ, lưu nguồn và lý do nếu tool cung cấp,
và giữ quyền sửa của người dùng. Kết quả trả chậm cho dữ liệu cũ không được ghi
đè dữ liệu mới. Tên sản phẩm một mình không chứng minh được nhãn như bán thưa
hay trend; cần xác minh tool sử dụng lịch sử/tín hiệu nào.

Trước khi chuyển chế độ, bổ sung trạng thái chờ/lỗi/thử lại trong luồng import,
kiểm tra ảnh hưởng chọn mô hình và backtest, và quyết định cách xử lý nhãn cũ.
Trong chế độ tool, trường hợp không có kết quả hợp lệ phải thể hiện chưa phân
loại thay vì âm thầm gán ổn định hoặc giả vờ đã gọi tool. Hợp đồng API, timeout
và chính sách fallback sẽ chốt khi có tài liệu của nhà cung cấp.
