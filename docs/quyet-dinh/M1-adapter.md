# M1 · Quyết định khi làm adapter (ING-01..06)

Phạm vi: `backend/app/ingest/adapters/**`, `detect.py`, `pii.py`, `timezone_check.py` và test `backend/tests/unit/ingest/test_*` (trừ `test_sku_matching.py`). Adapter chỉ đọc file và trả dữ liệu đã chuẩn hóa cùng danh sách vấn đề. Adapter không ghi DB và không ghép SKU. Việc ghi DB, kiểm chất lượng tổng hợp (ING-07) và pipeline thuộc đợt sau.

## 1. Hợp đồng đầu ra: `NormFrame`

- **Vấn đề:** 02 §1 chỉ ghi tên `NormFrame`, không định nghĩa nó. Mặt khác, sheet tồn và file giá vốn chưa có SKU sàn ở bước adapter.
- **Lựa chọn:** `NormFrame` gồm các trường sau:
  - `tables`: từ tên bảng trong `schema.sql` sang DataFrame có đúng tên cột của bảng đó.
  - `source_items`: danh sách định danh gốc để ghép SKU, gồm các cột `source_type, raw_code, raw_name, product_name, variant_name, list_price, n_rows, price`.
  - `extras`: khung phụ, không phải bảng schema, ví dụ `campaigns_seen` của file ads.
  - `issues`: danh sách `DataIssue`.
  - `dropped_columns`, `untrusted_columns`, `as_of`, `tz_offset_hours`, `detail`.
- Dòng chưa có SKU sàn thì để `sku_id = None` và mang định danh gốc trong các cột tiền tố `src_` (`src_raw_code`, `src_raw_name`, `src_fill`, `src_first_seen_at`, …). Pipeline dùng các cột này để ghép SKU rồi bỏ đi bằng `NormFrame.schema_frame(table)` trước khi upsert.
- **Lý do:** tên cột khớp hợp đồng DB, và bước ghép SKU vẫn đủ thông tin.
- **Ảnh hưởng:** pipeline phải ghép xong `sku_id` cho `inventory_snapshots`, `restock_plans`, `restock_receipts`, `cost_items` trước khi upsert, vì các cột này có khóa ngoại tới `skus`.

## 2. Phát hiện lệch múi giờ ads: dùng "ranh giới ngày" thay cho chỉ dùng tương quan

- **Vấn đề:** chạy cách của 02 §3.2 (tương quan nhịp giờ giữa chi ads và đơn hàng) trên mock thì ra **+9** (nho, corr 0,86) chứ không phải +7. Nguyên nhân là ngân sách hết vào buổi tối: chi ads bị "kéo sớm" so với đơn hàng. Lọc riêng các giờ "Đang chạy" thì nho ra +8, demo ra +7, nên vẫn không ổn định.
- **Lựa chọn:** tín hiệu chính là **nửa đêm giờ VN** để lại dấu trong file. Có ba dấu, mỗi dấu cho một giờ `h0` theo cột file:
  1. `Ngân sách ngày` đổi giá trị (ngày đôi gấp 3).
  2. `Hết ngân sách` dừng lại.
  3. Khung 6 giờ `Tạm dừng` ban đêm bắt đầu.

  Từ đó `offset = −h0 (mod 24)`. Chấp nhận khi mọi dấu có mặt đều cho cùng một offset và tương quan tại offset đó ≥ 0,6. Khi không có dấu nào thì quay về cách của plan (corr ≥ 0,8, vượt offset không kề ±1 giờ ≥ 0,1).
- **Kết quả:** nho và demo đều ra **+7**, cả ba dấu đồng ý. `ADS_TZ_SHIFT` có `status=auto_fixed`, `detail.suggestion="+7h"` và `needs_user_confirmation=true` để UI hỏi xác nhận.
- **Ảnh hưởng:** batch sau, ví dụ ads live chỉ có 1 ngày, cần offset đã xác nhận: pipeline truyền `IngestContext.tz_offsets={"ads": 7}`. Nếu không có offset đã xác nhận và cũng không có `orders_hour_profile`, adapter giữ offset 0 và ghi `detail.timezone.source="not_checked_no_orders"`.

## 3. Giờ ads thiếu ghi thành dòng `unknown`

- **Vấn đề:** 02 yêu cầu giờ thiếu (ngày 20/09, 3 giờ trễ cuối lịch sử) mang `ad_status='unknown'` và `spend=NULL`, không được hiểu là chi 0.
- **Lựa chọn:** adapter tự sinh dòng `ad_hourly` với `status='unknown'` và mọi số liệu NULL, đánh dấu `src_fill = gap|lag`:
  - Với gap: sinh cho các chiến dịch đã bắt đầu trước gap.
  - Với lag: sinh cho các chiến dịch còn hoạt động ở giờ cuối, trong khoảng `[ads_as_of, ctx.orders_as_of)`.
- `as_of` của ads vẫn là giờ có dữ liệu thật cuối cùng. Batch sau upsert đè lên các dòng `unknown`.
- **Ảnh hưởng:** trên nho có 168 dòng gap (đúng `so_dong_thieu`) và 24 dòng lag (đúng `so_dong_bi_giu`). Panel và iROAS phải loại giờ `unknown`.

## 4. Mã lỗi mới ngoài 02 §3.1

| Mã | Mức | Khi nào |
|---|---|---|
| `ROW_INVALID` | error | Trường bắt buộc không đọc được (ngày, mã, số lượng ≤ 0, tồn không phải số). Dòng bị bỏ |
| `UNKNOWN_STATUS` | warn | Trạng thái đơn hoặc ads lạ. Đơn được ánh xạ thành `pending` (tạm), ads thành `unknown` |
| `DUP_ROW` | warn | Trùng khóa tự nhiên ở file không phải đơn hàng (ads, tồn, chiến dịch, giá vốn) |
| `MISSING_COLUMNS` | error | Đã nhận diện được file nhưng thiếu cột bắt buộc |

Bảng mức của mọi mã nằm ở `app/ingest/adapters/base.py: ISSUE_SEVERITY`. ING-07 nên dùng chung bảng này.

## 5. Phân chia luật giữa adapter và ING-07

- **Adapter phát hiện** (cần dữ liệu thô): `DUP_ORDER`, `ORDER_TOTAL_MISMATCH`, `PROVISIONAL_STATUS` (1 issue mỗi file kèm số đếm), `NEG_STOCK`, `STOCK_ADJUSTMENT`, `MONEY_FORMAT`, `COST_NOTE_REVIEW`, `ADS_TZ_SHIFT`, `ADS_GAP_DAY`, `ADS_LAG`, `PII_DROPPED`, `SUSPICIOUS_NOTE`, `UNKNOWN_SOURCE`, `UNPARSED_RULE`.
- **Để lại cho ING-07** (cần ghép nhiều nguồn hoặc đã ghép SKU): `STOCK_JUMP`, `MISSING_COST`, `SPEND_OVER_BUDGET`, `UNMATCHED_SKU`, `BUNDLE_NO_STOCK_ROW`.
- `DataIssue` chưa có `issue_id/batch_id/created_at`, pipeline gán khi ghi.

## 6. PII và văn bản không tin cậy

- Cột PII bị lọc ngay trong `pd.read_csv(usecols=...)`, nên không bao giờ nằm trong DataFrame. Tên cột bị bỏ nằm trong `dropped_columns` và issue `PII_DROPPED`.
- Ghi chú người mua, ghi chú tồn, ghi chú giá vốn và câu quy định sale được lưu nguyên văn. Số điện thoại và email trong các ghi chú này bị che thành `***`.
- Các cột chứa văn bản tự do được liệt kê trong `NormFrame.untrusted_columns` (trust = untrusted, theo 07 §9). Câu khớp mẫu lệnh thì gắn `orders.note_flagged` và tạo `SUSPICIOUS_NOTE`. Ghi chú vẫn được giữ, không bị xóa.
- Ký tự che `***` không phải câu hiển thị nên không đưa vào `messages_vi`.

## 7. Sheet tồn

- **Thời điểm snapshot:**
  - Bản theo ngày: `snapshot_at` = 00:00 ngày hôm sau giờ VN, đổi sang UTC.
  - Bản live theo giờ: lấy đúng thời điểm ghi trong file.
  - `received_on` của phiếu nhập là ngày VN mà snapshot khép lại (`snapshot_at − 1h`).
- **Ghi chú live lặp lại:** sheet live lặp lại ghi chú của cả ngày ở mọi giờ, ví dụ "Nhập 40" xuất hiện ở mọi snapshot sau giờ nhập. Cách đếm phiếu nhập theo (mã, ngày):
  - Với mỗi số lượng, lấy số lần xuất hiện lớn nhất trong **một** snapshot.
  - `src_first_seen_at` = giờ đầu tiên thấy ghi chú, panel dùng làm giờ nhập.
- Trên nho, phiếu nhập khớp tuyệt đối `nhap_hang_that.csv` (63 lần về, gộp lại thành 61 cặp mã–ngày).
- `restock_receipts.planned_date` lấy từ kế hoạch "Đang về" gần nhất được thấy **trước** snapshot có phiếu nhập.

## 8. Giá vốn

- Theo hợp đồng với agent ghép SKU, `source_items.raw_code = "row:<số dòng trong file>"` (dòng 1 là header), và `cost_items.src_raw_code` cũng vậy. Lý do: demo có 4 cặp dòng trùng tên khác giá, mà `sku_aliases` là UNIQUE trên `(source_type, raw_code, raw_name)`.
- Chỉ dòng giống hệt nhau mới tính `DUP_ROW`.
- `price` trong `source_items` = giá vốn, dùng làm tín hiệu cho bước ghép SKU.
- Tên được giữ nguyên văn, kể cả khoảng trắng kép.
- Phí đóng gói để trống thì tính là 0. Không đọc được thì cũng tính 0 và tạo `MONEY_FORMAT` với `status=auto_fixed`. Giá vốn không đọc được thì dòng đó không vào `cost_items`, nhưng vẫn nằm trong `source_items`.

## 9. Đơn hàng và danh mục

- Adapter đơn hàng tự sinh `products` / `skus` từ SKU sàn, vì đây là khóa chuẩn nên không cần ghép.
  - `product_id` theo `repo.derive_product_id`. SKU bắt đầu bằng `COMBO` giữ nguyên làm product, khớp seed `COMBO-NOICHIEN`.
  - Không xuất cột `is_bundle` và `first_sale_at`, để upsert không hạ `is_bundle` của combo và batch live không ghi đè ngày bán đầu.
- `as_of` là mốc **mở** (đầu giờ sau giờ cuối có dữ liệu): đơn tới 23:57 ngày 10/11 cho ra `as_of` = 2026-11-10T17:00Z.
- Nhiều dòng cùng mã đơn nhưng khác nội dung được hiểu là đơn nhiều dòng (`line_no` 1, 2, …), không tính là trùng.

## 10. Chương trình sale

- `commitment_id` là giá trị xác định: `sc-` + sha1 của (chương trình, ngày, kind, đích, vị trí). Nạp lại file thì upsert đúng các dòng cũ.
- `campaign_must_run` sinh một dòng cho mỗi khung flash sale, có `window_start/end` theo UTC.
- `quy_dinh_chung` được lưu thành `rule_text` và không tạo issue.
- Quy định chiến dịch không nhận diện được thì lưu `rule_text` và tạo `UNPARSED_RULE`.
