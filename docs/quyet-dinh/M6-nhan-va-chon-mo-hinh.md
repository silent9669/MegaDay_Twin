# Nhãn nhu cầu chung và lựa chọn mô hình

Warehouse, HQ, Milo và backend dùng `Trendy`, `Seasonal`, `Normal` làm nhãn nhu cầu chính.
`moi`, `ban_thua` và mức thiếu dữ liệu là hồ sơ lịch sử phục vụ dự báo, không ghi đè nhãn AI.
Các mã `demand_group` cũ vẫn được giữ nội bộ để đọc lịch sử, tính hệ số nhóm và đối chiếu
backtest; không đổi nghĩa của các bản mô phỏng đã lưu.

## Đồng bộ và nhận diện

- `Label data` lưu nhãn trong Warehouse rồi thử đồng bộ. Sau khi gửi nguồn dữ liệu trong
  Data Studio, giao diện cũng thử đồng bộ; có thể bấm `Sync labels to forecasting` để thử lại.
- Gateway chỉ đọc snapshot đã lưu, không nhận nhãn hoặc canonical SKU do browser gửi lên.
  Revision, fingerprint và provider phải khớp; kết quả partial được phép gửi các nhãn đã có.
- CSV tồn kho được chuẩn hóa cùng một cách trước khi gửi dữ liệu và tính hash. Cột
  `Demand label` vẫn không được coi như dữ liệu doanh số. Nhãn đi qua API riêng có provenance.
- Backend migration 7 thêm `sku_demand_labels`. Nhãn phải khớp hash bản inventory được ingest
  gần nhất và mã nội bộ đã được ghép duy nhất qua `sku_aliases` (confirmed/auto_accepted).
  Không ghép bằng tên hoặc đoán mã. Mã chưa rõ, nhiều SKU ứng viên hoặc nhiều mã trong một
  lần gửi cùng trỏ một SKU được trả về `pending`.
- Mỗi bản nhãn lưu run/provider, nguyên nhân, điểm hỗ trợ, ngày đánh giá, thời điểm tạo,
  source revision/fingerprint và mã canonical. Retry cùng bản không sinh phiên bản mới.
  Điểm hỗ trợ AI không phải xác suất đúng đã hiệu chuẩn.
- Nhãn được xét chỉ khi đã tồn tại tại mốc dự báo (cả ngày đánh giá và thời điểm hoàn thành).
  Nhãn mới không được dùng để giải thích một dự báo lịch sử. Backend hiện dùng lịch Việt Nam,
  nên từ chối đồng bộ nhãn của thị trường khác. Đổi inventory hoặc alias làm nhãn cũ mất hiệu lực.

## Chính sách chọn mô hình `demand-selection-v1`

1. Giữ override hồ sơ dự báo thủ công nếu có, đồng thời giữ nhãn chính độc lập.
2. Nếu có champion đã kiểm thử trên cùng nhóm lịch sử/bối cảnh, ưu tiên bằng chứng đó;
   nhãn tin cậy vẫn bị giới hạn khi lịch sử ngắn, bất kể model nào thắng.
3. Khi chưa có champion phù hợp: lịch sử ngắn ưu tiên Analog; bán thưa ưu tiên TSB;
   chưa đủ ngày hợp lệ dùng mô hình nền.
4. Nếu chưa có, nhãn chính định hướng mô hình mặc định: Normal → Profile EWMA,
   Trendy → Trend Damped, Seasonal → Seasonal Calendar.
5. Seasonal chỉ dùng lịch mùa được code hỗ trợ và có căn cứ loại mùa. Không tự biến nhãn
   Seasonal chung thành lịch quà tặng; trường hợp chưa rõ quay về Profile EWMA và ghi lý do.
6. Khi có drift và đủ lịch sử, dùng Nowcast; không mang điểm backtest của mô hình cũ sang nó.

Hai tham số có phiên bản trong catalogue hiện có: `forecast_min_history_days` (mặc định 14,
giới hạn 14–90) và `forecast_sparse_adi` (mặc định 1.32, giới hạn 1–7), shop scope, lead chỉnh.
Đây là ngưỡng chọn mô hình; không tự thay các công thức kinh tế, giới hạn ngân sách hoặc
ngưỡng tin cậy. Chưa mở tùy ý tất cả hệ số của sáu mô hình: thay thuật toán vẫn cần kiểm thử
và quản lý phiên bản mô hình.

## Tính nhất quán và giải thích

- Forecast mới lưu nhãn, hồ sơ dữ liệu, model ID, chính sách, ngưỡng và lý do lựa chọn.
  Truy vết mô phỏng lưu nguyên snapshot này, dùng được trong HQ và Milo.
- Cache forecast kiểm tra dấu vết dữ liệu, nhãn có hiệu lực, tham số, danh mục và điểm mô hình.
  Thay nhãn hoặc alias cũng làm bản preview cũ không còn được áp dụng.
- Đồng bộ mới phát `RecomputeRequested` để runtime live tính lại SKU/chiến dịch liên quan.
  Runtime tắt thì lần mô phỏng kế tiếp tự kiểm tra cache; API đồng bộ không thực thi quảng cáo.
- HQ có phần `Demand classification` và `Why this forecast model?`; kết quả mô phỏng có
  `Demand labels and model choices`. Mốc dự báo cũ và nhãn hiện tại được hiển thị riêng.

## Giới hạn cần biết

Nhãn AI hiện chủ yếu từ tên/công dụng khi không có bằng chứng bổ sung; chưa tự thu thập web
hay đưa toàn bộ lịch sử sáu nguồn vào prompt. Chính sách coi đó là prior, không phải chứng minh
nhu cầu. Các mô hình mùa hiện hỗ trợ lịch quà tặng, Tết, mùa đông; chưa phải mọi mùa/ngành hàng.
Kiểm thử kỹ thuật không thay thế đánh giá độ chính xác bằng dữ liệu shop thật và backtest
theo đúng bối cảnh. Backend chưa kết nối thì nhãn được lưu tại Warehouse và báo chưa đồng bộ.
