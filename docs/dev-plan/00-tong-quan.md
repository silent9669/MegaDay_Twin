# 00 · Tổng quan

MegaDay Twin giúp người vận hành shop Shopee quyết định **tiếp tục, giảm hay tạm dừng quảng cáo** khi nhu cầu và tồn kho thay đổi, theo đúng mục tiêu kinh doanh của họ. Bản hackathon chạy trên dữ liệu giả lập có đáp án đúng, nên mọi con số app đưa ra đều chấm điểm được.

Tài liệu này dành cho team dev. Đọc xong bộ 00–13 là bắt tay code được, không cần hỏi lại.

## 1. Bài toán và người dùng

| | |
|---|---|
| Người dùng chính | Người vận hành shop/brand (có quyền chỉnh ads hoặc xin lead duyệt) |
| Người dùng phụ | Lead duyệt đánh đổi; người nhận bàn giao ca |
| Câu hỏi app trả lời | "Với tồn kho, mục tiêu và nhu cầu hiện tại, chiến dịch này nên chạy tiếp, giảm, chạy theo khung giờ hay dừng? Chắc chắn đến đâu? Điều gì làm đổi kết luận?" |
| Sàn | Shopee, 1 shop |
| Bối cảnh demo | Lịch sử 14/07 → 10/11/2026, phát lại trực tiếp ngày 11.11 |

Vì sao ROAS không đủ: ROAS sàn báo cáo **phóng đại trung bình 1,47 lần** so với hiệu quả thật trong bộ dữ liệu demo. Ngoài ra ROAS không biết giá vốn, tồn kho, lịch hàng về và mục tiêu của seller.

Ví dụ: ở giá cam kết 11.11, nồi chiên chỉ lãi khoảng 9.300đ/sp. ROAS sàn 8,1 trông rất tốt, nhưng mỗi đồng ads đang lỗ (xem [05](05-kinh-te-va-mo-phong.md) §8).

## 2. Phạm vi bản hackathon

| Trong phạm vi | Ngoài phạm vi (ghi rõ trên slide "Lộ trình") |
|---|---|
| 1 shop, 1 sàn, nhập file CSV/JSON + phát lại live từ thư mục | Kết nối Shopee Open Platform API thật |
| Ghép SKU 3 nguồn, kiểm chất lượng, bỏ dữ liệu cá nhân | Đa sàn, đa shop, phân tách tenant thật |
| Dự báo theo giờ có khoảng p10–p90 + backtest + nhãn tin cậy | Model deep learning, foundation model chuỗi thời gian (để sau, cắm vào registry) |
| Ước lượng iROAS từ lịch sử bật/tắt ads | Thử nghiệm switchback thật trên tài khoản (chỉ thiết kế) |
| Mô phỏng 5 hành động × 4 mục tiêu, có ràng buộc | Tối ưu giá thầu/target ROAS tự động, đổi giá |
| Lớp AI: hiểu câu hỏi, gọi công cụ, kiểm số, giải thích | AI tự thao tác ads |
| Duyệt + nhật ký quyết định + mô phỏng thao tác (có nhãn) | Gửi lệnh thật lên sàn |
| Chấm điểm tự động bằng `ground_truth/` | Billing, đăng nhập SSO |

## 3. Nguyên tắc thiết kế (áp dụng cho mọi module)

1. **AI không bịa số.** LLM không bao giờ tự tạo con số. Mọi số trong câu trả lời phải đến từ một lần gọi công cụ, và qua bước kiểm số (grounding) trước khi hiện lên ([07](07-lop-ai.md)).
2. **Số có nguồn và có nhãn.** Mỗi con số trên UI mang theo: nguồn, mốc dữ liệu (`as_of`), phiên bản model/tham số, nhãn tin cậy `verified`/`reference`/`scenario`.
3. **Trung thực về độ bất định.** Luôn trả khoảng p10–p90. Khi khoảng nằm cả hai phía ngưỡng thì kết luận là "chưa phân định", không ép ra một đáp án.
4. **Người duyệt mọi hành động.** App đề xuất. Người vận hành chọn, lead duyệt khi cần. Thao tác trên sàn ở bản này là **mô phỏng có nhãn**.
5. **Không âm thầm giữ kết quả cũ.** Dữ liệu cũ hoặc lỗi thì hạ kết quả xuống `scenario` và hiện rõ dữ liệu đúng tới lúc nào.
6. **Tôn trọng ràng buộc thật:** cấu trúc chiến dịch (không tắt riêng một SKU trong chiến dịch nhiều SKU), cam kết chương trình sale, giới hạn tồn, ngân sách dùng chung.
7. **Mô-đun thay được.** Mỗi lớp giao tiếp qua hợp đồng (pydantic model) cố định. Đổi model dự báo hay thêm nguồn dữ liệu không phải sửa lớp khác.
8. **Dữ liệu người dùng là dữ liệu, không phải lệnh.** Ghi chú, tên sản phẩm, nội dung file đều có thể chứa câu dụ AI. Không bao giờ thực thi chúng.

## 4. Luồng người dùng chính

```
[Onboarding]  Tải 6 file lịch sử → app ghép SKU, báo lỗi dữ liệu → người dùng xác nhận 5–20 mã
      │       → app chạy backtest → hiện "nếu dùng app ở 10.10, đề xuất sẽ là…"
      ▼
[Đặt mục tiêu] Chọn mục tiêu (lợi nhuận / bán đủ N / xả hàng / hiệu quả), hạn, giá trị hàng dư
      ▼
[Trong ca]    Dữ liệu mới về mỗi giờ → điều phối tính lại top SKU ưu tiên → cảnh báo có lý do
      │       → mở chiến dịch: thanh vùng quyết định (tầng 1) → lý do + điều làm đổi (tầng 2)
      │       → dữ liệu, model, phiên bản (tầng 3) → hỏi AI what-if bằng tiếng Việt
      ▼
[Quyết định]  Chọn hành động → (lead duyệt) → mô phỏng thao tác → ghi nhật ký
      ▼
[Sau ca]      So đề xuất với kết quả thật → cập nhật phiếu điểm, iROAS → ghi chú bàn giao ca
```

## 5. Module ↔ 6 tiêu chí

| Tiêu chí | Module chính | File plan | Đo bằng |
|---|---|---|---|
| 1. Chính xác & phản ánh thực tế | `forecast` (backtest, nhãn), `ads_effect` (iROAS), `governance` (shadow mode) | 03, 04, 11 | WAPE, coverage, sai số iROAS so với `ground_truth` |
| 2. Linh hoạt & tham số hóa | `economics` (parameters, goals), `simulator` (5 hành động) | 05 | Đổi tham số → kết quả tính lại < 300 ms |
| 3. Kết nối & tương thích dữ liệu | `ingest`, `warehouse`, `panel` | 02 | % ghép SKU đúng, % lỗi trong manifest bắt được |
| 4. Mô-đun hóa & tiến hóa | `forecast.registry`, adapter, hợp đồng pydantic, `governance.versions` | 01, 03, 10 | Thêm model mới không sửa file ngoài `forecast/models/` |
| 5. Tốc độ & hiệu năng | `orchestrator` (ưu tiên, cache), simulator vector hóa | 05, 06 | < 5 phút cho top 50 SKU sau khi có dữ liệu mới; 1 SKU < 300 ms |
| 6. Minh bạch & dễ hiểu | `web` (3 tầng, thanh vùng), `ai` (chip nguồn) | 07, 09 | Test usability; mọi số có chip nguồn |

## 6. Cách đọc bộ plan

| File | Nội dung | Ai đọc kỹ |
|---|---|---|
| 00-tong-quan.md | File này | Tất cả |
| [01-kien-truc-va-cong-nghe.md](01-kien-truc-va-cong-nghe.md) | Kiến trúc, stack, thư mục, cài đặt, quy ước | Tất cả |
| [02-lop-du-lieu.md](02-lop-du-lieu.md) | Adapter, ghép SKU, DDL, `hourly_panel`, kiểm chất lượng | D1, D2 |
| [03-lop-mo-hinh.md](03-lop-mo-hinh.md) | Dự báo, phân nhóm, backtest, nhãn tin cậy, drift | D2 |
| [04-hieu-qua-ads.md](04-hieu-qua-ads.md) | Ước lượng iROAS, bẫy gây nhiễu, switchback | D2 |
| [05-kinh-te-va-mo-phong.md](05-kinh-te-va-mo-phong.md) | Tham số, mục tiêu, mô phỏng, ngưỡng hòa vốn | D2, D3 |
| [06-dieu-phoi-va-hieu-nang.md](06-dieu-phoi-va-hieu-nang.md) | Điều phối tính lại, ưu tiên SKU, cache, live | D3 |
| [07-lop-ai.md](07-lop-ai.md) | Công cụ cho agent, grounding, chống injection | D3 |
| [08-api.md](08-api.md) | Endpoint `/api/v1`, SSE | D3, D4 |
| [09-giao-dien.md](09-giao-dien.md) | Màn hình, thanh vùng, 3 tầng | D4 |
| [10-quan-tri-va-bao-mat.md](10-quan-tri-va-bao-mat.md) | Duyệt, nhật ký, phiên bản, phân quyền, dữ liệu cá nhân | D3 |
| [11-kiem-thu-va-cham-diem.md](11-kiem-thu-va-cham-diem.md) | Test, chấm điểm bằng `ground_truth`, eval AI | Tất cả |
| [12-lo-trinh-va-phan-viec.md](12-lo-trinh-va-phan-viec.md) | Mốc M0–M5, phân việc D1–D4 | Tất cả |
| [13-rui-ro.md](13-rui-ro.md) | Rủi ro và phương án dự phòng | Tất cả |

Quy ước chung: mã task dạng `ING-01`, `FC-03`; người phụ trách D1 Data, D2 ML, D3 Backend + AI, D4 Frontend. Ước lượng tính bằng người-ngày (nd).

## 7. Sáu nhân vật demo (dùng làm test case xuyên suốt)

| SKU | Câu chuyện | Module được thử |
|---|---|---|
| `BINHNUOC500-*` | Bán đều, viral từ trưa 11.11 (nhu cầu ~20× ngày thường) | Drift CUSUM, cảnh báo nhu cầu vượt tồn |
| `DENLEDTET-*` | Hàng Tết, tồn rất nhiều | Mục tiêu `clear_stock`, giá trị hàng dư |
| `NOICHIEN2L` + `GIAYLOT50` + `COMBO-NOICHIEN` | Ads hiệu quả cao, tồn đầu 11.11 ≈ 80–130, hết buổi chiều, hàng về 12/11; giá 11.11 làm biên lợi nhuận rất mỏng | Combo, ngân sách chung, cam kết flash sale, `schedule_hours` |
| `COCMEME-*` | Trend nguội, tồn nhiều, ads lỗ | iROAS < ngưỡng hòa vốn |
| `AOGIUNHIET-*` | Size M hết hàng 11.11 khi ads vẫn chạy | Agent điều tra, cờ hết hàng |
| `MAYHUTMINI` | Mới 7 ngày | Nhãn `scenario`, model mượn SKU tương tự |

## Tiêu chí hoàn thành

- [ ] Cả team đã đọc 00, 01 và file của mình, thống nhất tên bảng, nhãn, hành động, mục tiêu như trên.
- [ ] Mỗi nhân vật demo có ít nhất 1 test end-to-end trong `backend/tests/e2e/`.
- [ ] Bảng module ↔ tiêu chí có số đo thật (điền ở M5) để đưa vào slide.

## Task gợi ý

| Mã | Việc | Người | nd |
|---|---|---|---|
| GEN-01 | Kick-off: đọc plan, chốt tên bảng/enum, tạo board task | Tất cả | 0,5 |
| GEN-02 | Viết `docs/glossary.md` (thuật ngữ Việt–Anh dùng trong code và UI) | D3 | 0,25 |
| GEN-03 | Chuẩn bị 6 kịch bản e2e theo nhân vật demo (input, kỳ vọng) | D2 | 0,5 |
