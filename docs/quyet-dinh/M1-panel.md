# Nhật ký quyết định · Panel theo giờ (M1)

Task: PNL-01, PNL-02, PNL-03. Code: `backend/app/panel/` (`build.py`, `stock.py`, `allocation.py`, `prices.py`, `schema.py`). Test: `backend/tests/unit/panel/`.

## API công khai

| Hàm | Đầu vào | Đầu ra |
|---|---|---|
| `build.build_hourly_panel(conn, sku_ids=None, start=None, end=None, *, panel_version=None) -> PanelBuildResult` | `skus`, `bundle_components`, `orders`+`order_lines`, `inventory_snapshots`, `restock_receipts`, `campaigns`, `campaign_skus`, `ad_hourly`, `sale_commitments`, `ingest_batches` | Ghi `hourly_panel` (xóa + chèn khoảng dựng lại, theo thứ tự `(sku_id, hour_utc)`) và `price_events` (upsert) |
| `build.compute_hourly_panel(...) -> PanelFrames \| None` | như trên | DataFrame panel + price_events, không ghi DB |
| `build.load_panel(conn, sku_ids, start, end) -> DataFrame` | `hourly_panel` | DataFrame đã chuẩn kiểu và qua `validate_panel` (đầu vào cho forecast / ads_effect) |
| `schema.validate_panel(df)` | DataFrame | Trả lại `df` hoặc ném `PanelSchemaError` (lớp con `DataQualityError`, mã `PANEL_SCHEMA`) liệt kê mọi lỗi |
| `stock.reconstruct_sku`, `stock.combo_stock`, `stock.stockout_flags`, `allocation.allocate_ad_spend`, `prices.build_price_events` | mảng numpy / DataFrame | Hàm thuần, test riêng được |

## Quyết định

### P1. Lưới giờ luôn bắt đầu từ nửa đêm VN của đơn đầu tiên
- `start`/`end` chỉ giới hạn phần **ghi**. Dựng tồn và cửa sổ 7 ngày luôn tính từ đầu lịch sử, nên dựng lại một khoảng cho kết quả giống hệt dựng toàn bộ (có test).
- `end` mặc định = làm tròn lên giờ của `max(ingest_batches.as_of)` với nguồn `orders`/`inventory`; không có batch thì = giờ của đơn cuối + 1.
- `sku_ids` được **đóng** theo combo ↔ thành phần và các SKU cùng chiến dịch (tồn thành phần phụ thuộc đơn combo; tỷ trọng chia ads phụ thuộc cả chiến dịch). Tập đóng là tập được ghi.

### P2. Số bán
- `units_gross` = mọi đơn trừ `cancelled` (giữ đơn trả). `units_provisional` = phần của đơn `status_final = false`. `orders_count` = số đơn không hủy. `revenue_net` = Σ `unit_price·qty` trừ hủy và trừ trả.
- Đối chiếu với `ban_rong`. Hai ngày cuối lệch lên do đơn "Chờ lấy hàng" sẽ hủy sau thời điểm xuất file (đúng như 02 §8 dự kiến). Test kiểm phần dư **được giải thích trọn** bởi số lượng đơn `awaiting_pickup` của đúng SKU-ngày đó, thay vì ngưỡng 2%: trên `nho` (30 SKU) 6 sp là 4,3% của ngày 10/11, còn trên `demo` là 18 sp.

### P3. Giá (`price_events` do panel ghi)
- 02 §6.3 đặt giá trong phần panel, nên panel tính và ghi `price_events`: trung vị `unit_price` / `list_price` của đơn không hủy theo (SKU, ngày VN); ngày không đơn mang giá trước (`carried_forward`); ngày có `sale_commitments.committed_price` thì giá cam kết thắng (`commitment`). Trước đơn đầu tiên của SKU: không có dòng, panel để `eff_price` NULL và `list_price` = `skus.list_price`.
- `discount_pct = 1 − eff/list`, kẹp về [0, 1].

### P4. Dựng tồn theo giờ (đoạn giữa hai snapshot)
- Đi xuôi giữa hai snapshot bất kỳ (ngày hoặc giờ), nên lịch sử (cuối ngày) và live (cuối giờ) dùng chung một thuật toán; đoạn 1 giờ = `snapshot_hour` (conf 1,0), đoạn dài = `reconstructed` (0,8), đoạn đi qua snapshot hỏng (`on_hand` NULL, tồn âm) = `interpolated` (0,5) + cờ `STOCK_INTERPOLATED`. Snapshot hỏng được **bỏ qua và đi xuôi qua hai ngày** thay vì nội suy tuyến tính: đi xuôi dùng số bán thật nên chính xác hơn (trên mock khớp tuyệt đối cả 3 dòng tồn âm).
- Lượng rời kệ = đơn không hủy của SKU + `qty ×` đơn không hủy của combo chứa nó. Đơn hủy không trừ tồn (mock cũng vậy).
- Sai lệch tại snapshot sau: `|e| ≤ max(3, 5% · on_hand)` thì rải đều vào các giờ có bán; lớn hơn thì `STOCK_JUMP`: nếu e > 0 và có giờ không thể bán được nếu thiếu phần này thì đặt bước nhảy ở giờ đó, còn lại đặt cuối đoạn (snapshot thắng).
- Trước snapshot đầu tiên: suy ngược từ snapshot đó. Sau snapshot cuối: đi xuôi không hiệu chỉnh.
- Kết quả âm (dữ liệu mâu thuẫn) kẹp về 0.

### P5. Giờ nhập hàng
- Mặc định 10:00 VN (02 §6.1). Dời **sớm hơn** khi có đơn chứng minh hàng đã về (tồn không đủ cho đơn trước 10:00). Dời **muộn hơn**, tới giờ đầu tiên cần hàng, khi kệ đã trống trong khoảng từ 10:00 tới giờ đó. Không có tín hiệu thì giữ 10:00.
- Ngày có snapshot theo giờ: đặt vào giờ có mức tăng không giải thích được lớn nhất.
- Lý do chọn "muộn nhất còn khớp số bán" khi kệ trống: giờ hết hàng bị bỏ sót làm model dự báo thấp (03 §4), còn báo thừa chỉ làm mất vài giờ dữ liệu. Trên `demo` cách này đưa recall giờ hết hàng từ 5 giờ sót lên 0 giờ sót, đổi lại thêm ~11 giờ báo thừa trên 216 nghìn giờ.

### P6. Cờ hết hàng
- `stockout = stock_begin ≤ 0` hoặc (`đơn trong giờ ≥ stock_begin` và > 0). "Đơn trong giờ" tính **cả đơn sau này bị hủy**: lúc đặt, chúng đã giữ hàng, nên một giờ "bán sạch" mà có đơn hủy vẫn là kệ trống. (Plan ghi `units_gross`; dùng `units_gross` sẽ sót giờ hết hàng có đơn hủy.)
- Combo: tồn = `min_c floor(tồn_c / qty_c)`; để xét hết hàng, combo tranh hàng với đơn riêng của thành phần trong cùng giờ, nên lượng combo bán được = `min_c floor((stock_begin_c − đơn riêng_c) / qty_c)`.
- Combo chưa khai báo thành phần → tồn NULL, `stockout=false`, cờ `BUNDLE_NO_STOCK_ROW`. SKU thường không có snapshot nào → cờ `NO_STOCK_DATA`.
- `low_stock = stock_begin < 2 × (số bán 168 giờ trước / 7)`.

### P7. Vì sao số giờ hết hàng "khác" đáp án
- Đáp án (`het_hang`, `so_gio_het_hang`) chỉ đánh dấu giờ **nhu cầu thật > tồn**, thứ app không quan sát được. Panel đánh dấu mọi giờ nhu cầu **có thể** bị chặn, gồm cả giờ kệ trống mà không ai mua (đêm, SKU bán chậm hết hàng nhiều tuần). Trên `nho`: panel 6.961 giờ, đáp án 1.052 giờ theo ngày; trong cửa sổ có dữ liệu giờ: 3.288 giờ báo thêm thì 3.285 là kệ trống + nhu cầu 0.
- Định nghĩa so được, dựng từ đáp án: `het_hang` hoặc (kệ trống sau giờ đó và mọi nhu cầu đã được bán). Theo định nghĩa này: `nho` 3.816 = 3.816 giờ, lệch 0; `demo` 17.951 so với 17.899 (lệch 0,3%), 98 giờ khác nhau (0,55%), đều do giờ nhập hàng không biết.
- Recall trên chính `het_hang`: 100% (`nho` 528/528, `demo` 1.871/1.871).

### P8. Chia chi ads
- Đúng công thức 02 §6.2, "7 ngày trước t" = 168 giờ trước, không gồm giờ t. Tổng chi SKU = chi chiến dịch từng giờ (sai số dấu phẩy động < 1e-6).
- Trạng thái: có dòng ads → theo dòng (`spend` NULL → `unknown`); không có dòng và trước `campaigns.started_at` → `none`, chi 0; không có dòng sau khi bắt đầu → `unknown`, chi NULL, cờ `ADS_LAG` (sau giờ cuối báo cáo có), `ADS_GAP_DAY` (lỗ ≥ 20 giờ liền), `ADS_MISSING_HOUR` (lỗ ngắn hơn).
- SKU không thuộc chiến dịch nào: `campaign_id` NULL, `none`, chi 0, `ad_alloc_share` NULL.
- SKU thuộc nhiều chiến dịch (mock không có): cộng chi các chiến dịch; `campaign_id`/`ad_status`/`ad_alloc_share` lấy của chiến dịch có mã nhỏ nhất.

### P9. Lịch
- `days_to_gift_peak` có dấu: dương = trước đỉnh, âm = sau đỉnh, tới 20/10 hoặc 20/11 gần nhất (hòa thì lấy đỉnh sắp tới).
- `in_flash_window` từ `sale_commitments(kind='flash_window')`; `sku_id` NULL = áp cho mọi SKU.

### P10. `data_flags`, `panel_version`
- Từ vựng: `PROVISIONAL`, `ADS_GAP_DAY`, `ADS_MISSING_HOUR`, `ADS_LAG`, `STOCK_INTERPOLATED`, `STOCK_JUMP`, `NO_STOCK_DATA`, `BUNDLE_NO_STOCK_ROW` (hằng trong `schema.py`).
- `panel_version` = `max(ingest_batches.batch_id)` (ULID tăng theo thời gian) lúc dựng, giống nhau cho cả lần dựng; tính theo từng dòng thì tốn mà lớp sau chỉ cần một mốc. Có thể truyền vào.

### P11. Chi tiết kỹ thuật DuckDB
- Cột chuỗi được ép sang `string` dtype và `data_flags` đi qua dạng văn bản (`string_split`) khi chèn: DuckDB đoán kiểu cột object từ một mẫu đầu bảng, cột bắt đầu bằng nhiều NULL / list rỗng bị đoán thành `NULL` và lỗi khi gặp giá trị thật (đã gặp trên `demo`).
- `_scalar` dùng `fetchall()`: DuckDB 2.0 giữ kết quả dở của `fetchone()` và chặn lệnh `register` tiếp theo.

## Kết quả trên mock

| | `nho` (30 SKU) | `demo` (300 SKU) |
|---|---|---|
| Dòng panel | 86.400 | 864.000 |
| Thời gian dựng | ~0,6 s | ~4 s (mục tiêu < 20 s) |
| Số bán theo ngày vs `ban_rong` | khớp tuyệt đối tới 09/11; 10/11 dư 6 sp = đơn "Chờ lấy hàng" | khớp tuyệt đối tới 09/11; 10/11 dư 18 sp |
| Tồn cuối ngày vs `ton_cuoi_ngay` | khớp tuyệt đối mọi dòng (cả 3 dòng tồn âm) | khớp tuyệt đối |
| Giờ hết hàng (định nghĩa so được, P7) | 3.816 / 3.816 | 17.951 / 17.899 |
| Tổng chi SKU − chi chiến dịch | < 1e-6 mỗi giờ | — |

`ton_cuoi_gio` theo giờ của thành phần combo trong đáp án được ghi **trước** khi combo cùng giờ trừ tồn; test cộng lại phần này rồi so (còn lệch 0,15% số giờ, do giờ nhập hàng).

## Test tự nạp dữ liệu

`tests/unit/panel/mock_loader.py` đọc CSV thô bằng pandas và chèn thẳng vào bảng chuẩn (bỏ cột PII, bỏ dòng trùng, đổi trạng thái, ads đọc như UTC, mã nội bộ → SKU qua `sku_that.csv` **chỉ trong test**, "Nhập {qty}" → `restock_receipts`, combo từ `repo.seed_bundles`). Khi adapter xong, nên thay bằng fixture `loaded_db` chung (EVAL-01) và giữ nguyên các test chấm điểm.

## Đề xuất ngoài ranh giới (chưa làm)

1. **Adapter phải điền để panel chạy đúng:**
   - `orders.status`/`status_final` theo bảng 02 §2.1 (panel tách hủy bằng `status = 'cancelled'`, tạm bằng `NOT status_final`).
   - `order_lines.sku_id/qty/unit_price/list_price`.
   - `inventory_snapshots.snapshot_at` = **mốc tồn đúng** (ngày → nửa đêm VN hôm sau; live → giờ trong file), `on_hand` NULL khi âm, `granularity`.
   - `restock_receipts(sku_id, received_on, qty)` từ ghi chú "Nhập {qty}". Live: ghi chú lặp lại ở mọi giờ còn lại trong ngày, adapter cần chỉ tạo **một** receipt mỗi (SKU, ngày).
   - `campaigns.started_at` (để phân biệt `none` với `unknown`), `campaign_skus`.
   - `ad_hourly.hour_utc` đã trừ offset; **không chèn dòng giả** cho giờ thiếu (panel tự suy `unknown` từ giờ thiếu); nếu adapter muốn chèn dòng `unknown` thì để `spend` NULL.
   - `sale_commitments` `flash_window` (`window_start/end` UTC, `sku_id` NULL) và `committed_price` (`sku_id`, `day_local`, `value_int`).
   - `ingest_batches.as_of` cho nguồn đơn/tồn (panel lấy làm điểm cuối).
   - `skus.is_bundle` không bị hạ về false (đã có trong INF-backend C2).
2. **Pipeline (PNL-04 / ING):** gọi `build_hourly_panel(conn, sku_ids=…, start=…)` sau mỗi batch; panel tự đóng tập SKU. Cập nhật một giờ live hiện dựng lại toàn bộ lưới cho tập SKU đó (~4 s cho 300 SKU trên `demo`); nếu cần < 2 s thì PNL-04 nên chỉ đi lại từ snapshot gần nhất.
3. **Schema:** không cần cột mới. Có thể cân nhắc thêm `stock_adjustment` (số lượng nhảy tồn đã áp) nếu UI muốn hiện, hiện chỉ có cờ `STOCK_JUMP`.
4. **pytest:** test `demo` gắn marker `e2e` đã có sẵn; bỏ qua bằng `-m "not e2e"`. DuckDB `.df()` sinh `Pandas4Warning` (inplace) từ bên trong thư viện; có thể lọc trong `pyproject.toml` (`filterwarnings`).
