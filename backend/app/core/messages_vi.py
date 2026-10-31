"""Vietnamese user-facing strings for the backend (01 §7).

Keep every string shown to users here so wording stays consistent and can be
reviewed in one place. Frontend strings live in ``web/src/lib/i18n.ts``.
Use ``str.format`` placeholders for numbers; numbers themselves come from code.
"""

from __future__ import annotations

# ------------------------------------------------------------------ errors (08 §1)
ERR_INTERNAL = "Có lỗi không mong muốn ở máy chủ. Vui lòng thử lại."
ERR_VALIDATION = "Dữ liệu gửi lên không đúng định dạng."
ERR_PARAM_OUT_OF_RANGE = "Giá trị tham số nằm ngoài khoảng cho phép."
ERR_UNAUTHENTICATED = "Thiếu thông tin người dùng (X-User)."
ERR_FORBIDDEN = "Vai trò của bạn không có quyền thực hiện thao tác này."
ERR_NOT_FOUND = "Không tìm thấy dữ liệu yêu cầu."
ERR_STATE_CONFLICT = "Không thể chuyển trạng thái này."
ERR_CONSTRAINT_VIOLATION = "Hành động vi phạm ràng buộc của chương trình sale."
ERR_STALE_INPUT = "Dữ liệu đã thay đổi sau khi tính. Vui lòng tính lại trước."
ERR_BLOCKING_DATA_ISSUE = "Thiếu dữ liệu bắt buộc nên chưa tính được."
ERR_AI_BUDGET_EXCEEDED = "Đã dùng hết hạn mức AI hôm nay."
ERR_AI_UNAVAILABLE = "Trợ lý đang ngoại tuyến."
ERR_NOT_IMPLEMENTED = "Chức năng này chưa được làm."

# ------------------------------------------------------------------ CLI
CLI_MIGRATE_DONE = "Đã migrate DuckDB tại {path}: {n_tables} bảng."
CLI_RESET_DONE = "Đã xóa và tạo lại DuckDB tại {path}."
CLI_RESET_CONFIRM = "Xóa toàn bộ dữ liệu trong {path}?"
CLI_RESET_ABORTED = "Đã hủy."
CLI_SEED_DONE = "Đã nạp dữ liệu khai báo mẫu (combo): {n} dòng."
CLI_NOT_IMPLEMENTED = "Lệnh '{command}' chưa làm (task {task}). Xem {plan}."

# ------------------------------------------------------------------ confidence labels
LABEL_VERIFIED = "Đã kiểm"
LABEL_REFERENCE = "Tham khảo"
LABEL_SCENARIO = "Kịch bản"

# ------------------------------------------------------------------ health
HEALTH_OK = "Hệ thống hoạt động bình thường."
HEALTH_DEGRADED = "Một số thành phần đang lỗi."

# ------------------------------------------------------------------ ingest (CLI summary)
INGEST_DONE = (
    "Đã nạp {loaded}/{total} file từ {path} trong {seconds} giây "
    "(bỏ qua {skipped} file đã nạp trước đó)."
)
INGEST_NOTHING_NEW = (
    "Không có file mới trong {path}: mọi file đã được nạp (dùng --force để nạp lại)."
)
INGEST_FILE_LINE = "  {name:34s} {source:13s} {status:15s} {rows:>9s} dòng  {ms:>7s} ms{extra}"
INGEST_FILE_HELD = "  · {n} dòng chờ xác nhận SKU"
INGEST_TABLES = "Số dòng mỗi bảng:"
INGEST_ALIASES = (
    "Ghép SKU: {auto} tự động, {confirmed} đã xác nhận, {pending} chờ xác nhận ({detail})."
)
INGEST_ISSUES = "Vấn đề dữ liệu theo mã:"
INGEST_NO_ISSUES = "Không có vấn đề dữ liệu nào."
INGEST_PANEL = "hourly_panel: ghi {rows} dòng cho {skus} SKU trong {ms} ms."
INGEST_TIMINGS = "Thời gian từng bước (ms): {detail}"
LIVE_UNCHANGED = "Dữ liệu live trong {path} chưa đổi so với lần nạp trước."
LIVE_EMPTY = "Không có dòng live mới trong {path}."
LIVE_DONE = "Đã nạp phần live mới trong {seconds} giây: {detail}{restart}."
LIVE_RESTARTED = " (phát lại chạy lại từ đầu: đã xóa dữ liệu live cũ)"
LIVE_TICK = "Dữ liệu tới: đơn hàng và tồn {orders}, ads {ads}."
PANEL_REBUILT = "Đã dựng lại hourly_panel: {rows} dòng, {skus} SKU, {ms} ms."
EVAL_RUNNING = "Chạy chấm điểm: {cmd}"
BACKTEST_DONE = (
    "Backtest xong: {scores} dòng điểm, {champions} champion (nhóm × bối cảnh), {s:.1f} giây."
)
FIT_DONE = "Dự báo xong {n} SKU ({horizon} giờ tới). Nhãn tin cậy: {labels}."

# ------------------------------------------------------------------ ads effect (iROAS, 04)
ADS_DONE = (
    "Đã ước lượng iROAS cho {n} chiến dịch trong {s:.1f} giây (δ = {delta}, nhịp giờ từ "
    "{rhythm}). Nhãn tin cậy: {labels}."
)
ADS_LINE = (
    "  {cid:7s} ROAS sàn {roas:>6s} · iROAS {mean:>5s} [{p10}–{p90}] · biên {marg:>5s}"
    " · hòa vốn {be:>5s} · {label}"
)
ADS_NONE = "Không có chiến dịch nào có dữ liệu panel để ước lượng iROAS."
ADS_RHYTHM_NO_ADS = "SKU không chạy ads"
ADS_RHYTHM_ALL = "mọi SKU (shop có quá ít SKU không ads)"
ADS_REASON_DELTA = "Ads còn tác dụng sau khi tắt: δ = {delta} mỗi giờ, dùng chung cho cả shop"
ADS_REASON_EXCLUDED = "{n} giờ {what} đã bị loại khỏi ước lượng"
ADS_EXCLUDED_WHAT = {
    "unknown": "thiếu số liệu ads",
    "launch": "trong 3 ngày đầu chiến dịch hoặc SKU mới ra mắt",
    "stock_pause": "tạm dừng ads vì sắp hết hàng",
    "low_stock": "SKU chính hết hàng hoặc tồn thấp",
}
ADS_REASON_OFF_HOURS = "Còn {n} giờ không chi ads sạch để so sánh"
ADS_REASON_FEW_OFF = "Chỉ còn {n} giờ không chi ads sạch: kết quả gần như chỉ là giả định ban đầu"
ADS_REASON_WIDE = "Khoảng p10–p90 rộng ({width:.0%} so với trung bình)"
ADS_REASON_CHECK_DISAGREE = (
    "So sánh thô giờ hết ngân sách (tăng {crude:.0%}) lệch nhiều so với mô hình ({model:.0%})"
)
ADS_REASON_SKU_SPLIT = "Hiệu quả ads ước lượng theo chiến dịch, chia cho SKU theo tỷ trọng bán"
ADS_REASON_SHORT_TERM = (
    "iROAS ngắn hạn: chưa tính phần thứ hạng tự nhiên tăng nhờ ads qua nhiều ngày"
)
ADS_REASON_PRIOR = "Chưa có dữ liệu ads của chiến dịch: dùng giả định ban đầu"
ADS_REASON_RHYTHM_ALL = "Nhịp giờ lấy từ mọi SKU vì shop có quá ít SKU không chạy ads"
