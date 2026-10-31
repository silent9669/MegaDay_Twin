# Plan kỹ thuật MegaDay Twin

Bộ plan để team dev xây bản hackathon của MegaDay Twin: một web app giúp người vận hành shop Shopee quyết định tiếp tục hay tạm dừng quảng cáo theo tồn kho, giá vốn và mục tiêu, với một lớp AI chỉ được nói những con số do hệ thống đã tính và đã kiểm.

Dữ liệu để dev và demo đã có sẵn ở [../../mock-data/](../../mock-data/README.md).

## Đọc theo vai trò

Ai cũng đọc [00 · Tổng quan](00-tong-quan.md) và [12 · Lộ trình và phân việc](12-lo-trinh-va-phan-viec.md) trước. Sau đó:

| Vai trò | Đọc kỹ | Đọc lướt |
|---|---|---|
| D1 · Data | 01, 02 | 03, 06, 11 |
| D2 · ML | 03, 04, 11 | 02, 05 |
| D3 · Backend + AI | 05, 06, 07, 08, 10 | 01, 02, 04 |
| D4 · Frontend | 09, 08 | 05, 07 |
| PM / pitch lead | 00, 12, 13 | 09, 11 mục 5 (kịch bản diễn tập) |

## Mục lục

| File | Nội dung |
|---|---|
| [00 · Tổng quan](00-tong-quan.md) | Bài toán, phạm vi, nguyên tắc thiết kế, luồng người dùng, 6 nhân vật demo |
| [01 · Kiến trúc và công nghệ](01-kien-truc-va-cong-nghe.md) | Kiến trúc 5 lớp + lớp AI, stack, cấu trúc thư mục, cài đặt trên Windows, quy ước code |
| [02 · Lớp dữ liệu](02-lop-du-lieu.md) | Adapter cho 7 file, ghép SKU, kiểm chất lượng, bỏ PII, DDL, bảng `hourly_panel` |
| [03 · Lớp mô hình](03-lop-mo-hinh.md) | Interface dự báo, các model baseline, backtest, phiếu điểm, nhãn tin cậy, phân nhóm nhu cầu |
| [04 · Hiệu quả ads](04-hieu-qua-ads.md) | Ước lượng iROAS, xử lý gây nhiễu, so với ROAS sàn |
| [05 · Kinh tế và mô phỏng](05-kinh-te-va-mo-phong.md) | Tham số, mục tiêu, mô phỏng 5 hành động, ngưỡng hòa vốn, ca nồi chiên 11.11 |
| [06 · Điều phối và hiệu năng](06-dieu-phoi-va-hieu-nang.md) | Event bus, xếp ưu tiên, cache, chế độ live, mục tiêu hiệu năng |
| [07 · Lớp AI](07-lop-ai.md) | Công cụ cho agent, 5 luồng AI, kiểm số (grounding), chống prompt injection, bộ eval |
| [08 · API](08-api.md) | Hợp đồng REST và SSE cho giao diện |
| [09 · Giao diện](09-giao-dien.md) | Màn hình, wireframe, thanh vùng quyết định, trạng thái hiển thị |
| [10 · Quản trị và bảo mật](10-quan-tri-va-bao-mat.md) | Vai trò, luồng duyệt quyết định, phiên bản, PII, bí mật |
| [11 · Kiểm thử và chấm điểm](11-kiem-thu-va-cham-diem.md) | Test, chấm điểm tự động bằng `ground_truth`, kịch bản diễn tập demo |
| [12 · Lộ trình và phân việc](12-lo-trinh-va-phan-viec.md) | Mốc M0–M5, bảng task đầy đủ, lịch 10 ngày, đường găng, bản 48 giờ |
| [13 · Rủi ro và câu hỏi mở](13-rui-ro.md) | Rủi ro, dấu hiệu sớm, phương án; các câu cần team chốt |

## Mốc chính

| Mốc | Kết thúc (lịch 10 ngày) | Qua mốc khi |
|---|---|---|
| M0 · Khung dự án | Ngày 1 | Backend + frontend chạy, CI xanh, DuckDB đủ bảng |
| M1 · Dữ liệu sạch | Ngày 4 | Nạp đủ 7 file, ghép SKU tự động ≥ 90%, bắt đủ 12 lỗi cài sẵn |
| M2 · Dự báo và chấm điểm | Ngày 6 | Tốt hơn baseline ≥ 15% WAPE trên đợt 10.10, mọi SKU có nhãn tin cậy |
| M3 · Quyết định | Ngày 8 | Mô phỏng < 1 giây; ca nồi chiên 11.11 ra đúng `schedule_hours` |
| M4 · AI và live | Ngày 9 | Phát lại 11.11 chạy, AI điều tra đúng ca size M, kiểm số 100% |
| M5 · Demo | Ngày 10 | Diễn tập đạt 2 lần liên tiếp, có video dự phòng |

Chi tiết tiêu chí từng mốc nằm ở file 12, mục 1.

## Cần biết trước khi bắt đầu

- **Plan đang vượt công suất.** Ước lượng gốc khoảng 72 người-ngày, riêng P0 là 51,5. Đội 4 dev × 10 ngày chỉ có 40. Lịch trong file 12 chạy theo "mức sàn" (42 người-ngày), vẫn vượt khoảng 5%. Team cần chọn: thêm dev thứ 5, kéo dài khoảng 13 ngày, hoặc giữ mức sàn và cắt phần trễ sang bản 48 giờ.
- **File 12 là nguồn chính** về người phụ trách, ưu tiên và ước lượng. Bảng "Task gợi ý" cuối các file 01–11 có thể lệch so với file 12.
- **Code trong `backend/app/` không được đọc `mock-data/out/*/ground_truth/`.** Đáp án chỉ dùng trong `backend/eval/`, và có test chặn rò.
- **Máy hiện tại chưa cài Python.** Cài Python 3.11 và `uv` theo file 01 trước ngày 1.
- **Đo regret khi chạy lại đợt sale cũ cần sửa `mock-data`**, để bộ sinh chạy lại được theo chính sách khác (task EVAL-03, ưu tiên P2).
- **Câu hỏi còn mở** (độ dài hackathon, số người, nơi deploy…) nằm ở file 13. Chốt trong buổi kickoff.
