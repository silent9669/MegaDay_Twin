# Nhật ký quyết định · Pipeline nạp dữ liệu, kiểm chất lượng, live, chấm điểm (M1 · đợt 2)

Task: ING-07 (`backend/app/ingest/quality.py`), pipeline (`backend/app/ingest/pipeline.py`), PNL-04 (`ingest_live_delta`), CLI (`ingest`, `ingest-live`, `rebuild-panel`, `eval`), EVAL-01 (`tests/mockdata.py`, `loaded_db`), chấm điểm lớp dữ liệu (`backend/eval/eval_data.py`, `backend/eval/live_replay.py`).

Mỗi mục ghi: vấn đề, lựa chọn, lý do, ảnh hưởng.

## Kết quả chạy thật (demo, seed 20261111, máy dev Windows)

| Tiêu chí M1 (12) | Kết quả | Ngưỡng | Đạt |
|---|---|---|---|
| Nạp file cỡ demo | 6 file lịch sử 11,8–12,8 giây (`cli ingest`, tổng lệnh 14 giây); `live_11_11` (3 file cả ngày 11.11) thêm 3,1 giây | < 60 giây | ✓ |
| Ghép SKU tự động đúng | 97,1 % (560/577 alias mã nội bộ + giá vốn); 98,1 % cả 3 nguồn (860/877) | ≥ 90 % | ✓ |
| Ghép SKU tự động sai | 0 alias (0 %), 0 nhầm biến thể | ≤ 1 % | ✓ |
| Lỗi cài sẵn bắt được | 12/12 | 12/12 | ✓ |
| `hourly_panel` | 300 SKU × 2.880 giờ = 864.000 dòng | 300 × 2.880 | ✓ |
| PII trong DuckDB | 0 ô khớp (tên, địa chỉ, số điện thoại) ở mọi cột văn bản; 0 lần thấy chuỗi PII trong file DuckDB | 0 | ✓ |
| PNL-04: nạp 1 giờ live | đợt 2: 2,2–2,5 giây máy rảnh, 6,2–7,5 giây máy bận. **Sau "Hoàn thiện M1": p50 1,5–2,6 · p95 3,3–4,3 giây khi máy bận** (§21) | < 5 giây | ✓ |

Ngoài tiêu chí M1 (02 §8): tồn cuối ngày khớp tuyệt đối 100 %; cờ hết hàng recall 100 % trên `het_hang`, precision/recall 99,6 %/99,9 % theo định nghĩa so được; gợi ý đầu đúng 99,3 %; số bán theo ngày khớp tuyệt đối tới 08/11, nhưng ngày 10/11 lệch 2,2 % so với ngưỡng 2 % (§18).

## 1. Thứ tự các bước khác sơ đồ 02 §1

- **Vấn đề:** sơ đồ 02 §1 đặt `quality.check` ngay sau adapter và trước ghép SKU. Nhưng các luật ING-07 (`MISSING_COST`, `UNMATCHED_SKU`, `STOCK_JUMP`, `BUNDLE_NO_STOCK_ROW`, `SPEND_OVER_BUDGET`) cần SKU đã ghép và dữ liệu của nhiều nguồn đã nằm trong kho.
- **Lựa chọn:** chạy theo thứ tự: batch → nhận diện + adapter → ghi đơn/danh mục/chiến dịch/sale → ghép SKU → gán `sku_id` → ghi ads/tồn/giá vốn → issue của adapter (theo batch) + luật dẫn xuất (tính lại trên toàn kho) → panel → sự kiện. Mọi lệnh ghi (trừ panel) nằm trong một transaction. Panel tự mở transaction riêng, mà DuckDB không cho lồng transaction.
- **Ảnh hưởng:** một file lỗi giữa chừng thì cả lượt rollback. Panel chạy sau khi commit.

## 2. Khóa alias giá vốn dùng `raw_code = ''`, không dùng `row:<n>` (đã thay ở §21.2)

- **Vấn đề:** adapter giá vốn đặt `source_items.raw_code = "row:<dòng>"` (M1-adapter §8). Trong khi đó `sku_matching` coi alias có `raw_code` là "nguồn có mã": nguồn này chạy trước, và luật tên viết tắt mơ hồ (`shorthand`) bị bỏ qua. ING-08 đã chỉnh và kiểm với `raw_code = ''` (M1-ghep-sku Q5, Q6).
- **Lựa chọn:** pipeline dựng `AliasRecord(source_type="cost", raw_code="", raw_name=…, price=…)` và gán `sku_id` cho `cost_items` theo tên (`src_raw_name`). Không sửa adapter và cũng không sửa thuật toán ghép.
- **Lý do:** giữ kết quả đã kiểm của ING-08 (0 ghép sai trên demo).
- **Ảnh hưởng:** trên demo có 4 cặp dòng cùng tên nhưng khác giá. Mỗi cặp gộp thành một alias ở trạng thái `pending` (`duplicate_raw_name`), nên người dùng chỉ chọn được một SKU cho cả cặp. **Đề xuất:** cho `sku_matching` coi mã `row:` là "không có mã", rồi dùng lại `row:<n>` để tách hai dòng.

## 3. Dòng ads "giả" của adapter không được ghi

- **Vấn đề:** adapter ads tự sinh dòng `status='unknown'` cho các giờ báo cáo chưa có (gap 20/09 và 3 giờ trễ cuối), có đánh dấu `src_fill`. Nếu ghi các dòng này, `allocation._align_campaign` thấy "có dòng" nên không gắn cờ `ADS_GAP_DAY`/`ADS_LAG` trên panel.
- **Lựa chọn:** bỏ các dòng có `src_fill` trước khi upsert. Bảng `ad_hourly` chỉ chứa dòng thật. Panel tự suy ra giờ `unknown` (chi NULL) và cờ tương ứng, đúng như M1-panel đề xuất ("không chèn dòng giả").
- **Ảnh hưởng:** issue `ADS_GAP_DAY`/`ADS_LAG` của adapter vẫn được ghi. Số dòng bị thiếu = `hours × campaigns` trong `detail` (demo: 1.128 và 147, khớp manifest).

## 4. Dòng có alias đang chờ thì bị giữ lại, không ghi

- **Vấn đề:** `inventory_snapshots`, `restock_*`, `cost_items` có khóa ngoại tới `skus`. Alias `pending` thì chưa có `sku_id` tin được.
- **Lựa chọn:** chỉ gán `sku_id` từ alias `auto_accepted` hoặc `confirmed`. Dòng không gán được thì không ghi, nhưng được đếm vào `FileReport.rows_held` (CLI in "N dòng chờ xác nhận SKU"). Mỗi alias chờ có issue `UNMATCHED_SKU`.
- **Ảnh hưởng:** demo có 21 dòng giá vốn bị giữ (17 alias). Sau khi người dùng xác nhận (`apply_confirmation`), chạy `cli ingest --force` cho file đó để nạp lại: `force` dùng lại `batch_id` và `received_at` cũ, nên `cost_items.valid_from` không bị nhân bản. API-03 có thể gọi đúng việc này sau bước xác nhận.

## 5. Idempotent

- File đã nạp (cùng `file_sha256`) thì bỏ qua và báo `skipped`. Chạy lại cả thư mục thì không ghi gì, không phát sự kiện (có test).
- Với `force=True`, file được xử lý lại dưới `batch_id` cũ, upsert theo khóa tự nhiên. Số dòng mọi bảng giữ nguyên (có test).
- Issue của adapter có id tất định theo (batch, thứ tự, mã), và được thay toàn bộ khi xử lý lại batch đó. Issue dẫn xuất có id tất định theo (mã, đối tượng, `row_ref`). Issue không còn đúng bị xóa, trừ khi người dùng đã `confirmed`/`dismissed`. Khi upsert, trạng thái người dùng đã đặt không bị ghi đè.

## 6. Giới hạn khóa ngoại của DuckDB 2.0 (không đổi schema)

Ba trường hợp đã gặp khi chạy thật:

| Thao tác | Lỗi | Cách xử lý |
|---|---|---|
| Upsert `skus` với `DO UPDATE SET product_id = …` khi SKU đã bị `bundle_components`/`order_lines` tham chiếu | DuckDB coi việc cập nhật một cột khóa ngoại là xóa rồi chèn | `skus`: chèn SKU mới (`DO NOTHING`), rồi `UPDATE` riêng các cột mô tả (`variant_name`, `list_price`). Không bao giờ đụng `product_id`/`is_bundle` (đúng INF-backend C2) |
| Cập nhật `ingest_batches` (có cột LIST `dropped_columns`, cột UNIQUE `file_sha256`) sau khi issue đã tham chiếu batch | Cập nhật cột LIST hoặc UNIQUE cũng là xóa rồi chèn | Batch được chèn một lần với đủ cột. Sau đó chỉ `UPDATE` các cột vô hướng (`as_of`, `row_count`, `tz_offset_hours`, `status`, `duration_ms`). Issue được ghi sau khi đã cập nhật batch |
| Xóa `order_lines` rồi `orders` trong cùng một transaction (`reset_live_data`) | Kiểm khóa ngoại nhìn trạng thái lúc bắt đầu transaction | Không bọc transaction. Mỗi lệnh tự commit, bảng con xóa trước |

## 7. Ghép SKU giữa các lượt nạp

- Alias đã chốt (`auto_accepted`, `confirmed`, `rejected`) thì được dùng lại, không chấm lại. Chỉ khi có alias mới hoặc alias còn `pending` thì mới chạy `resolve` cho mọi alias tồn và giá vốn của lượt đó (luật tên viết tắt cần cả hai nguồn).
- Nhập riêng file giá vốn (ví dụ `demo-seed` chừa file này lại) thì các alias tồn đã có trong DB được đưa vào làm ngữ cảnh. Luồng xuất kho của chúng tính lại từ `inventory_snapshots` qua SKU đã ghép.
- Kết quả máy không bao giờ hạ một alias `auto_accepted` về `pending`. Lý do: batch sau có thể thiếu bằng chứng, ví dụ sheet live theo giờ không có luồng xuất theo ngày.
- `daily_out` của danh mục = đơn không hủy theo ngày VN cộng phần combo ăn tồn (`add_bundle_consumption` với `bundle_components`). `daily_out` của alias tồn = `stock_flow` trên snapshot theo ngày của file.

## 8. `write_aliases` ghi theo lô (sửa bên trong `sku_matching`, API giữ nguyên)

- **Vấn đề:** trên demo, `write_aliases` mất khoảng 15 giây: gọi `repo.ensure_sku` cho cả 877 alias và `executemany` từng dòng.
- **Lựa chọn:** chỉ `ensure_sku` cho SKU chưa có. Ghi bằng `repo.upsert_df`, với kiểu cột ghi rõ (`string`/`float64`).
- **Ảnh hưởng:** bước ghép SKU trên demo còn 0,6–0,9 giây. Chữ ký hàm và hành vi không đổi; test ING-08 vẫn xanh.

## 9. Nghĩa của `MISSING_COST`

- **Lựa chọn:** mọi SKU có bán (đơn không hủy) mà không có dòng `cost_items` đều bị chặn (mức `error`). `detail.reason` ghi nguyên nhân:
  - `no_cost_file`: chưa nhập file giá vốn nào.
  - `cost_alias_pending`: có dòng giá vốn đang chờ xác nhận, và gợi ý của dòng đó là SKU này.
  - `no_cost_row`: không dòng nào trỏ tới SKU.
- **Lý do:** kinh tế cần chặn cả SKU có giá vốn chưa chắc. Không thể tách sạch hai trường hợp, vì gợi ý của alias mơ hồ có thể trỏ đúng vào SKU thật sự thiếu giá vốn.
- **Ảnh hưởng:** demo có 39 SKU bị chặn = 18 SKU thiếu thật (đủ 18/18 theo manifest) + 21 SKU có dòng giá vốn đang chờ (17 alias). Xác nhận hàng chờ xong thì 21 SKU này hết bị chặn. `eval_data` chấm theo độ phủ của danh sách manifest (11 §2: "đủ SKU trong danh sách").

## 10. Issue dẫn xuất gắn vào batch nào

`data_quality_issues.batch_id` là khóa ngoại bắt buộc. Mỗi issue dẫn xuất gắn vào batch mới nhất của nguồn liên quan:

| Mã | Nguồn liên quan |
|---|---|
| `UNMATCHED_SKU` | tồn, rồi giá vốn |
| `MISSING_COST` | giá vốn, nếu chưa có thì đơn |
| `STOCK_JUMP` | tồn |
| `SPEND_OVER_BUDGET` | ads |
| `BUNDLE_NO_STOCK_ROW` | tồn, nếu chưa có thì đơn |

`BUNDLE_NO_STOCK_ROW` chỉ tính khi đã có ít nhất một file tồn. SKU là gợi ý của alias tồn đang chờ thì không bị tính (đã có `UNMATCHED_SKU`).

## 11. Luật `STOCK_JUMP`

- So `tồn(t₁)` với `tồn(t₀) − lượng rời kệ trong [t₀, t₁) + phiếu nhập`. Bỏ qua snapshot NULL (tồn âm): snapshot hợp lệ kế tiếp được so với snapshot hợp lệ trước. Ngưỡng là `max(5, 10 % kỳ vọng)` (02 §3.1). Lượng rời kệ = đơn không hủy + `qty ×` đơn combo chứa SKU.
- Phiếu nhập tính lúc 10:00 VN giữa hai snapshot ngày. Giữa các snapshot giờ, phiếu nhập được gán cho giờ tăng nhiều nhất mà không giải thích được (giờ nhập thật không biết).
- Trên mock: lịch sử không có bước nhảy nào. Live 11.11 có "Loại 71 áo" (`STOCK_ADJUSTMENT`).
- Live: chỉ kiểm snapshot từ giờ sớm nhất bị ảnh hưởng (đi lùi 3 ngày để có snapshot gốc). Chỉ xóa hoặc thay issue có `detail.snapshot_at` ≥ mốc đó. Nhờ vậy bước kiểm lỗi của một giờ live còn khoảng 0,3–0,9 giây thay vì 3 giây.

## 12. Live delta (PNL-04)

- **Con trỏ:** `live_cursor.file` = đường dẫn tuyệt đối của thư mục + tên file, nên `live_stream/` và `live_11_11/` không lẫn nhau. Thêm một dòng cho `trang_thai.json` (`last_tick` = 3 mốc). Mốc không đổi thì bỏ qua (`unchanged`).
- **Chỉ nạp phần mới:** đọc CSV bằng `csv` (đúng cả trường có dấu phẩy trong ngoặc kép). Các dòng sau con trỏ được ghi ra file tạm có header, rồi đi qua đúng các adapter dùng cho nạp lịch sử (`mode='live'`). Tên batch dạng `live_stream/don_hang_seller_center.csv#20-32`.
- **Phát lại chạy lại từ đầu:** file có ít dòng hơn con trỏ thì `reset_live_data` xóa **mọi** batch live và dữ liệu của chúng (đơn, ads, tồn giờ, phiếu nhập/kế hoạch từ ngày live, issue, panel từ ngày live, con trỏ), rồi nạp lại từ dòng 0. Mock chỉ có một ngày live là 11.11, nên xóa theo batch live gọn hơn xóa theo ngày.
- **`as_of`:** có `trang_thai.json` thì đơn và tồn lấy `don_hang_va_ton_den_het` + 1 phút ("14:59" → 15:00), ads lấy `ads_den_het` + 1 phút ("chưa có" → không ghi đè). Không có file này (thư mục `live_11_11/` cả ngày) thì lấy `as_of` của adapter, và `LiveTick` suy ra từ các batch live.
- **Múi giờ ads live:** dùng lại offset của batch ads trước (live luôn dùng lại; lịch sử dùng lại nếu offset ≠ 0). Batch đang được xử lý lại không tính. Lý do: phát hiện offset trên 1 ngày (mà lại là ngày đôi) không đáng tin.
- **Sự kiện:** `DataUpdated(skus_changed = SKU có trong dòng mới + SKU của các chiến dịch có dòng ads mới)`, rồi `LiveTick(day, orders_inventory_until, ads_until)` mang mốc "HH:59" như `trang_thai.json`.

## 13. Sự kiện

- Mỗi lượt nạp phát một `DataUpdated`, với `batch_id` = batch lớn nhất (ULID mới nhất), `sources`, `as_of` = mốc mới nhất của đơn/tồn, và `skus_changed`. Sự kiện được trả trong `IngestReport.events`. Nếu có truyền `bus` thì được đẩy lên bằng `publish_nowait`; pipeline đồng bộ nên không cần event loop.
- Pipeline không ghi `event_log`. Việc đó do sink `subscribe("*")` của ORC-01 làm.

## 14. Panel khi live và thời gian đo được

- **Lựa chọn:** SKU bị chạm được dựng lại từ giờ sớm nhất bị ảnh hưởng (hoặc từ cuối panel cũ nếu sớm hơn). SKU không bị chạm chỉ được dựng phần giờ mới, vì mốc dữ liệu đã tiến lên. Thực tế sheet tồn live có dòng cho mọi SKU mỗi giờ, nên gần như mọi SKU đều bị chạm.
- **Số đo trên demo (một giờ live = khoảng 130 đơn + 299 dòng tồn + 49 dòng ads):**

  | Lần chạy | Tổng | Panel | Kiểm lỗi | Phần còn lại |
  |---|---|---|---|---|
  | Máy rảnh | 2,2–2,5 giây | 1,5–1,7 giây | 0,25–0,3 giây | khoảng 0,25 giây |
  | Máy đang chạy song song việc khác | 6,2–7,5 giây | 4,8–5,5 giây | 0,7–0,9 giây | khoảng 0,4 giây |

- **Hướng tối ưu (đã làm ở §21.1):** `compute_hourly_panel` luôn tính trên toàn lưới từ đầu lịch sử, nên chi phí của một giờ live ≈ dựng lại toàn bộ panel cho 300 SKU. Nên thêm chế độ cửa sổ:
  - tính từ snapshot hợp lệ cuối cùng trước `start` lùi thêm 168 giờ (cửa sổ 7 ngày của tỷ trọng ads và `low_stock`);
  - mang giá `carried_forward` cuối cùng vào đầu cửa sổ;
  - giữ nguyên cách gọi `build_hourly_panel(sku_ids, start)`.

  Ước tính một giờ live còn dưới 1 giây, đạt cả mục tiêu 2 giây của 02 §6.4. Việc này thuộc PNL-04 tiếp theo / LIVE-01 và cần chạy lại bộ test panel.

## 15. Thống nhất mã lỗi (M0-tich-hop §4)

- Code chỉ dùng mã của 02 §3.1 (viết hoa) cộng 4 mã adapter (`ROW_INVALID`, `UNKNOWN_STATUS`, `DUP_ROW`, `MISSING_COLUMNS`). Bảng mức nằm ở `adapters/base.py: ISSUE_SEVERITY`. Có test bảo đảm mọi mã ghi vào DB đều có trong bảng này.
- Đã sửa tài liệu:
  - 11 §2 (bảng 12 lỗi cài sẵn): `ADS_TZ_SHIFT`, `ADS_LAG`, `ADS_GAP_DAY`, `DUP_ORDER`, `NEG_STOCK`, `MISSING_COST`, `MONEY_FORMAT`, `PROVISIONAL_STATUS`, `PII_DROPPED`, `SUSPICIOUS_NOTE` + `orders.note_flagged`.
  - 08 §3.3: `kind` = `data_quality_issues.code`; `severity` của API ánh xạ `error→blocking`, `warn→warning`.
  - 10 §6: `PII_DROPPED`.
  - 07 (ví dụ `check_data_quality`): `MISSING_COST`.

## 16. Test và fixture dùng chung

- `tests/mockdata.py: load_mock(conn, size, live=…)` nạp mock bằng pipeline thật: seed combo → `ingest_files` → (tuỳ chọn) `ingest_live_delta(live_11_11)`.
- `tests/conftest.py` có `loaded_db` (connection chỉ đọc, `nho` đã nạp + panel, dựng một lần cho cả phiên) và `loaded_ingest`. 11 §1 dự kiến sinh `nho` bằng Node vào thư mục tạm; ở đây dùng bản đã commit, vì job CI `mock-data` đã kiểm bản này khớp bộ sinh, và test không cần Node.
- **Đã thay `tests/unit/panel/mock_loader.py` bằng pipeline thật.** Toàn bộ test panel (gồm test `demo` e2e) vẫn xanh: số bán, tồn cuối ngày, giờ hết hàng, chia ads đều khớp như khi dùng loader riêng. `answer_dir`/`MOCK_OUT` chuyển sang `tests/mockdata.py`.
- `eval/live_replay.py` là bản Python của một bước `replay.js`: ghi `live_stream/` vào thư mục tạm từ `live_11_11/_replay.json`. Nhờ vậy test và chấm điểm phát lại theo giờ được mà không cần Node và không ghi vào `mock-data/`.
- Thêm `filterwarnings` trong `pyproject.toml` cho cảnh báo `inplace` của pandas 3.1. Cảnh báo này phát ra từ bên trong `duckdb .df()` (M1-panel đã đề xuất).

## 17. Chấm điểm (`eval_data.py`)

- Chạy pipeline thật trên DuckDB tạm (tuỳ chọn `--keep-db`), chấm rồi ghi `eval/reports/data_<cỡ>_<YYYYMMDD-HHMM>.md` + `.json`, và in ra màn hình. Mã thoát 1 khi có tiêu chí trong bảng tóm tắt chưa đạt.
- **Ghép SKU:** tính trên `sku_aliases`.
  - Tồn: đáp án theo `ma_noi_bo`.
  - Giá vốn: theo thứ tự dòng của `sku_that` sau khi bỏ SKU thiếu giá vốn (cùng cách căn hàng như test ING-08). Tên trùng thì đáp án là một tập SKU.
  - Ngưỡng 11 §2 tính trên **tổng alias**: tự động đúng / tổng, sai / tổng.
- **`dinh_dang_tien`:** 28 dòng giá vốn demo ghi kiểu "25k" cho giá thật 24.500. App đọc đúng số đã viết (25.000), nên eval coi là đúng nếu bằng giá thật hoặc bằng giá thật làm tròn tới nghìn. Kết quả: 233 khớp tuyệt đối + 28 làm tròn = 261/261.
- **PII:** quét mọi cột VARCHAR/JSON (chuỗi tên, địa chỉ mẫu, số điện thoại 10 chữ số không nằm trong mã hay hash). Sau đó đóng DB và `grep` byte của file DuckDB (Windows khóa file khi đang mở). Quét cả sau khi nạp live.
- `cli eval` gọi `python -m eval.eval_data` ở tiến trình riêng, vì `app/` không được import `eval/` (INF-06).

## 18. Chưa đạt: số bán ngày 10/11 lệch 2,2 % (02 §8 ghi ≤ 2 %)

- Demo dư 18 sp ở ngày 09–10/11. Toàn bộ phần dư nằm trong đơn "Chờ lấy hàng", là đơn sẽ bị hủy sau thời điểm xuất file (M1-panel P2 đã giải thích). App không có cách biết trước đơn nào sẽ hủy.
- Không nới ngưỡng. Báo cáo ghi ✗ kèm `surplus_explained_by_awaiting_pickup = true`. Đây không phải tiêu chí M1 ở 12.
- **Đề xuất:** khi dự báo, coi đơn `awaiting_pickup` của 48 giờ cuối là nhu cầu có xác suất hủy, dùng tỷ lệ hủy lịch sử của trạng thái này. Nếu vẫn cần so với `ban_rong`, đổi ngưỡng ở 02 §8 thành "phần dư ≤ số lượng đơn `awaiting_pickup`" như test panel đang làm.

## 19. Việc nhỏ khác

- Pipeline cập nhật `skus.first_sale_at` = đơn sớm nhất (chỉ lùi về trước, không tiến lên).
- Chiến dịch chỉ thấy trong báo cáo ads → tạo dòng `campaigns` tối thiểu (`started_at` = giờ đầu có dữ liệu). SKU có trong danh sách chiến dịch mà chưa có đơn → `ensure_sku` tạo dòng giữ chỗ.
- `ingest_batches.duration_ms` của mỗi file = thời gian adapter + thời gian ghi các bảng của file đó. Các bước dùng chung (ghép SKU, kiểm lỗi, panel) nằm ở `IngestReport.timings_ms`.
- File không nhận diện được → batch `status='failed'`, `source_type='unknown'` + issue `UNKNOWN_SOURCE`.

## 20. Làm việc song song

Trong lúc làm đợt này có các file `backend/app/forecast/*`, `backend/eval/eval_forecast.py`, `backend/tests/unit/forecast/` (chưa track) đang được viết ở chỗ khác. Đợt này không sửa chúng. Lỗi `ruff`/`ruff format`/`mypy` còn lại đều nằm trong các file đó. Một lần chạy `ruff format eval` của đợt này có thể đã định dạng lại `eval/eval_forecast.py` (chỉ đổi định dạng, không đổi nội dung).

## 21. Hoàn thiện M1

Đợt này làm nốt hai việc còn treo của M1 (§2, §14) rồi chấm lại toàn bộ tiêu chí.

### 21.1 PNL-04: panel theo cửa sổ

**Vấn đề.** `compute_hourly_panel` luôn tính trên toàn lưới từ 14/07, nên một giờ live tốn gần bằng dựng lại panel 300 SKU. Ngoài ra, khi so panel sau mỗi giờ live với bản dựng toàn bộ, phát hiện khoảng ghi của pipeline (§14) **sót ô** ngay cả khi chưa có cửa sổ (trên `nho`, sau giờ 6: 13 ô `eff_price` và 72 ô `data_flags` sai):
- giá là trung vị **cả ngày**, nên đơn mới làm đổi giá của các giờ trước trong cùng ngày;
- dòng ads mới lấp lỗ ads cuối lịch sử: 3 giờ trễ trước đó phải đổi từ `ADS_LAG` sang `ADS_MISSING_HOUR`.

**Lựa chọn.** Tách hai việc:

1. **Khoảng ghi** (pipeline, `_panel_start`). Lấy giờ sớm nhất của dòng mới, rồi lùi về mốc sớm nhất trong ba mốc sau:
   - nửa đêm VN của ngày đó;
   - snapshot hợp lệ cuối cùng ≤ mốc đó của các SKU bị chạm (sai lệch ở snapshot sau được rải cho cả đoạn);
   - đầu lỗ ads ngay trước dòng ads mới của mỗi chiến dịch.

   Nếu một SKU chỉ có snapshot sau mốc (đoạn đầu phải đi ngược), dựng lại toàn bộ lịch sử cho các SKU bị chạm. Tập SKU giữ như cũ: SKU bị chạm, đóng theo combo/thành phần và chiến dịch. SKU khác chỉ ghi phần giờ mới.
2. **Khoảng tính** (panel, `window=True` mặc định khi có `start`). Lưới tính bắt đầu từ nửa đêm VN trước mốc sớm nhất của:
   - `start − 168 giờ` (tổng 7 ngày cho tỷ trọng ads và `low_stock`);
   - với mỗi SKU có tồn: snapshot hợp lệ cuối cùng ≤ `start` mà **không nằm giữa ngày của một phiếu nhập**. Từ snapshot đó trở đi, bước đi tồn không phụ thuộc gì phía trước, và không phiếu nhập nào bị cắt đôi (giờ nhập được đặt theo các snapshot trong ngày).

   Trạng thái vắt qua đầu cửa sổ được mang vào tường minh:
   - `prior_missing`: số giờ ads thiếu liền trước cửa sổ, để lỗ dài ≥ 20 giờ vẫn là `ADS_GAP_DAY`;
   - giá mang theo: một dòng "giả quan sát" ngày trước cửa sổ, với giá hiệu lực cuối và giá niêm yết cuối. Thường lấy trung vị của ngày bán cuối bằng một truy vấn. Nếu có `committed_price` trước cửa sổ thì tính lại đầy đủ.

   `window=False` vẫn đi toàn lịch sử (dùng trong test).
3. **Tối ưu kèm theo** (kết quả không đổi):
   - `build_price_events` vector hóa: 0,57 → 0,04 giây trên demo đầy đủ. Đã so khớp từng dòng với bản cũ, cả ca giá cam kết và giá dự phòng;
   - vòng lặp trong của `quality.check_stock_jump` dùng mảng numpy thay cho `Timestamp` từng phần tử;
   - truy vấn lỗ ads gộp làm một.

**Kiểm chứng (so từng ô, bỏ `panel_version`, gồm cả `price_events`).**
- `nho`: nạp lần lượt 24 giờ live 11.11, sau mỗi giờ panel trong DB **bằng hệt** bản dựng toàn bộ. Cũng bằng hệt khi nhảy giờ (0, 1, 2, 5, 9, 10, 15, 23) và khi replay chạy lại từ đầu. Có test `tests/integration/test_live_delta.py::test_hour_by_hour_panel_equals_full_rebuild`.
- `demo`: các giờ 0, 1, 2, 3, 7, 12 bằng hệt. Test e2e `test_ingest_demo.py` kiểm các giờ 0, 1, 2, 5.
- `tests/unit/panel/test_panel_window.py` có 5 mốc `start` trên lịch sử `nho`: trong ngày mất ads 20/09, giờ nhập hàng, giờ trễ ads cuối, tập con SKU. Có thêm một bản `nho` sửa tay để trạng thái vắt qua đầu cửa sổ: giá cam kết trước cửa sổ, lỗ ads 30 giờ cắt ngang đầu cửa sổ, phiếu nhập đúng ngày mở cửa sổ. Test cũng kiểm số giờ tính chỉ còn khoảng 7–8 ngày trước `start`.

**Số đo trên demo (một giờ live ≈ 17–410 đơn + 299 dòng tồn + 49 dòng ads; 24 giờ).** Máy dev Windows 16 luồng đang chạy song song 2–3 agent khác (dự báo M2, `demo_whatif --scan`). CPU tổng 10–25 %, có lúc dồn cục. Bước `normalize` chậm 2–4 lần so với lúc rảnh (66 ms → 180–300 ms). Để so công bằng, đo **xen kẽ** trên hai bản sao DB (giờ chẵn A trước, giờ lẻ B trước). B = panel toàn lịch sử (`window=False`) + vòng lặp giá cũ. Giờ 0 không tính vào p50/p95.

| Lần đo | Mới (cửa sổ) p50 / p95 / tối đa | Cũ (toàn lịch sử) p50 / p95 / tối đa |
|---|---|---|
| A/B lần 1 | 1,52 / 4,32 / 5,90 giây | 2,90 / 9,62 / 11,90 giây |
| A/B lần 2 (máy bận hơn) | 2,62 / 3,29 / 3,32 giây | 6,64 / 9,78 / 11,71 giây |
| `eval_data --live-hours 24` (máy bận: nạp lịch sử 39 giây, bình thường 14 giây) | 3,43 / 4,21 / 4,81 giây | — |
| A/B lần 1, giờ 2–12 (máy ít bận) | 1,2–1,6 giây | 2,5–2,9 giây |

- Panel một giờ live còn 0,5–0,6 giây khi rảnh và 1,0–1,4 giây khi bận (trước: 1,5–2,1 và 4,8–5,5). Phần còn lại:
  - kiểm lỗi 0,35–1,0 giây;
  - mở file trên Windows khoảng 9 ms/lần × 27 lần, có thể do quét virus file tạm;
  - các bước nạp nhỏ.
- Lần 1 có một đỉnh 5,9 giây (giờ 15). Lúc đó bản cũ chạy xen kẽ cũng nhảy lên 9,6–11,9 giây, và thời gian CPU của chính tiến trình tăng theo, nên đây là tranh chấp máy chứ không phải cửa sổ to ra. Ngoài đỉnh đó, mọi lần đo đều dưới 5 giây.
- Có test đo thời gian `tests/e2e/test_ingest_demo.py::test_demo_live_hour_under_5_seconds`, gắn marker `perf` (chạy riêng khi máy rảnh: `pytest -m perf`). Lần chạy trong đợt này đạt.
- Mục tiêu 2 giây của 02 §6.4 đạt khi máy rảnh (p50 ≈ 1,2–1,5 giây). Khi máy bận chỉ đạt p50 ở lần đo 1.

**Ảnh hưởng.**
- Chữ ký `build_hourly_panel(sku_ids, start, end)` giữ nguyên, chỉ thêm `window: bool = True`. `allocate_ad_spend` thêm `prior_missing`. `stock.DAY_HOURS` là hằng mới.
- Nếu sau này có nguồn ảnh hưởng giờ cũ theo cách khác (ví dụ đơn đổi trạng thái muộn), phải thêm mốc tương ứng vào `_panel_start`. Test so từng ô sau mỗi giờ live sẽ bắt được nếu sót.

### 21.2 Tách các dòng giá vốn cùng tên khác giá

**Vấn đề.** Theo §2, demo có 4 cặp dòng cùng tên, mỗi cặp bị gộp thành một alias `duplicate_raw_name`, nên người dùng chỉ chọn được một SKU cho cả hai dòng.

**Lựa chọn.**
- Adapter vốn đã ghi `row:<dòng>` (M1-adapter §8). `sku_matching` thêm `ROW_CODE_PREFIX` và `has_code()`: mã `row:` được coi là "không có mã" ở mọi chỗ trước đây xét `raw_code`:
  - nguồn có mã chạy trước;
  - thu thập tên viết tắt mơ hồ;
  - luật tên viết tắt;
  - khớp mã chính xác;
  - nhóm gốc mã, `code_pattern`.

  Luật tên viết tắt giữ nguyên. Có test: không có sheet tồn thì dòng giá vốn tự động; có sheet tồn dùng tên đó cho 2 mã thì về hàng chờ, cả khi mã là `''` lẫn `row:`.
- **Tự quyết:** chỉ dòng có tên **lặp lại trong file** mới giữ `row:<n>` làm khóa alias (`pipeline._cost_codes`). Tên duy nhất vẫn dùng khóa `(cost, '', tên)`. Lý do: số dòng đổi khi shop chèn hoặc xóa dòng ở bản file sau. Nếu mọi alias đều gắn số dòng, xác nhận của người dùng sẽ mất ở lần nhập tới. Như vậy mỗi dòng vẫn là một alias riêng, còn khóa của tên duy nhất ổn định. `cost_items` được gán SKU theo cùng khóa `(mã, tên)`.
- DB cũ còn alias gộp `(cost, '', tên)` ở trạng thái `pending` cho tên đã tách: được xóa khi nạp lại. Alias đã có quyết định của người dùng thì không bao giờ xóa.
- `eval_data` thêm đáp án theo khóa `row:<dòng>`: đúng SKU của dòng đó, thay vì tập SKU của cả cặp.

**Kết quả trên demo.**
- 282 alias giá vốn (trước: 278). Tự động đúng 261 (= trước), sai 0.
- Chờ xác nhận 21 (trước: 17). 4 alias gộp thành 8 alias, mỗi dòng một alias, đều `low_margin` với gợi ý 1–1 khác nhau ("Móc dán tường chịu lực - gói 3" → MOCDAN2-G3 / MOCDAN6-G3, "Nến thơm…", "Túi zip… gói 3", "Đèn bàn gỗ decor (mẫu").
- Tỷ lệ tự động trên tổng giảm nhẹ do mẫu số tăng: mã nội bộ + giá vốn 97,1 % → 96,4 % (560/581), cả 3 nguồn 98,1 % → 97,6 %. Vẫn trên ngưỡng 90 %.
- Gợi ý đầu đúng 99,3 %. Lỗi cài sẵn 12/12. `MISSING_COST`: 18/18 SKU thiếu thật, cộng 21 SKU có dòng giá vốn đang chờ (21 alias).

### 21.3 Bảng tiêu chí M1 mới

`uv run python -m eval.eval_data --size demo --live-hours 24`, ngày 30/10, máy bận như mô tả ở §21.1. Báo cáo: `backend/eval/reports/data_demo_20261030-1814.md`.

| Tiêu chí M1 (12) | Kết quả | Ngưỡng | Đạt |
|---|---|---|---|
| Nạp 6 file lịch sử cỡ demo | 39,3 giây khi máy bận (lúc rảnh 14–15 giây, dựng base để đo ở §21.1) | < 60 giây | ✓ |
| Ghép SKU tự động đúng (mã nội bộ + giá vốn) | 96,4 % (560/581); cả 3 nguồn 97,6 % | ≥ 90 % | ✓ |
| Ghép SKU tự động sai | 0 % (0 alias, 0 nhầm biến thể) | ≤ 1 % | ✓ |
| Gợi ý đầu đúng | 99,3 % | ≥ 97 % | ✓ |
| Lỗi cài sẵn bắt được | 12/12 | 12/12 | ✓ |
| `hourly_panel` | 300 SKU × 2.880 giờ = 864.000 dòng | 300 × 2.880 | ✓ |
| PII trong DuckDB | bỏ 3/3 cột; 0 ô; 0 lần thấy chuỗi PII trong file | 0 | ✓ |
| PNL-04: nạp 1 giờ live 11.11 | p50 3,43 · p95 4,21 · tối đa 4,81 giây / 24 giờ (máy bận); A/B xen kẽ p50 1,5–2,6, p95 3,3–4,3 giây | < 5 giây | ✓ |
| Panel live = dựng toàn bộ | 0 ô lệch sau mỗi giờ (`nho` 24/24 giờ, `demo` 6 giờ) | bằng hệt | ✓ |

Tiêu chí ngoài M1 (§18): số bán ngày 10/11 lệch 2,2 % vẫn như cũ, không nới ngưỡng.

### 21.4 Chất lượng

- `ruff check .`, `ruff format --check .`, `mypy app` (110 file) đều sạch, kể cả file của agent khác tại thời điểm chạy.
- `pytest` xanh: `tests/unit/panel`, `tests/unit/ingest`, `tests/integration`, `tests/e2e/test_ingest_demo.py` (gồm test `perf`), `tests/unit/test_no_ground_truth_leak.py`, và phần còn lại của `tests/` (trừ `unit/forecast`, `unit/ai` của agent khác).
- DuckDB thử nghiệm (`backend/data/m1-check*.duckdb`) đã xóa. Không đụng `eval/reports/whatif_full.*`.
