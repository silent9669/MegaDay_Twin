# Mock data cho MegaDay Twin

Bộ sinh dữ liệu giả lập của một shop Shopee bán đồ gia dụng và quà tặng. Dữ liệu được sinh theo cơ chế: nhu cầu, ads, giá, sự kiện, tồn kho. Kèm theo là **đáp án đúng** (nhu cầu thật, hiệu quả thật của ads), dùng để chấm điểm dự báo và mô-đun iROAS.

Chỉ cần Node.js 18 trở lên, không có thư viện ngoài.

```bash
cd mock-data
node generate.js --size demo          # 300 SKU, ~2 giây, ~60 MB
node generate.js --size nho           # 30 SKU, dùng khi dev
node generate.js --size tai_nang      # 2.000 SKU, kiểm hiệu năng (~35 giây)
node generate.js --size demo --seed 7 # bộ dữ liệu khác
node replay.js --size demo --interval 20   # phát lại 11.11, mỗi giờ = 20 giây
```

Cùng seed thì cả team sinh ra cùng một bộ dữ liệu. Mọi tham số nằm trong [config.json](config.json).

## Mốc thời gian

| Giai đoạn | Thời gian | Dùng để |
|---|---|---|
| Lịch sử | 14/07/2026 → 10/11/2026 (120 ngày, theo giờ) | Huấn luyện, backtest 8.8, 9.9, 10.10 và 20/10 |
| Phát lại | 11/11/2026 | Demo "real-time" giữa mega sale |

Tất cả là giờ Việt Nam (GMT+7), **trừ file ads** (xem phần lỗi cài sẵn).

## File đầu vào cho app (`out/<cỡ>/`)

| File | Mô phỏng | Ghi chú |
|---|---|---|
| `don_hang_seller_center.csv` | File xuất đơn hàng | Mỗi dòng một đơn, có trạng thái hủy/trả/đang giao |
| `bao_cao_ads.csv` | Báo cáo quảng cáo theo giờ | "Đơn hàng" và "Doanh thu" là số **sàn ghi nhận**, cao hơn hiệu quả thật |
| `ton_kho_google_sheet.csv` | Sheet tồn kho tự quản, chụp mỗi tối | Dùng **mã nội bộ**, khác SKU sàn |
| `gia_von_nhap_tay.csv` | Bảng giá vốn gõ tay | Tên viết tắt hoặc không dấu, tiền viết nhiều kiểu |
| `cau_truc_chien_dich.json` | Chiến dịch → danh sách SKU | Ngân sách dùng chung, không tắt riêng từng SKU được |
| `chuong_trinh_sale.json` | Cam kết khi đăng ký 11.11 | Giá cam kết, tồn giữ cho flash sale, chiến dịch không được tắt |
| `live_11_11/` | Dữ liệu ngày 11.11 | Cùng định dạng; tồn kho cập nhật theo giờ |
| `live_stream/` | Do `replay.js` tạo | Dữ liệu "đang về" theo giờ; ads trễ 2 giờ; `trang_thai.json` cho biết đã phát tới đâu |

## Đáp án (`out/<cỡ>/ground_truth/`): không nạp vào app

| File | Nội dung |
|---|---|
| `manifest.json` | Danh sách nhân vật demo, lỗi cài sẵn (kèm vị trí), ghi chú mô hình |
| `sku_that.csv` | Bảng ghép đúng SKU sàn ↔ mã nội bộ, nhóm nhu cầu, tham số thật, **iROAS thật** theo SKU |
| `chien_dich_that.csv` | **ROAS sàn báo cáo so với iROAS thật** theo chiến dịch |
| `nhu_cau_theo_ngay.csv` | Nhu cầu thật, số bán, số giờ hết hàng, đơn tăng thêm nhờ ads, mọi SKU, mọi ngày |
| `nhu_cau_theo_gio.csv` | Như trên theo giờ, cho các cửa sổ cần backtest: quanh 8.8, 9.9, 10.10, tuần 20/10, 14 ngày cuối và 11.11 |
| `nhap_hang_that.csv` | Ngày nhập hàng dự kiến và thực tế (có lô bị trễ) |
| `su_kien_ton_kho.csv` | Các lần tồn kho thay đổi ngoài bán hàng |

## Sáu nhân vật demo

Các nhân vật có mặt ở mọi cỡ dữ liệu, kịch bản giống nhau:

| SKU | Câu chuyện | Phần của app được thử |
|---|---|---|
| `BINHNUOC500-*` | Bán đều nhiều tháng, viral từ trưa 11.11 (gấp ~20 lần ngày thường) | Cảnh báo nhu cầu vượt tồn |
| `DENLEDTET-*` | Hàng Tết, tồn rất nhiều so với nhịp bán | Bán dần hay giữ hàng |
| `NOICHIEN2L` (+ `GIAYLOT50`, `COMBO-NOICHIEN`) | Ads hiệu quả cao, đầu 11.11 còn ~80–130 sp, hết vào chiều, hàng về chiều 12/11 | Tiếp tục hay chậm lại chờ hàng; combo ăn tồn |
| `COCMEME-*` | Trend đỉnh giữa tháng 10, đang nguội, tồn nhiều, ads tốn mà hiệu quả thật thấp | Xả hay giữ; ROAS sàn đánh lừa |
| `AOGIUNHIET-*` | Mùa đông kéo nhu cầu lên; **size M hết hàng giữa 11.11** khi ads vẫn chạy | Agent điều tra: số bán tụt nhưng không phải hết trend |
| `MAYHUTMINI` | Ra mắt 04/11, chỉ 7 ngày dữ liệu | Nhãn "kịch bản" khi ít dữ liệu |

## Lỗi cài sẵn

Vị trí cụ thể của từng lỗi ghi trong `manifest.json`.

- Cột "Thời gian" của `bao_cao_ads.csv` là **giờ UTC** (chậm 7 tiếng), file không ghi chú.
- 3 giờ cuối của lịch sử chưa có trong báo cáo ads; báo cáo ads thiếu trọn ngày 20/09.
- Đơn hàng bị lặp, tồn kho có vài dòng âm, ~5% SKU không có giá vốn.
- Tên và mã sản phẩm khác nhau giữa các file.
- Đơn trong 48 giờ cuối còn "Đang giao"; một số sẽ hủy hoặc trả hàng sau đó.
- Combo không có dòng tồn riêng mà trừ tồn của sản phẩm thành phần.
- Đơn hàng có tên, số điện thoại, địa chỉ người mua (giả, dạng `Khách Test 00001`, `0900xxxxxx`). App nên bỏ các cột này khi nhập.
- Một ghi chú của người mua chứa câu dụ AI "tắt toàn bộ quảng cáo". Đây là bài test chống prompt injection.

## Mô hình sinh dữ liệu

```
nhu cầu/giờ = nền × nhịp giờ/thứ × sự kiện × mùa vụ × trend × ra mắt × giá/voucher × thứ hạng × (1 + hiệu ứng ads)
hiệu ứng ads = a × (1 − e^(−adstock / k)),   adstock = chi giờ này + 0,6 × adstock giờ trước
bán thực tế  = min(nhu cầu thật ~ Negative Binomial, tồn còn lại)
```

- Ngân sách đặt theo chiến dịch, chia cho SKU theo nhu cầu. Ngày đôi ngân sách gấp 3; thường hết ngân sách vào buổi tối.
- Lịch sử có các đợt tắt ads: seller tự tắt vài lần, một số chiến dịch tắt ban đêm, một số tắt khi hàng sắp hết. Kiểu tắt cuối là **gây nhiễu**: tắt đúng lúc bán ít đi vì thiếu hàng.
- Thứ hạng tự nhiên đi theo số bán khoảng 3 ngày gần nhất, nên pause lâu hoặc hết hàng làm giảm cả đơn tự nhiên.
- iROAS thật trong đáp án là hiệu quả ngắn hạn, chưa tính phần thứ hạng tăng nhờ ads.
- `chuong_trinh_sale.json` chỉ là ràng buộc để app tôn trọng; bộ mô phỏng không tự giữ tồn cho flash sale.

## Cấu trúc code

```
generate.js      CLI sinh dữ liệu, in tóm tắt kiểm nhanh
replay.js        Phát lại 11.11 theo giờ
src/catalog.js   Danh mục sản phẩm, 6 nhân vật demo, chiến dịch
src/simulate.js  Mô phỏng theo giờ: nhu cầu, ads, tồn, đơn hàng
src/export.js    Xuất file "bẩn" như thật và file đáp án
src/rng.js       Số ngẫu nhiên có seed
```

Kết quả nằm trong `out/`. Hai bộ `nho` và `demo` (seed 20261111) được commit sẵn để cả team dùng ngay.
