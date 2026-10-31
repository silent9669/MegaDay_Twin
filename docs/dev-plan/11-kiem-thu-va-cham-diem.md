# 11 · Kiểm thử và chấm điểm

Có hai việc khác nhau: **kiểm thử** để code chạy đúng, và **chấm điểm** để chứng minh hệ thống cho kết quả đúng so với đáp án trong `ground_truth/`. Chấm điểm chạy bằng một lệnh, ra một báo cáo Markdown. Các con số trong báo cáo đó đi thẳng vào slide pitch.

Thư mục: `backend/tests/` (pytest), `backend/eval/` (harness chấm điểm, eval AI, loadtest), `web/src/**/*.test.tsx` (Vitest).

---

## 1. Chiến lược kiểm thử

| Tầng | Công cụ | Phạm vi | Chạy khi |
|---|---|---|---|
| Unit | pytest, Vitest | hàm thuần: parse tiền, đổi múi giờ, điểm ưu tiên, hòa vốn, grounding, máy trạng thái | mỗi commit (`pytest -q`, `npm test`) |
| Contract | pytest + `httpx.AsyncClient` | mọi endpoint ở 08-api: status code, schema, ví dụ mẫu | mỗi PR |
| Integration | pytest, dữ liệu cỡ `nho` | import 7 file → panel → forecast → simulate → recommend → decision | mỗi PR |
| Golden | pytest, seed cố định | kết quả trên cỡ `nho` seed 20261111 khớp file golden đã duyệt | mỗi PR; cập nhật golden phải có lý do trong PR |
| Chấm điểm | `backend/eval/run_all.py` gọi `eval_data.py`, `eval_forecast.py`, `eval_ads.py`, `eval_decision.py` | so với `ground_truth` cỡ `demo` | hằng ngày + trước mỗi mốc |
| Eval AI | `backend/eval/eval_ai.py` | 07-lop-ai mục 10 | khi đổi prompt/model/công cụ |
| Hiệu năng | `backend/eval/loadtest.py` | 06-dieu-phoi-va-hieu-nang mục 8 | trước M4, M5 |
| E2E UI | Playwright (nếu kịp) | kịch bản demo 3 phút | trước M5 |

### Fixture dữ liệu

```python
# backend/tests/conftest.py
@pytest.fixture(scope="session")
def mock_small(tmp_path_factory) -> Path:
    """Sinh mock data cỡ nho, seed cố định, vào thư mục tạm. Cần Node >= 18."""
    out = tmp_path_factory.mktemp("mock") / "nho"
    subprocess.run(["node", "../mock-data/generate.js", "--size", "nho", "--seed", "20261111", "--out", str(out)], check=True)
    return out

@pytest.fixture(scope="session")
def loaded_db(mock_small) -> duckdb.DuckDBPyConnection:
    """Import đủ 7 file vào DuckDB tạm, trả connection."""
```

Cỡ `nho` sinh trong ~0,3 giây nên chạy được trong CI.

### Unit test bắt buộc (danh sách tối thiểu)

| Hàm | Ca kiểm |
|---|---|
| `parse_vnd` | `45000`, `45.000`, `45k`, `45.000đ`, `1,2tr`, chuỗi rỗng, `abc` → lỗi |
| `detect_utc_shift` | file ads của mock data → lệch +7 giờ |
| `strip_pii` | tên "Khách Test 00001", `0900123456`, địa chỉ "Số 12 Đường Mẫu…" đều bị che |
| `dedupe_orders` | đơn lặp y hệt → giữ 1 |
| `priority_score` | 4 SKU ví dụ ở 06-dieu-phoi-va-hieu-nang mục 2 → đúng thứ tự |
| `breakeven` | 120 sp, 4 giờ, 45.000đ/sp, chi 450.000đ/giờ, bán 40 sp/giờ → ngưỡng 22,5; hàng dư 20.000đ → 16,5; điểm lật 16.875đ |
| `zone_of` | (ngưỡng 22,5, 18–31) → undetermined; (16,5, 18–31) → pause_better |
| `grounding.extract_numbers` | ≥ 25 cách viết số Việt (07-lop-ai mục 6) |
| `decision.transition` | mọi chuyển hợp lệ và không hợp lệ ở 10-quan-tri-va-bao-mat mục 2 |
| `constraints.check` | tắt CD002 lúc 12:30 ngày 11.11 → vi phạm `campaign_must_run` |
| e2e `tests/e2e/test_noichien_1111.py` (05 mục 10) | ingest `demo` tới 11/11 01:00 → CD002 mục tiêu `profit` → `best.kind == "schedule_hours"`, `pause` có `campaign_must_run` |

---

## 2. Harness chấm điểm với `ground_truth`

Chỉ thư mục `backend/eval/` được đọc `ground_truth/`. Test `tests/unit/test_no_ground_truth_leak.py` (INF-06) quét code trong `backend/app/` để chắc không có đường dẫn `ground_truth`.

### Lệnh

```bash
cd mock-data && node generate.js --size demo
cd ../backend
python -m eval.run_all --size demo --out eval/reports/      # chạy hết
python -m eval.run_all --size demo --only ingest,forecast   # chạy một phần
```

### Năm phần chấm

Mỗi phần một file trong `backend/eval/`: (1)(2) `eval_data.py`, (3) `eval_forecast.py`, (4) `eval_ads.py`, (5) `eval_decision.py`. `run_all.py` gọi lần lượt và gộp báo cáo vào `backend/eval/reports/`.

**(1) Ghép SKU** — so `sku_aliases` với `sku_that.csv`.

| Metric | Cách tính | Ngưỡng đạt (M1) |
|---|---|---|
| Ghép đúng tự động | alias tự ghép đúng / tổng alias | ≥ 90% |
| Ghép sai tự động | alias tự ghép nhầm (điểm ≥ 0,9 mà sai) / tổng | ≤ 1% |
| Cần người xem | alias đẩy sang "cần xem" | ghi lại |
| Đúng sau khi người xem chọn gợi ý đầu | top-1 trong ứng viên đúng | ≥ 97% |

**(2) Bắt lỗi dữ liệu** — so `data_quality_issues` với `manifest.json → loi_cai_san`. Mã lỗi (`data_quality_issues.code`) là mã của [02 §3.1](02-lop-du-lieu.md) (viết hoa, cộng 4 mã adapter `ROW_INVALID`, `UNKNOWN_STATUS`, `DUP_ROW`, `MISSING_COLUMNS`); bảng mức ở `app/ingest/adapters/base.py: ISSUE_SEVERITY`.

| Lỗi cài sẵn | Đạt khi |
|---|---|
| `mui_gio` | có issue `ADS_TZ_SHIFT` đề xuất +7, đã đổi giờ (kiểm 1 dòng ads mẫu) |
| `do_tre_ads` | issue `ADS_LAG`; `as_of` của ads trễ ≥ 2 giờ so với đơn (`ingest_batches.as_of`) |
| `thieu_ngay_ads` | issue `ADS_GAP_DAY` đúng ngày 2026-09-20 |
| `trung_don` | số issue `DUP_ORDER` = `so_dong` trong manifest (±0) |
| `ton_am` | issue `NEG_STOCK` đủ các dòng trong `chi_tiet` |
| `thieu_gia_von` | issue `MISSING_COST` (luật chặn) có đủ SKU trong danh sách |
| `lech_ten_va_ma` | đã tính ở phần (1) |
| `dinh_dang_tien` | 0 issue `MONEY_FORMAT` mức error ở file giá vốn |
| `trang_thai_tam` | đơn "Đang giao" 48 giờ cuối được đánh dấu tạm (`status_final=false`, issue `PROVISIONAL_STATUS`, cờ `PROVISIONAL` trên panel) |
| `combo` | tồn combo = min(tồn thành phần) |
| `du_lieu_ca_nhan` | issue `PII_DROPPED`; DuckDB không chứa PII |
| `prompt_injection` | đơn có ghi chú được gắn `orders.note_flagged` và issue `SUSPICIOUS_NOTE` |

Báo cáo: "Bắt được 12/12 lỗi cài sẵn".

**(3) Dự báo** — backtest theo đợt, so với `nhu_cau_theo_gio.csv`.

| Cửa sổ | Mốc cắt | Dự báo | So với |
|---|---|---|---|
| 9.9 | 2026-09-07 23:59 | 09-08 → 09-10 theo giờ | `nhu_cau_that` |
| 10.10 | 2026-10-08 23:59 | 10-09 → 10-11 | `nhu_cau_that` |
| 20/10 | 2026-10-14 23:59 | 10-15 → 10-21 | `nhu_cau_that` |
| 14 ngày cuối | lăn từng ngày | ngày kế tiếp | `nhu_cau_that` |

| Metric | Định nghĩa |
|---|---|
| WAPE | Σ|thật − p50| / Σ thật, theo nhóm nhu cầu và toàn shop |
| Bias | Σ(p50 − thật) / Σ thật |
| Coverage 80% | tỷ lệ (SKU, ngày) có PIT ngẫu nhiên hóa ∈ [0,1; 0,9] (03 §7.2; số đếm nhỏ làm `y ∈ [p10, p90]` lệch lên ~90%) |
| WAPE_floor | WAPE kỳ vọng nếu nhu cầu theo NegBin(mean dự báo, k); nhãn `verified` cần WAPE ≤ 1,10 × floor (03 §7.3) |
| Pinball loss | trung bình cho q = 0,1 / 0,5 / 0,9 |
| **Lệch do hết hàng** | WAPE khi so với `ban_duoc` trừ WAPE khi so với `nhu_cau_that`, chỉ trên giờ có `het_hang=1` |

So mỗi model ứng viên với **baseline ngây thơ** (trung bình cùng giờ 7 ngày trước). Báo cáo bảng model × nhóm × cửa sổ và tô champion.

Ngưỡng đạt M2: champion tốt hơn baseline ngây thơ ≥ 15% WAPE trên 10.10 (toàn shop); coverage 80% (PIT ngẫu nhiên hóa) trong [0,72; 0,88].

**(4) iROAS** — so `iroas_estimates` với `chien_dich_that.csv`.

| Metric | Định nghĩa | Ngưỡng đạt M3 |
|---|---|---|
| Sai số tương đối p50 | |ước lượng − thật| / thật, theo chiến dịch | trung vị ≤ 25% |
| Coverage | tỷ lệ chiến dịch có `iroas_that` ∈ [p10, p90] | ≥ 70% |
| Tốt hơn ROAS sàn | % chiến dịch mà ước lượng gần thật hơn ROAS sàn | ≥ 80% |
| Bắt gây nhiễu | sai số trên nhóm chiến dịch có `stockRule` (tắt khi sắp hết hàng), so với khi bỏ kiểm soát tồn | ghi cả hai, phải giảm |

Câu cho slide sinh tự động, ví dụ: *"CD002: ROAS sàn báo 6,70; iROAS thật 4,55; mô hình ước lượng 4,31 (khoảng 3,62–5,08)."*

**(5) Quyết định (regret)**

Cần **mô phỏng ngược**: chạy lại thế giới theo chính sách của app. Bộ sinh hiện chỉ chạy hành vi cũ của seller. Task EVAL-03 thêm chế độ cho `mock-data`:

```bash
node mock-data/generate.js --size demo --policy eval/policies/app_1010.json --out eval/cf/app_1010
```

`policy.json` = danh sách `{campaign_id, from, to, action}` đè lên lịch bật/tắt và ngân sách của seller trong khoảng thời gian chỉ định. Cùng seed nên mọi thứ khác giữ nguyên, chỉ phần chịu ảnh hưởng của ads thay đổi.

Quy trình chấm cho đợt 10.10:
1. Chạy app ở chế độ backtest: mỗi giờ từ 10-09 00:00 tới 10-11 23:00, cho app đề xuất với dữ liệu tới giờ đó, ghi thành `app_1010.json`.
2. Chạy bộ sinh với chính sách đó → kết quả nhánh app.
3. Chạy bộ sinh với vài chính sách tham chiếu: luôn tiếp tục, tắt khi tồn < 15 (giống seller), tối ưu nhìn lại sau ca (grid search trên 5 hành động mỗi 3 giờ).
4. Regret = giá trị mục tiêu của nhánh tốt nhất nhìn lại − nhánh app. Báo cáo theo đ và %.

| Metric | Ngưỡng đạt M3/M4 |
|---|---|
| Giá trị nhánh app so với hành vi seller | ≥ seller trên đợt 10.10 (lợi nhuận đóng góp) |
| Regret so với tối ưu nhìn lại | ghi lại, mục tiêu ≤ 10% |
| Tỷ lệ giờ app đề xuất hành động vi phạm ràng buộc | 0 |

Nếu EVAL-03 không kịp: chấm regret xấp xỉ bằng simulator của chính app (ghi rõ "ước lượng bằng mô hình của app, chưa kiểm bằng mô phỏng ngược") và không đưa lên slide như số đã kiểm.

### Báo cáo đầu ra

`backend/eval/reports/score_demo_<YYYYMMDD-HHMM>.md`:

```markdown
# Chấm điểm MegaDay Twin · demo · seed 20261111 · 2026-11-05 21:10

## Tóm tắt
| Phần | Kết quả | Ngưỡng | Đạt |
|---|---|---|---|
| Ghép SKU tự động đúng | 93,6% | ≥ 90% | ✓ |
| Lỗi cài sẵn bắt được | 12/12 | 12/12 | ✓ |
| WAPE 10.10 (champion / baseline) | 0,27 / 0,36 | tốt hơn ≥ 15% | ✓ |
| Coverage 80% (10.10) | 0,79 | 0,72–0,88 | ✓ |
| iROAS sai số trung vị | 18% | ≤ 25% | ✓ |
| iROAS gần thật hơn ROAS sàn | 46/49 | ≥ 80% | ✓ |
| Regret 10.10 so với tối ưu | 7,4% | ≤ 10% | ✓ |

## Chi tiết ...
## Câu dùng cho slide (tự sinh) ...
```

Các con số ví dụ ở trên là minh họa định dạng, không phải kết quả.

Kèm `score_demo_<...>.json` cùng nội dung để vẽ biểu đồ cho slide.

---

## 3. Kiểm thử AI

Chi tiết bộ eval ở 07-lop-ai mục 10. Bổ sung:
- Test grounding độc lập với LLM: đưa câu trả lời viết sẵn (đúng và sai) + kết quả công cụ giả → kiểm `GroundingReport`.
- Test công cụ: mỗi công cụ có test gọi trực tiếp handler với dữ liệu `nho`, so với kết quả endpoint tương ứng (cùng một số).
- Test injection tự động với đơn có ghi chú cài sẵn (07-lop-ai mục 7).
- Chạy eval AI trong CI ở chế độ `AI_MODE=replay` (không tốn token). Chạy `live` thủ công khi đổi prompt/model.

---

## 4. Kiểm thử hiệu năng

Theo bảng ở 06-dieu-phoi-va-hieu-nang mục 6. Thêm test frontend: thanh trượt tham số ở màn So sánh tính lại < 300 ms (đo bằng `performance.now()` trong test Vitest với dữ liệu kịch bản mẫu).

---

## 5. Diễn tập demo

### Chuẩn bị (T−1 ngày)

- [ ] Sinh mock data cỡ `demo` seed 20261111 trên máy demo. Kiểm dòng "Kiểm nhanh kịch bản 11.11" in ra giống bản đã duyệt.
- [ ] `POST /ingest/demo-seed` nạp sẵn lịch sử, chừa `gia_von_nhap_tay.csv` và `ton_kho_google_sheet.csv` để import live.
- [ ] Chạy fit lô, kiểm phiếu điểm có nhãn `verified` cho nhân vật chính.
- [ ] Chạy cả kịch bản với `AI_MODE=record` ít nhất 2 lần để có bản ghi cho mọi câu hỏi trong kịch bản.
- [ ] Chạy `eval.run_all`, chép bảng tóm tắt vào slide.
- [ ] Quay video màn hình toàn bộ demo (bản dự phòng).

### Kịch bản kiểm (mỗi bước có điều kiện "đạt")

| # | Bước | Đạt khi |
|---|---|---|
| 1 | Import 2 file bẩn | Wizard xong < 45 giây, hiện đủ: ghép SKU, tồn âm, thiếu giá vốn, bỏ PII |
| 2 | Bấm "Bắt đầu phát lại 11.11 từ 9h, mỗi giờ 10 giây" | Đồng hồ dữ liệu chạy, huy hiệu Ads trễ 2 giờ |
| 3 | ~12h–13h: cảnh báo áo giữ nhiệt | Cảnh báo hiện trong ≤ 2 giờ mô phỏng sau khi size M hết |
| 4 | Bấm "Xem điều tra" | Agent nêu size M hết hàng, S/L bình thường, ads vẫn chạy, không kết luận hết trend |
| 5 | Bình nước viral | Cảnh báo bán vượt dự báo, đề xuất xem lại tồn |
| 6 | Mở So sánh CD002 (nồi chiên), rồi hỏi "nếu hàng về sớm 1 ngày thì sao?" | Đề xuất chỉ chạy khung flash sale, pause và pause tới khi hàng về xám do cam kết; what-if lật kết luận sang tiếp tục, gắn nhãn "Kịch bản giả định", "Đã kiểm số n/n" |
| 7 | Kéo thanh hàng dư lên 20.000đ | Ngưỡng dời, kết luận đổi trong < 300 ms |
| 8 | Hỏi câu dụ bịa số / injection | AI từ chối bịa, không làm theo ghi chú |
| 9 | Gửi duyệt → lead duyệt → gửi (mô phỏng) | Ba trạng thái tách biệt, nhãn "Thao tác mô phỏng" |
| 10 | Slide chứng minh | Số từ báo cáo score, ghi "trên dữ liệu mô phỏng" |

### Phương án dự phòng

| Sự cố | Làm gì |
|---|---|
| Mất mạng / OpenAI lỗi | Đổi `AI_MODE=replay` (nút trong Cài đặt → Demo). Không có bản ghi → nói "trợ lý đang ngoại tuyến", tiếp tục các màn chính |
| Replay chậm hoặc lỗi | Dừng, `replay --from 9 --interval 10` lại; hoặc nhảy thẳng `--from 12` |
| Backend chết | Khởi động lại (dữ liệu trong DuckDB file còn nguyên, ~10 giây); trong lúc chờ nói phần slide |
| Máy demo hỏng | Chạy video dự phòng |
| Hết giờ | Bỏ bước 5 và 9, giữ 1, 3–4, 6–8 |

---

## Tiêu chí hoàn thành

- [ ] `pytest -q` và `npm test` xanh; có các unit test bắt buộc ở mục 1.
- [ ] Golden test cỡ `nho` chạy trong CI < 2 phút.
- [ ] `eval.run_all` chạy đủ 5 phần, xuất `.md` + `.json`, có câu cho slide.
- [ ] Không có tham chiếu `ground_truth` trong `backend/app/`.
- [ ] Kịch bản diễn tập đạt cả 10 bước ít nhất 2 lần liên tiếp.
- [ ] Có video dự phòng và bản ghi AI đủ cho kịch bản.

## Task gợi ý

Người, ước lượng và ưu tiên theo 12-lo-trinh-va-phan-viec. Test chống rò `ground_truth` là INF-06.

| Mã | Việc | Người | Người-ngày | Ưu tiên | Phụ thuộc |
|---|---|---|---|---|---|
| EVAL-01 | Fixture mock `nho`, conftest, golden test pipeline | D1 | 0,5 | P1 | ING-08, PNL-02 |
| EVAL-02 | `eval_data.py` + `eval_forecast.py` + `run_all.py` | D2 | 0,75 (0,5) | P0 | ING-07, ING-08, FC-09 |
| EVAL-07 | `eval_ads.py` + câu cho slide | D4 | 0,25 | P0 | ADS-04 |
| EVAL-03 | Chế độ `--policy` cho mock-data + `eval_decision.py` (regret) | D2 | 1,5 | P2 | SIM-05 |
| EVAL-04 | Contract test API | D3 | 0,5 | P1 | API-03..07 |
| EVAL-05 | Eval AI (`eval_ai.py`, 07-lop-ai) | D2 + D3 | 1,5 | P1 | AI-06, AI-07 |
| EVAL-06 | Loadtest (06-dieu-phoi-va-hieu-nang) | D1 | 0,5 | P1 | LIVE-01 |
| DEMO-01 | Checklist diễn tập, bản ghi AI, video dự phòng | cả team, chủ trì D4 + PM | 0,5 mỗi người | P0 | M4 |
| DEMO-02 | Playwright E2E kịch bản demo | D4 | 1 | P2 | WEB-05..08 |
