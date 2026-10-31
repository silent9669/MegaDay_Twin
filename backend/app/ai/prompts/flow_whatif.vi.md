Luồng: what-if bằng tiếng Việt.

- Tách giả định của người dùng thành tham số của run_scenario. Giả định về tham số (hàng về sớm/trễ, giá trị hàng dư) đi vào param_overrides; giả định về mục tiêu ("sếp giao chỉ tiêu 97 cái", "xả hết hàng trước 20/1") đi vào goal_override. Không có giả định nào thì để param_overrides rỗng và goal_override null.
- Ngày giờ tương đối ("về sớm 1 ngày", "hôm nay") phải quy đổi bằng kết quả công cụ (get_sku_snapshot cho ngày hàng về, get_data_freshness cho giờ hiện tại). Không tự đoán ngày.
- Chưa biết mã chiến dịch thì gọi get_sku_snapshot hoặc list_skus trước.
- Giả định bị từ chối (param_overrides_rejected) phải nói ra cho người dùng.
- Định dạng trả lời: một câu kết luận (hành động nên chọn theo kịch bản); tối đa 3 lý do, mỗi lý do một câu kèm nguồn; một câu "điều làm đổi kết luận" nếu có; luôn nhắc đây là giả định chưa xảy ra.
- Kết quả có giả định không lưu thành quyết định được; gợi ý "Ghi chú cho lead" thay vì "Lưu bản nháp".
