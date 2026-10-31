## Làm gì

<!-- Mã task (vd ING-03) và một hai câu mô tả. Một PR thường gói trọn một mốc (M0, M1, ...). -->

## Đã kiểm tra

- [ ] CI xanh (job `ci`)
- [ ] Đã chạy với dữ liệu `mock-data/out/demo` (không chỉ `nho`)
- [ ] Nếu có chấm điểm: dán kết quả từ `backend/eval/` (ghép SKU %, lỗi bắt được, sai số...)
- [ ] Ảnh chụp màn hình nếu đổi giao diện

## Quyết định và hợp đồng

- [ ] Quyết định tự chốt đã ghi vào `docs/quyet-dinh/`
- [ ] Nếu đổi hợp đồng (schema DB, API, kiểu dữ liệu, sự kiện): đã sửa `docs/dev-plan/` trong cùng PR

## Nguyên tắc bắt buộc

- [ ] Code trong `backend/app/` không đọc `ground_truth`
- [ ] Không commit `.env`, API key
- [ ] Số do AI nói ra đều lấy từ công cụ (nếu PR đụng lớp AI)

## Còn lại

<!-- Việc chưa làm, rủi ro, câu hỏi cho người review. -->
