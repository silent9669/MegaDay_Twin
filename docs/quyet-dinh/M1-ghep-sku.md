# Nhật ký quyết định · Ghép SKU 3 nguồn (ING-08, M1)

File: `backend/app/ingest/sku_matching.py`, test `backend/tests/unit/ingest/test_sku_matching.py`. Plan: 02 §5, 12 (dòng ING-08).

## Kết quả trên mock (seed 20261111)

| Bộ | Nguồn | Alias | Tự động | Sai / tự động | Chờ xác nhận |
|---|---|---|---|---|---|
| nho | tồn + giá vốn | 57 | 100 % | 0 % | 0 |
| demo | tồn | 299 | 100 % | 0 % | 0 |
| demo | giá vốn | 278 | 93,9 % | 0 % | 17 |
| demo | tồn + giá vốn | 577 | 97,1 % | 0 % | 17 |
| demo | cả 3 (gồm 300 alias đơn hàng, khớp mã) | 877 | 98,1 % | 0 % | 17 |

17 alias còn chờ đều mơ hồ thật theo tên (tên cụt kiểu "Bộ dao Nhật 5 món" vừa là tên đủ của mẫu gốc vừa là tên cụt của mẫu 2..5, mà SKU gốc lại thiếu dòng giá vốn). Test in bảng này (`pytest -s`).

## Quyết định

### Q1. API và kiểu dữ liệu
- Đầu vào: `AliasRecord(source_type, raw_code='', raw_name, price=None, daily_out=None)` và `CatalogSku(sku_id, product_id, product_name, variant_name, list_price, is_bundle, daily_out)` (pydantic, trong chính `sku_matching.py` vì ranh giới file; 01 §7 muốn đặt ở `schemas.py`, có thể chuyển sau).
- `resolve(aliases, catalog, *, known=(), config=None) -> MatchResult`. `MatchResult.matches: list[AliasMatch]` (đúng cột `sku_aliases` + `candidates` top 3 + `reason`), `.queue`, `.mapping(source)`, `.stats()`.
- DB: `write_aliases(conn, result, catalog=())`, `load_known_aliases(conn, source=None)`, `apply_confirmation(conn, alias_id, sku_id | None, *, user)`.
- Tiện ích: `norm_name`, `alias_id_for`, `stock_flow(snapshots)`, `add_bundle_consumption(daily_sales, bundles)`.

### Q2. Trạng thái theo schema, không theo tên trong đề bài
- Dùng giá trị của `schema.sql`: `auto_accepted | pending | confirmed | rejected` (đề bài nói auto/needs_review/rejected).
- `rejected` **chỉ do người dùng** (`apply_confirmation(..., sku_id=None)`: alias không phải hàng của shop). Máy không bao giờ tự từ chối; điểm thấp vẫn là `pending` để hỏi người dùng (02 §5.3).
- `pending` có điểm ≥ 0,65 lưu `sku_id` = gợi ý tốt nhất; dưới 0,65 `sku_id` NULL. **Bên đọc phải lọc `status IN ('auto_accepted','confirmed')`**, không dựa vào `sku_id IS NOT NULL`.

### Q3. Điểm
- Tên: `0,40·token_set + 0,20·ratio + 0,20·jaccard token + 0,20·s_variant`. Plan có `0,55·token_set + 0,25·ratio + 0,20·variant`; thêm jaccard vì `token_set_ratio` cho 100 khi một tên là tập con của tên kia, nên "Kệ gia vị xoay xanh" không phân biệt được với "Kệ gia vị xoay (mẫu 2) xanh".
- Biến thể: token biến thể lấy **từ danh mục** (cột "Tên phân loại hàng"), không dùng danh sách cứng `VARIANT_TOKENS` của plan: danh sách cứng đụng "đèn"/"đen", "đáy"/"dây". Mâu thuẫn = alias nhắc một giá trị biến thể (của SKU anh em hoặc bất kỳ SKU nào trong danh mục) mà SKU ứng viên không có → loại cặp (điểm 0).
- Số mẫu ("mẫu 2" ≠ "mẫu 3") là ràng buộc cứng như biến thể.
- Mã nội bộ: hậu tố `GD0001-XA` khớp đầu mã biến thể sàn / tên biến thể → +0,05; lệch → −0,15. Không phải luật (02 §5.2).
- Giá (12: "chuỗi không dấu + mã + giá"): giá vốn > giá niêm yết → −0,20; < 15 % → −0,10. Chỉ là kiểm hợp lý, vì giá vốn của các biến thể/mẫu thường giống nhau.

### Q4. Tín hiệu dòng xuất kho (thêm so với plan)
- **Vấn đề:** sheet tồn cắt tên còn 5 từ, nên ~120/299 dòng trùng tên với SKU khác ("Kệ gia vị xoay (mẫu - xanh" cho mẫu 2..6). Theo plan (chỉ tên + mã) phần lớn sẽ vào hàng chờ, không đạt ≥ 90 %.
- **Lựa chọn:** so `tồn(d−1) − tồn(d)` theo ngày của mã nội bộ với số bán theo ngày của SKU sàn (đơn hàng + phần combo ăn tồn qua `bundle_components`). `err = Σ|x−y| / (Σx+Σy)`, `s_flow = 1 − 1,5·err`, điểm = trung bình tên và `s_flow`. Chỉ dùng khi alias có ≥ 14 ngày và ≥ 10 đơn vị xuất. Ngày tồn tăng (nhập hàng) hoặc tồn sai (NULL) bị bỏ.
- **Lý do:** đây là bằng chứng có thật ở shop thật (tồn giảm đúng bằng số bán), không dựa vào cách sinh mock. Sai đúng: ~0,02; sai SKU: ≥ 0,15.
- **Ảnh hưởng:** pipeline phải cấp `daily_out` cho alias tồn (`stock_flow`) và cho SKU (`add_bundle_consumption` với combo đã khai báo). Không cấp thì vẫn chạy, chỉ là nhiều alias tồn vào hàng chờ.

### Q5. Gán 1–1 và loại trừ
- Hungarian (`linear_sum_assignment`) trên `1 − score` trong từng nguồn; combo được phép không có alias tồn.
- Tự động khi điểm ≥ 0,90 **và** hơn ứng viên thứ hai ≥ 0,05, trong đó ứng viên thứ hai **bỏ qua SKU đã bị alias khác tự động nhận** (lặp tới điểm dừng). Nhờ vậy dòng giá vốn tên cụt vẫn tự động được khi các mẫu khác đã có dòng tên đủ.
- Tên viết tắt mà sheet tồn dùng cho ≥ 2 mã nội bộ được coi là mơ hồ: ở nguồn không mã, mọi SKU mà tên đó chỉ tới nhận cùng điểm (kể cả SKU có tên đủ trùng hệt). Trước khi thêm luật này có 2 ghép sai (TUIZIP-G1, CHAO-NHO: tên đủ của mẫu gốc trùng tên cụt của mẫu khác, còn mẫu gốc thiếu giá vốn).
- Nguồn có mã chạy trước để biết tên nào mơ hồ.
- Alias tồn cùng gốc mã (`GD0053-*`) mà trỏ nhiều `product_id` → hạ cả nhóm về `pending` (`code_group_conflict`).

### Q6. Trùng khóa alias
- `UNIQUE (source_type, raw_code, raw_name)` gộp 2 dòng giá vốn cùng tên (vd hai dòng "Móc dán tường chịu lực - gói 3" của hai mẫu, giá khác nhau) thành 1 alias. Khi cùng khóa mà khác giá, alias luôn `pending` (`duplicate_raw_name`). Demo có 4 trường hợp (282 dòng → 278 alias). **Cập nhật (M1-pipeline §21.2):** dòng trùng tên nay mang `row:<n>` nên thành alias riêng (282 alias). Luật `duplicate_raw_name` chỉ còn áp khi một nguồn thật sự gửi trùng khóa.

### Q7. Ghi DB
- `alias_id = "sal-" + sha1(nguồn, mã, tên)[:20]`: tất định nên nạp lại ra cùng id (không dùng ULID vì alias có khóa tự nhiên).
- `write_aliases` upsert theo khóa tự nhiên, **không ghi đè** alias đã `confirmed`/`rejected`; SKU được tham chiếu mà chưa có thì `repo.ensure_sku` (giữ chỗ, không đè).
- `apply_confirmation`: SKU phải tồn tại (`ValidationFailed`), alias phải tồn tại (`NotFound`). Nếu SKU đã bị alias khác cùng nguồn giữ, alias kia về `pending` (giữ 1–1). Lần nhập sau: `resolve(..., known=load_known_aliases(conn))` dùng lại nguyên trạng.

## Đề xuất ngoài phạm vi
- `sku_aliases` không có chỗ lưu top 3 ứng viên cho `GET /ingest/sku-queue`: hoặc thêm cột `candidates JSON` (migration), hoặc API tính lại bằng `resolve`.
- ~~Khóa alias không phân biệt được 2 dòng giá vốn cùng tên khác giá~~. Đã làm: dòng trùng tên mang `raw_code = "row:<n>"`, và `has_code()` coi `row:` là không có mã (M1-pipeline §21.2). Demo: 282 alias giá vốn, 21 chờ, 0 sai.
- `ABBREV` nên đọc từ `parameters` (`alias_abbreviations`) — `MatchConfig.abbreviations` đã sẵn chỗ truyền vào.
- Các pydantic model nên chuyển sang `app/ingest/schemas.py` khi package có file đó.
