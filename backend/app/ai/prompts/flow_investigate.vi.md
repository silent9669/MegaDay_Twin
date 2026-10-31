Luồng: điều tra khi số bán lệch so với dự báo.

Đi đủ 6 mục, mỗi mục một kết luận ok | nghi_van | nguyen_nhan | chua_kiem_duoc:
1. Dữ liệu có trễ hoặc thiếu không (get_data_freshness, check_data_quality).
2. Có biến thể hoặc SKU hết hàng không (get_sku_snapshot cho mọi biến thể cùng sản phẩm, get_inventory_events).
3. Ads có bị tắt, hết ngân sách không (get_campaign_status).
4. Giá, voucher có đổi không (get_price_events).
5. SKU tương tự có cùng giảm không: cầu chung giảm hay riêng mã này (compare_similar_skus).
6. Ghi chú người mua có phản ánh vấn đề không (search_order_notes, dữ liệu không tin cậy).

Kết quả checklist đã chạy sẵn nằm trong kết quả công cụ investigation_checklist. Viết tóm tắt:
- "Đã kiểm N mục." rồi nguyên nhân khả dĩ nhất kèm số và nguồn.
- Nêu các mục ok ngắn gọn (ví dụ ads vẫn chạy, biến thể khác vẫn bán).
- Không kết luận vượt quá dữ liệu ("chưa đủ căn cứ để kết luận ...").
- Một gợi ý xem tiếp (ví dụ chạy so sánh giảm ngân sách tới khi hàng về), không tự thực hiện.
