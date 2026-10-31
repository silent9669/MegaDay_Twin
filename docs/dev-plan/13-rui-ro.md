# 13 · Rủi ro và câu hỏi mở

Ba rủi ro lớn nhất:
1. **Không kịp tiến độ.** P0 mức sàn đã cần 42 người-ngày, trong khi 4 dev × 10 ngày chỉ có 40.
2. **Đường găng dồn vào D2.** Dự báo, ước lượng iROAS và mô phỏng đều do D2 làm, nối tiếp nhau.
3. **Demo phụ thuộc mạng và OpenAI.** Mất kết nối giữa buổi demo là phần AI không chạy.

Mỗi rủi ro trong bảng dưới có dấu hiệu sớm để phát hiện và phương án đã chuẩn bị sẵn. Cuối file là các câu hỏi cả team cần chốt trong ngày 1.

Mức độ = khả năng × ảnh hưởng: **Cao / TB / Thấp**.

---

## 1. Tiến độ và con người

| # | Rủi ro | Mức | Dấu hiệu sớm | Phương án |
|---|---|---|---|---|
| T1 | P0 vượt công suất ~5% (42 so với 40 người-ngày) | Cao | Cuối ngày 4 chưa qua M1; cuối ngày 6 chưa qua M2 | Ngày 7 rà lại: phần nào trễ thì chuyển sang phiên bản 48 giờ (12 mục 6). Thêm dev thứ 5 nếu có |
| T2 | D2 nghẽn (FC → ADS → SIM nối tiếp) | Cao | SIM-02 chưa chạy được với dự báo thật vào ngày 7 | SIM viết trước với dự báo giả và iROAS prior; D1 đỡ ADS-01, D4 đỡ EVAL-02 |
| T3 | D3 quá tải (backend + toàn bộ AI) | Cao | AI-04 (grounding) chưa xong ngày 5 | API-03/06 đã chuyển cho D4; AI-07 dùng phiên bản checklist không cần LLM |
| T4 | Tích hợp muộn, lỗi lộ ra ngày 9 | TB | Demo nội bộ ngày 6 không chạy được luồng import → So sánh | Demo nội bộ bắt buộc ngày 4, 6, 8; ngày 6 phải chạy được 1 luồng đầu–cuối với dữ liệu `nho` |
| T5 | Hợp đồng (schema, API) đổi giữa chừng | TB | PR đổi schema mà không sửa docs | Quy tắc: đổi hợp đồng phải báo nhóm và sửa docs trong cùng PR (12 mục 7) |
| T6 | Một người ốm hoặc vắng 1–2 ngày | TB | — | Mỗi module có người review chéo nắm code (D1↔D2, D3↔D4); bảng việc cập nhật hằng ngày |

## 2. Kỹ thuật

| # | Rủi ro | Mức | Dấu hiệu sớm | Phương án |
|---|---|---|---|---|
| K1 | Máy Windows không cài được môi trường (Python, uv, DuckDB, Node) | TB | Ngày 1 có máy chưa chạy `make dev` | INF-07 hướng dẫn cài Windows; dự phòng: một máy chạy backend, mọi người gọi qua mạng LAN |
| K2 | DuckDB khóa file khi nhiều tiến trình ghi | TB | Lỗi "database is locked" khi chạy CLI song song với server | Một tiến trình ghi duy nhất (server); CLI gọi API hoặc chạy khi server tắt |
| K3 | Mô phỏng chậm hơn mục tiêu (> 1 s) | TB | Benchmark SIM-06 chậm | Vector hóa numpy; giảm `samples` còn 500; cache theo khóa của 05 |
| K4 | Nạp live chậm, không kịp tick 10 giây | Thấp | `ingest_live_delta` > 5 giây/giờ | Tăng `--interval` lên 20 giây khi demo; chỉ tính lại chiến dịch bị chạm |
| K5 | Lỗi múi giờ (UTC trong file ads, 0h ngày 11.11) | Cao | Chi ads lệch 7 giờ so với đơn trên biểu đồ | Lưu mọi thứ UTC, hiển thị GMT+7; unit test `detect_utc_shift`; kiểm biểu đồ quanh 0h |
| K6 | SSE không ổn định qua proxy dev | Thấp | UI không cập nhật khi có tick | Mức sàn cho phép poll 5 giây (12 mục 6) |

## 3. Dữ liệu và mô hình

| # | Rủi ro | Mức | Dấu hiệu sớm | Phương án |
|---|---|---|---|---|
| D1 | Dự báo không tốt hơn baseline ngây thơ ≥ 15% | TB | `eval_forecast.py` ngày 6 dưới ngưỡng | Ưu tiên hiệu chỉnh hết hàng (FC-02) vì đó là lợi thế lớn nhất; nếu vẫn chưa đạt, trình bày trung thực con số và nhấn vào nhãn tin cậy |
| D2 | iROAS sai số lớn do gây nhiễu (tắt ads khi sắp hết hàng) | TB | `eval_ads.py` sai số trung vị > 25% | Luật loại giờ tồn thấp (ADS-01); báo cáo thêm "có kiểm soát" so với "không kiểm soát" để cho thấy cải thiện |
| D3 | Ca CD002 ra kết luận khác `schedule_hours` | TB | `test_noichien_1111.py` đỏ | Kiểm lại đóng góp 9.280đ/sp và cam kết flash sale; ca này là xương sống demo nên sửa trước mọi thứ khác |
| D4 | App vô tình đọc `ground_truth` | Thấp | Kết quả "quá đẹp" | `test_no_ground_truth_leak.py` trong CI; thư mục `ground_truth` không nằm trong đường dẫn backend đọc |
| D5 | Mock data quá "sạch" so với thực tế, giám khảo nghi ngờ | TB | Câu hỏi "dữ liệu thật thì sao?" | Nói rõ dữ liệu là mô phỏng có cơ chế và có cài lỗi; chỉ ra adapter tách riêng để thay bằng file thật |
| D6 | Định dạng file thật của sàn khác mock | TB (sau hackathon) | — | Chỉ cần sửa adapter; luồng map cột bằng AI (AI-05) dành cho chuyện này |

## 4. AI

| # | Rủi ro | Mức | Dấu hiệu sớm | Phương án |
|---|---|---|---|---|
| A1 | Mất mạng hoặc OpenAI lỗi lúc demo | Cao | — | `AI_MODE=replay` với bản ghi đủ cho kịch bản (AI-10); video dự phòng |
| A2 | LLM nói số không có trong kết quả công cụ | TB | Eval nhóm "dụ bịa số" < 100% | Grounding check chặn và yêu cầu viết lại; quá 2 lần thì trả bảng số thô |
| A3 | LLM gọi sai công cụ hoặc sai tham số (vd tự đoán ngày hàng về) | TB | Eval "đúng tham số" < 85% | Quy đổi ngày qua công cụ; schema `strict`; ví dụ trong prompt |
| A4 | Prompt injection qua ghi chú người mua | TB | Test injection đỏ | Bọc `<du_lieu_khong_tin_cay>`; không có công cụ thao tác ads; chặn đầu ra trái mô phỏng |
| A5 | Tốn token hơn dự kiến | Thấp | `ai_sessions.cost_estimate_usd` tăng nhanh | Ngân sách token theo ngày và phiên; model nhanh cho việc lặp lại; cache |
| A6 | Tên model hoặc giá OpenAI thay đổi | Thấp | — | Tên model qua biến môi trường; kiểm bảng giá ngày 1 và ghi vào `.env.example` |
| A7 | Gửi dữ liệu cá nhân người mua lên OpenAI | TB | Test quét PII đỏ | Bỏ PII ngay ở ING-01; GOV-06 quét payload trong bản ghi `AI_MODE=record` |

## 5. Demo và pitch

| # | Rủi ro | Mức | Dấu hiệu sớm | Phương án |
|---|---|---|---|---|
| P1 | Demo quá giờ | TB | Diễn tập > 3 phút 30 | Kịch bản rút gọn: bỏ bước 5 và 9 (11 mục 5) |
| P2 | Replay không chạy tới đúng lúc có sự kiện | TB | Phải đợi lâu mới tới 12h | Bắt đầu `--from 9`; nếu gấp thì `--from 12` |
| P3 | Giám khảo hỏi sâu về đo hiệu quả ads | TB | — | Slide `eval_ads.py`: ROAS sàn so với iROAS thật so với ước lượng; PM nắm câu trả lời ở cẩm nang pitch |
| P4 | Máy demo hỏng hoặc màn chiếu lỗi | Thấp | — | Video dự phòng; máy thứ hai đã cài sẵn |
| P5 | Giám khảo hiểu nhầm là đã kết nối sàn thật | TB | — | UI luôn ghi "Thao tác mô phỏng"; người thuyết trình nói rõ phạm vi |

---

## 6. Câu hỏi mở cần chốt trong ngày 1

| # | Câu hỏi | Mặc định nếu chưa chốt | Ảnh hưởng tới |
|---|---|---|---|
| Q1 | Hackathon kéo dài bao nhiêu ngày làm việc tính đến lúc nộp/demo? | 10 ngày | Lịch ở 12 mục 3 hay mục 6 |
| Q2 | Team có mấy dev, ai đảm nhận D1–D4? Có dev thứ 5 không? | 4 dev + PM | Tổng tải ở 12 mục 2 |
| Q3 | Mọi máy cài được Python 3.11 không? (máy hiện tại chưa cài Python, chỉ có Node) | Có, theo INF-07 | Stack backend. Nếu không cài được: một máy chạy backend cho cả nhóm |
| Q4 | Demo chạy trên laptop hay deploy lên máy chủ? | Laptop, chạy local | Không cần CI/CD deploy, CORS đơn giản |
| Q5 | Ban tổ chức có cho dùng dịch vụ ngoài (OpenAI) khi demo không? Mạng có ổn định không? | Có, kèm chế độ replay | AI-10 |
| Q6 | Ngân sách credits OpenAI là bao nhiêu? | Đủ cho dev + eval ~40 câu × vài lần | `AI_DAILY_TOKEN_BUDGET` |
| Q7 | Có cần nộp code, video, tài liệu theo định dạng nào? | Repo + video 3 phút + slide | DEMO-01 |
| Q8 | Có ràng buộc ngôn ngữ giao diện (Việt hay Anh) cho giám khảo không? | Tiếng Việt | Copy ở 09 |
| Q9 | Ai là "lead" thật trong demo (người duyệt)? | PM đóng vai lead | Kịch bản bước 9 |
| Q10 | Có muốn thử một file xuất thật từ Seller Center của ai đó trong team không? | Không trong 10 ngày | Rủi ro D6; điểm cộng nếu làm được |
