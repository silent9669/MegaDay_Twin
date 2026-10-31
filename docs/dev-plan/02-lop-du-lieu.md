# 02 · Lớp dữ liệu

Lớp dữ liệu biến 6 file "bẩn" và thư mục live thành **bảng chuẩn trong DuckDB** và một bảng gốc `hourly_panel` (SKU × giờ). Mọi lớp sau chỉ đọc từ đây. Lớp này phải bắt đủ các lỗi cài sẵn trong `ground_truth/manifest.json` và ghép SKU đúng ≥ 99% ở chế độ tự động.

## 1. Pipeline

```
ingest_files(paths) ──┬─ detect.source_type(header)          # nhận diện file qua header, không qua tên file
                      ├─ adapter.read(path) → RawFrame       # đã bỏ cột PII ngay lúc đọc
                      ├─ adapter.normalize(raw) → NormFrame  # kiểu dữ liệu, UTC, VND int
                      ├─ quality.check(norm) → issues[]      # ghi data_quality_issues
                      ├─ sku_matching.resolve(...)           # sku_aliases + hàng chờ
                      ├─ warehouse.upsert(norm, batch_id)    # idempotent theo khóa tự nhiên
                      └─ panel.build_hourly_panel(range)     # chỉ khoảng thời gian bị ảnh hưởng
```

```python
# app/ingest/adapters/base.py
class RawFrame(BaseModel):
    source_type: SourceType            # orders | ads | inventory | cost | campaigns | sale_program
    df: pd.DataFrame                   # cột gốc, đã bỏ PII
    file_name: str
    file_sha256: str
    dropped_columns: list[str]         # PII đã bỏ (ghi vào batch để chứng minh)

class SourceAdapter(Protocol):
    source_type: SourceType
    header_signature: set[str]         # tập cột bắt buộc để nhận diện
    def read(self, path: Path) -> RawFrame: ...
    def normalize(self, raw: RawFrame, ctx: IngestContext) -> NormFrame: ...
```

`IngestContext` mang `batch_id`, `as_of` (thời điểm dữ liệu đúng tới), `tz_offsets` (độ lệch múi giờ đã phát hiện cho từng nguồn), `sku_resolver`.

**Idempotent:** batch có `file_sha256`. Nạp lại cùng file thì bỏ qua và trả batch cũ. Upsert theo khóa tự nhiên, nên nạp lại file đã sửa sẽ ghi đè đúng dòng.

**Nhận diện file theo header** (đọc dòng đầu, bỏ BOM `﻿`):

| source_type | Cột nhận diện |
|---|---|
| orders | `Mã đơn hàng`, `SKU phân loại hàng`, `Số lượng` |
| ads | `Mã chiến dịch`, `Chi phí`, `Lượt click` |
| inventory | `Mã nội bộ`, `Tồn thực tế` (+ `Ngày` = snapshot ngày, `Thời điểm cập nhật` = snapshot giờ) |
| cost | `tên sp`, `giá vốn` |
| campaigns | JSON mảng, phần tử có `ma_chien_dich`, `sku` |
| sale_program | JSON object có `cam_ket`, `khung_flash_sale` |

Không khớp → `UNKNOWN_SOURCE`, trả về UI để AI "nhập dữ liệu thông minh" gợi ý ánh xạ cột ([07](07-lop-ai.md)).

## 2. Adapter từng file

### 2.1 `don_hang_seller_center.csv` → `orders`, `order_lines`

Header: `Mã đơn hàng, Ngày đặt hàng, Trạng thái đơn hàng, Tên sản phẩm, Tên phân loại hàng, SKU phân loại hàng, Giá gốc, Giá ưu đãi, Số lượng, Tổng tiền hàng, Người mua, Số điện thoại, Địa chỉ nhận hàng, Ghi chú của người mua`

| Cột gốc | Đích | Xử lý |
|---|---|---|
| Mã đơn hàng | `orders.order_id` | Khóa. Dòng lặp y hệt → giữ 1, ghi `DUP_ORDER` |
| Ngày đặt hàng | `orders.ordered_at` | `'YYYY-MM-DD HH:MM'` giờ VN → UTC (trừ 7 giờ) |
| Trạng thái đơn hàng | `orders.status`, `orders.status_final` | Ánh xạ bảng dưới |
| Tên sản phẩm, Tên phân loại hàng | `sku_aliases` (nguồn `orders`) | Dùng cho ghép SKU |
| SKU phân loại hàng | `order_lines.sku_id` | SKU sàn là khóa chuẩn của hệ thống |
| Giá gốc / Giá ưu đãi | `order_lines.list_price` / `unit_price` | int VND |
| Số lượng | `order_lines.qty` | int ≥ 1 |
| Tổng tiền hàng | `order_lines.line_total` | Kiểm `= unit_price × qty`, lệch → `ORDER_TOTAL_MISMATCH` |
| Người mua, Số điện thoại, Địa chỉ nhận hàng | **bỏ ngay khi đọc** | `pd.read_csv(usecols=...)`; ghi tên cột vào `ingest_batches.dropped_columns` |
| Ghi chú của người mua | `orders.buyer_note_masked` | Che số điện thoại/email (regex). Gắn `untrusted=true`. Quét mẫu câu lệnh → `SUSPICIOUS_NOTE` |

Trạng thái:

| Gốc | `status` | `status_final` | Tính vào số bán? |
|---|---|---|---|
| Hoàn thành | completed | true | Có |
| Trả hàng/Hoàn tiền | returned | true | Có (nhu cầu đã xảy ra); trừ khỏi doanh thu ròng |
| Đã hủy | cancelled | true | Không |
| Đang giao | shipping | **false** | Có, nhưng gắn tạm (`PROVISIONAL_STATUS`) |
| Chờ lấy hàng | awaiting_pickup | **false** | Có, tạm. Trong mock, phần lớn là đơn sẽ hủy sau thời điểm xuất |
| Chờ xác nhận (live) | pending | **false** | Có, tạm |

Đơn tạm sẽ được batch sau ghi đè (upsert theo `order_id`). Lịch sử trạng thái lưu ở `orders.status_history` (JSON).

Bộ mock luôn 1 đơn = 1 dòng = 1 SKU. Schema vẫn tách `orders` / `order_lines` để dùng được với file thật nhiều dòng một đơn.

### 2.2 `bao_cao_ads.csv` → `ad_hourly`

Header: `Thời gian, Mã chiến dịch, Tên chiến dịch, Trạng thái, Ngân sách ngày, Chi phí, Lượt hiển thị, Lượt click, Đơn hàng, Doanh thu`

| Cột gốc | Đích | Xử lý |
|---|---|---|
| Thời gian | `ad_hourly.hour_utc` | **Không ghi múi giờ.** Phát hiện độ lệch (§3.2), rồi `parse(...) - offset` |
| Mã chiến dịch | `campaign_id` | Khóa cùng `hour_utc` |
| Tên chiến dịch | `campaigns.name` | Cập nhật nếu đổi |
| Trạng thái | `status` | `Đang chạy`→running, `Tạm dừng`→paused, `Hết ngân sách`→budget_exhausted |
| Ngân sách ngày | `daily_budget` | int. Ngày đôi gấp 3 trong mock: giữ nguyên số trong file |
| Chi phí, Lượt hiển thị, Lượt click | `spend`, `impressions`, `clicks` | int |
| Đơn hàng, Doanh thu | `attributed_orders`, `attributed_revenue` | **Số sàn ghi nhận cho ads**, không phải hiệu quả thật. Đặt tên cột có tiền tố `attributed_` để không ai nhầm |

Bẫy trong mock:
- Cột thời gian là **UTC**, chậm 7 tiếng.
- **3 giờ cuối lịch sử chưa có** (21:00–23:00 ngày 10/11 giờ VN). Không phải chi 0: trạng thái `unknown`, ghi `ADS_LAG`.
- **Thiếu trọn ngày 20/09 giờ VN** (tức 2026-09-19 17:00 → 2026-09-20 16:00 theo cột UTC). Ghi `ADS_GAP_DAY`. Trong panel, các giờ đó `ad_status='unknown'`, `spend=NULL` và bị loại khỏi ước lượng iROAS.

### 2.3 `ton_kho_google_sheet.csv` → `inventory_snapshots`, `restock_plans`

Lịch sử: `Ngày, Mã nội bộ, Tên hàng, Tồn thực tế, Đang về, Ngày dự kiến về, Ghi chú`, chụp **cuối ngày** (sau giờ 23 bán xong).
Live: `Thời điểm cập nhật, …` chụp **cuối mỗi giờ** (`'2026-11-11 01:00'` = tồn lúc 01:00, tức sau giờ 00).

| Cột gốc | Đích | Xử lý |
|---|---|---|
| Ngày / Thời điểm cập nhật | `snapshot_at` | Ngày → `ngày + 1 ngày 00:00` giờ VN (cuối ngày) → UTC; giờ → giờ VN → UTC |
| Mã nội bộ | `sku_id` qua `sku_aliases` (nguồn `inventory`) | Ghép SKU (§5) |
| Tên hàng | `sku_aliases.raw_name` | Tên viết tắt: "Bình nước GN 500ml - xanh" |
| Tồn thực tế | `on_hand` | Âm → `NEG_STOCK`, đặt `on_hand=NULL`, `quality_flag='negative'`; panel nội suy từ hai snapshot kề |
| Đang về, Ngày dự kiến về | `restock_plans` (qty, `planned_date`) | Là **ngày dự kiến**, không phải ngày về thật. Có lô trễ 2–4 ngày |
| Ghi chú | `inventory_snapshots.note` | Parse `Nhập {qty}` → sự kiện nhập hàng thật (`restock_receipts`). Các ghi chú khác ("Xuất 2 sp…", "Loại 71 áo…") → `STOCK_ADJUSTMENT` |

Combo `COMBO-NOICHIEN` **không có dòng tồn** vì trừ tồn của sản phẩm thành phần. Thành phần combo không có trong file nào, nên người dùng khai báo ở bước onboarding (bảng `bundle_components`). Seed demo: `COMBO-NOICHIEN = 1 × NOICHIEN2L + 1 × GIAYLOT50`. Ghi `BUNDLE_NO_STOCK_ROW` cho đến khi khai báo xong.

### 2.4 `gia_von_nhap_tay.csv` → `cost_items`

Header: `tên sp, giá vốn, phí đóng gói, ghi chú`. Không có mã, chỉ có tên tự do, nên mọi dòng phải qua ghép SKU (nguồn `cost`).

`parse_vnd(text) -> int | None` (`app/core/money.py`):

```python
def parse_vnd(text: str) -> int | None:
    s = unidecode(text).strip().lower().replace(" ", "")
    s = s.removesuffix("vnd").removesuffix("d")   # "đ" thành "d" sau unidecode
    if not s:
        return None
    mult = 1
    if s.endswith("k"):
        mult, s = 1000, s[:-1]
    elif s.endswith("tr"):
        mult, s = 1_000_000, s[:-2]
    # Kiểu VN: dấu chấm = phân tách nghìn, dấu phẩy = thập phân
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    s = s.replace(",", ".")
    try:
        return round(float(s) * mult)
    except ValueError:
        return None
```

Test bắt buộc: `45000`, `45.000`, `45k`, `45.000đ`, `455.000đ`, `3k`, `1,5tr` → 45000, 45000, 45000, 45000, 455000, 3000, 1500000. Không đọc được → `MONEY_FORMAT`.

Ghi chú "chưa gồm VAT", "giá cũ, cần cập nhật" → `cost_items.note` + cờ `COST_NOTE_REVIEW` (mức info). SKU không có dòng giá vốn → `MISSING_COST` (mức error): **chặn tính kinh tế** cho SKU đó, nhãn kết quả hạ xuống `scenario` cho tới khi người dùng nhập.

### 2.5 `cau_truc_chien_dich.json` → `campaigns`, `campaign_skus`

```json
{"ma_chien_dich":"CD001","ten_chien_dich":"[Tìm kiếm] Bình nước giữ nhiệt","loai":"Tìm kiếm",
 "ngan_sach_ngay":367000,"bat_dau":"2026-07-14 00:00","sku":["BINHNUOC500-XANH", "..."],"ghi_chu":"..."}
```

`campaigns(campaign_id, name, type, daily_budget, started_at)`, `campaign_skus(campaign_id, sku_id)`. Chiến dịch có > 1 SKU thì `sku_level_control=false`: **simulator không được đề xuất tắt riêng một SKU** ([05](05-kinh-te-va-mo-phong.md) §6).

### 2.6 `chuong_trinh_sale.json` → `sale_commitments`

```json
{"chuong_trinh":"Siêu sale 11.11","ngay":"2026-11-11","khung_flash_sale":["00:00-02:00","12:00-14:00"],
 "quy_dinh_chung":["Không tăng giá ...","Không hủy đăng ký sau 2026-11-08"],
 "cam_ket":[{"sku":"NOICHIEN2L","gia_cam_ket":531000,"ton_giu_cho_flash_sale":23}],
 "quy_dinh_chien_dich":[{"ma_chien_dich":"CD002","quy_dinh":"Không tắt chiến dịch trong khung flash sale (đã cam kết với sàn)"}]}
```

Lưu thành các dòng ràng buộc có kiểu:

| `kind` | Từ | Ý nghĩa cho simulator |
|---|---|---|
| `committed_price` | `cam_ket[].gia_cam_ket` | Giá hiệu lực ngày 11.11; không được đề xuất tăng giá |
| `flash_reserve` | `cam_ket[].ton_giu_cho_flash_sale` | Tồn giữ riêng, chỉ bán trong khung flash sale |
| `flash_window` | `khung_flash_sale` | Giờ VN → UTC |
| `campaign_must_run` | `quy_dinh_chien_dich` | Chiến dịch không được `paused` trong khung flash sale |

`quy_dinh_chien_dich[].quy_dinh` là câu tự do. Ở bản hackathon, adapter nhận diện bằng mẫu "Không tắt chiến dịch trong khung flash sale" → `campaign_must_run`. Câu khác → `UNPARSED_RULE` để người dùng xác nhận (AI gợi ý ánh xạ).

## 3. Kiểm chất lượng dữ liệu

### 3.1 Danh sách luật

| Mã | Mức | Phát hiện | Hành vi |
|---|---|---|---|
| `DUP_ORDER` | warn | Trùng `Mã đơn hàng` và mọi cột | Giữ 1 dòng |
| `ORDER_TOTAL_MISMATCH` | warn | `Tổng tiền hàng ≠ Giá ưu đãi × Số lượng` | Dùng `unit_price × qty` |
| `PROVISIONAL_STATUS` | info | Trạng thái chưa cuối | Gắn cờ trên panel |
| `NEG_STOCK` | error | `Tồn thực tế < 0` | Bỏ giá trị, nội suy |
| `STOCK_JUMP` | warn | Tồn thay đổi khác `tồn hôm trước − bán + nhập` quá `max(5, 10%)` | Ghi chú, tin snapshot |
| `STOCK_ADJUSTMENT` | info | Ghi chú tồn không phải "Nhập …" | Ghi sự kiện điều chỉnh |
| `MISSING_COST` | error | SKU có bán nhưng không có giá vốn | Chặn kinh tế, nhãn `scenario` |
| `MONEY_FORMAT` | error | `parse_vnd` trả None | Hỏi người dùng |
| `ADS_TZ_SHIFT` | warn | Độ lệch múi giờ ≠ 0 (§3.2) | Tự sửa, hiện cho người dùng xác nhận |
| `ADS_GAP_DAY` | warn | Có ≥ 20 giờ liền không có dòng ads cho mọi chiến dịch đã bắt đầu | `spend=NULL`, loại khỏi iROAS |
| `ADS_LAG` | info | Giờ cuối của ads < giờ cuối của đơn | Hiện độ trễ trên huy hiệu độ mới |
| `SPEND_OVER_BUDGET` | warn | Tổng chi ngày > ngân sách ngày × 1,05 | Ghi chú |
| `UNMATCHED_SKU` | error | Alias chưa ghép | Vào hàng chờ xác nhận |
| `BUNDLE_NO_STOCK_ROW` | warn | SKU có bán, không có tồn, chưa khai báo combo | Hỏi người dùng |
| `PII_DROPPED` | info | Đã bỏ cột cá nhân | Hiện trong báo cáo nhập |
| `SUSPICIOUS_NOTE` | warn | Ghi chú khớp mẫu câu lệnh | Đánh dấu nội dung không tin cậy cho AI |
| `UNKNOWN_SOURCE` / `UNPARSED_RULE` | error | Không nhận diện được | Hỏi người dùng hoặc AI gợi ý |

Mẫu `SUSPICIOUS_NOTE` (regex sau khi `unidecode` và lowercase): `bo qua (moi|tat ca)? ?huong dan`, `ignore (all|previous) instructions`, `tat (toan bo|het) quang cao`, `de xuat .* ngay lap tuc`, `system prompt`. Mục tiêu không phải chặn hết, mà để AI biết đây là văn bản không tin cậy. Phòng thủ chính nằm ở lớp AI ([07](07-lop-ai.md)).

### 3.2 Phát hiện lệch múi giờ của file ads

Ý tưởng: chi ads chạy theo nhịp giờ trong ngày giống đơn hàng (thấp về đêm, cao trưa và tối). Tìm độ dịch `s` làm hai nhịp khớp nhau nhất.

```python
def detect_offset_hours(orders_by_hour: pd.Series, spend_by_hour: pd.Series) -> tuple[int, float]:
    """orders_by_hour: tổng đơn theo giờ-trong-ngày (0..23, giờ VN). spend_by_hour: tổng chi theo
    giờ-trong-ngày đọc thô từ file ads. Trả (offset, corr): giờ_VN = giờ_file + offset."""
    best = max(range(-12, 13), key=lambda s: np.corrcoef(orders_by_hour, np.roll(spend_by_hour, s))[0, 1])
    corr = np.corrcoef(orders_by_hour, np.roll(spend_by_hour, best))[0, 1]
    return best, corr
```

Chỉ dùng ngày thường (loại ngày đôi). Chấp nhận khi `corr ≥ 0,8` và vượt offset thứ hai ≥ 0,1. Kết quả mong đợi trên mock: **offset = +7**. Kiểm thêm: chiến dịch tắt ban đêm (`Tạm dừng` lúc 0–5 giờ VN) phải rơi đúng 17–22 giờ theo cột file.

## 4. DDL DuckDB (bảng lớp dữ liệu)

Các bảng của lớp sau nằm trong file tương ứng: 03 (`model_registry`, `forecasts`, `model_scores`), 04 (`iroas_estimates`), 05 (`goals`, `parameters`, `scenarios`, `scenario_results`, `recommendations`), 07 và 10 (`decisions`, `actions`, `alerts`, `ai_sessions`, `ai_tool_calls`).

```sql
-- app/warehouse/schema.sql  (phần lớp dữ liệu)
CREATE TABLE IF NOT EXISTS ingest_batches (
  batch_id        VARCHAR PRIMARY KEY,           -- ULID
  source_type     VARCHAR NOT NULL,              -- orders|ads|inventory|cost|campaigns|sale_program
  file_name       VARCHAR NOT NULL,
  file_sha256     VARCHAR NOT NULL UNIQUE,
  received_at     TIMESTAMPTZ NOT NULL,
  as_of           TIMESTAMPTZ,                   -- dữ liệu đúng tới (giờ cuối có dữ liệu)
  row_count       INTEGER,
  dropped_columns VARCHAR[],                     -- PII đã bỏ
  tz_offset_hours INTEGER DEFAULT 0,
  mode            VARCHAR NOT NULL,              -- history|live
  status          VARCHAR NOT NULL               -- ok|ok_with_issues|failed
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
  issue_id      VARCHAR PRIMARY KEY,
  batch_id      VARCHAR NOT NULL REFERENCES ingest_batches(batch_id),
  code          VARCHAR NOT NULL,                -- DUP_ORDER, NEG_STOCK, ...
  severity      VARCHAR NOT NULL,                -- info|warn|error
  entity_type   VARCHAR,                         -- order|sku|campaign|snapshot|file
  entity_id     VARCHAR,
  row_ref       VARCHAR,                         -- "line 1043" hoặc khóa
  detail        JSON,                            -- {"value": -7, "expected": 112}
  status        VARCHAR NOT NULL DEFAULT 'open', -- open|auto_fixed|confirmed|dismissed
  resolved_by   VARCHAR,
  created_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
  product_id    VARCHAR PRIMARY KEY,             -- tiền tố SKU sàn trước dấu '-' (vd BINHNUOC500)
  name          VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS skus (
  sku_id        VARCHAR PRIMARY KEY,             -- SKU sàn (khóa chuẩn), vd BINHNUOC500-XANH
  product_id    VARCHAR NOT NULL REFERENCES products(product_id),
  variant_name  VARCHAR,
  list_price    INTEGER,
  is_bundle     BOOLEAN NOT NULL DEFAULT FALSE,
  first_sale_at TIMESTAMPTZ,
  demand_group  VARCHAR,                         -- on_dinh|mua_vu|trend|moi|ban_thua|unclassified (03 §6)
  demand_group_source VARCHAR,                   -- auto|user
  demand_group_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sku_aliases (
  alias_id      VARCHAR PRIMARY KEY,
  source_type   VARCHAR NOT NULL,                -- orders|inventory|cost
  raw_code      VARCHAR,                         -- mã nội bộ (inventory) hoặc NULL
  raw_name      VARCHAR NOT NULL,
  norm_name     VARCHAR NOT NULL,
  sku_id        VARCHAR REFERENCES skus(sku_id), -- NULL = chưa ghép
  score         DOUBLE,
  method        VARCHAR,                         -- exact|code_pattern|fuzzy|embedding|user
  status        VARCHAR NOT NULL,                -- auto_accepted|pending|confirmed|rejected
  confirmed_by  VARCHAR,
  UNIQUE (source_type, raw_code, raw_name)
);

CREATE TABLE IF NOT EXISTS bundle_components (
  bundle_sku_id    VARCHAR NOT NULL REFERENCES skus(sku_id),
  component_sku_id VARCHAR NOT NULL REFERENCES skus(sku_id),
  qty              INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (bundle_sku_id, component_sku_id)
);

CREATE TABLE IF NOT EXISTS orders (
  order_id          VARCHAR PRIMARY KEY,
  ordered_at        TIMESTAMPTZ NOT NULL,
  status            VARCHAR NOT NULL,            -- completed|returned|cancelled|shipping|awaiting_pickup|pending
  status_final      BOOLEAN NOT NULL,
  status_history    JSON,                        -- [{"status":"pending","batch_id":"..."}]
  buyer_note_masked VARCHAR,                     -- đã che, KHÔNG tin cậy
  note_flagged      BOOLEAN DEFAULT FALSE,       -- SUSPICIOUS_NOTE
  batch_id          VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS order_lines (
  order_id     VARCHAR NOT NULL REFERENCES orders(order_id),
  line_no      INTEGER NOT NULL,
  sku_id       VARCHAR NOT NULL REFERENCES skus(sku_id),
  qty          INTEGER NOT NULL,
  list_price   INTEGER NOT NULL,
  unit_price   INTEGER NOT NULL,
  line_total   INTEGER NOT NULL,
  PRIMARY KEY (order_id, line_no)
);

CREATE TABLE IF NOT EXISTS inventory_snapshots (
  sku_id       VARCHAR NOT NULL REFERENCES skus(sku_id),
  snapshot_at  TIMESTAMPTZ NOT NULL,             -- thời điểm tồn đúng (cuối ngày / cuối giờ)
  on_hand      INTEGER,                          -- NULL nếu giá trị gốc sai
  quality_flag VARCHAR,                          -- negative|interpolated|NULL
  note         VARCHAR,
  granularity  VARCHAR NOT NULL,                 -- day|hour
  batch_id     VARCHAR NOT NULL,
  PRIMARY KEY (sku_id, snapshot_at)
);

CREATE TABLE IF NOT EXISTS restock_plans (           -- "Đang về" như seller khai báo
  sku_id        VARCHAR NOT NULL REFERENCES skus(sku_id),
  seen_at       TIMESTAMPTZ NOT NULL,            -- snapshot nhìn thấy kế hoạch
  qty_incoming  INTEGER NOT NULL,
  planned_date  DATE NOT NULL,                   -- ngày dự kiến (KHÔNG phải ngày về thật)
  PRIMARY KEY (sku_id, seen_at)
);

CREATE TABLE IF NOT EXISTS restock_receipts (        -- nhập thật, parse từ ghi chú "Nhập {qty}"
  sku_id       VARCHAR NOT NULL REFERENCES skus(sku_id),
  received_on  DATE NOT NULL,
  qty          INTEGER NOT NULL,
  planned_date DATE,                             -- ghép với restock_plans gần nhất → đo độ trễ
  PRIMARY KEY (sku_id, received_on)
);

CREATE TABLE IF NOT EXISTS campaigns (
  campaign_id       VARCHAR PRIMARY KEY,
  name              VARCHAR NOT NULL,
  type              VARCHAR,                     -- Tìm kiếm|Khám phá|Tự động
  daily_budget      INTEGER NOT NULL,
  started_at        TIMESTAMPTZ,
  sku_level_control BOOLEAN NOT NULL             -- false nếu > 1 SKU
);

CREATE TABLE IF NOT EXISTS campaign_skus (
  campaign_id VARCHAR NOT NULL REFERENCES campaigns(campaign_id),
  sku_id      VARCHAR NOT NULL REFERENCES skus(sku_id),
  PRIMARY KEY (campaign_id, sku_id)
);

CREATE TABLE IF NOT EXISTS ad_hourly (
  campaign_id        VARCHAR NOT NULL REFERENCES campaigns(campaign_id),
  hour_utc           TIMESTAMPTZ NOT NULL,
  status             VARCHAR NOT NULL,           -- running|paused|budget_exhausted|unknown
  daily_budget       INTEGER,
  spend              INTEGER,                    -- NULL khi unknown
  impressions        INTEGER,
  clicks             INTEGER,
  attributed_orders  INTEGER,                    -- số SÀN ghi nhận
  attributed_revenue INTEGER,
  batch_id           VARCHAR NOT NULL,
  PRIMARY KEY (campaign_id, hour_utc)
);

CREATE TABLE IF NOT EXISTS cost_items (
  sku_id          VARCHAR NOT NULL REFERENCES skus(sku_id),
  valid_from      TIMESTAMPTZ NOT NULL,          -- batch received_at
  unit_cost       INTEGER NOT NULL,
  packaging_cost  INTEGER NOT NULL DEFAULT 0,
  note            VARCHAR,
  source          VARCHAR NOT NULL,              -- file|user
  PRIMARY KEY (sku_id, valid_from)
);

CREATE TABLE IF NOT EXISTS price_events (            -- suy ra từ đơn: giá hiệu lực theo ngày
  sku_id         VARCHAR NOT NULL REFERENCES skus(sku_id),
  day_local      DATE NOT NULL,
  list_price     INTEGER NOT NULL,
  eff_price      INTEGER NOT NULL,               -- trung vị unit_price trong ngày
  discount_pct   DOUBLE NOT NULL,                -- 1 - eff/list
  source         VARCHAR NOT NULL,               -- orders|commitment|carried_forward
  PRIMARY KEY (sku_id, day_local)
);

CREATE TABLE IF NOT EXISTS sale_commitments (
  commitment_id VARCHAR PRIMARY KEY,
  program       VARCHAR NOT NULL,                -- "Siêu sale 11.11"
  day_local     DATE NOT NULL,
  kind          VARCHAR NOT NULL,                -- committed_price|flash_reserve|flash_window|campaign_must_run|rule_text
  sku_id        VARCHAR,
  campaign_id   VARCHAR,
  value_int     INTEGER,                         -- giá hoặc số lượng
  window_start  TIMESTAMPTZ,
  window_end    TIMESTAMPTZ,
  raw_text      VARCHAR
);
```

`hourly_panel` xem §6. Index: DuckDB dùng zone map, nên chỉ cần ghi dữ liệu theo thứ tự `(sku_id, hour_utc)`.

## 5. Ghép SKU 3 nguồn

Nguồn: **SKU sàn** (từ đơn hàng: `SKU phân loại hàng` + `Tên sản phẩm` + `Tên phân loại hàng`) là chuẩn. Cần ghép **mã nội bộ + tên hàng** (sheet tồn) và **tên tự do** (giá vốn) vào SKU sàn.

### 5.1 Chuẩn hóa tên

```python
ABBREV = {"gn": "giu nhiet", "kd": "khong dau", "tt": "trang tri", "tp": "thuc pham", "ct": "cam tay"}
VARIANT_TOKENS = {"xanh","hong","den","trang","be","s","m","l","xl","nho","vua","lon",
                  "500ml","750ml","1","lit","g1","g3","g5","goi","a","b","c","5m","10m","day"}

def norm_name(s: str) -> str:
    s = unidecode(s).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)            # bỏ "-", "(", ")", nhân đôi khoảng trắng
    toks = [ABBREV.get(t, t) for t in s.split()]
    return " ".join(" ".join(toks).split())
```

Ví dụ: `"Bình nước GN 500ml - xanh"`, `"binh nuoc giu nhiet 500ml den"` và `"Bình  nước  giữ  nhiệt  500ml  Hồng"` đều thành dạng `binh nuoc giu nhiet 500ml <màu>`.

Bảng `ABBREV` cho phép người dùng thêm từ UI và lưu ở `parameters` (scope shop, key `alias_abbreviations`).

### 5.2 Điểm ghép

Với mỗi alias `a` và mỗi ứng viên SKU `c` (tên chuẩn hóa của `Tên sản phẩm + Tên phân loại hàng`):

```
s_token   = rapidfuzz.fuzz.token_set_ratio(a, c) / 100
s_char    = rapidfuzz.fuzz.ratio(a, c) / 100
s_variant = 1 nếu tập token biến thể của a == của c; 0,5 nếu một bên không có biến thể; 0 nếu mâu thuẫn
score     = 0,55·s_token + 0,25·s_char + 0,20·s_variant
```

Ràng buộc cứng: **biến thể mâu thuẫn** (xanh và hồng, S và M) thì `score = 0`. Đây là lỗi nguy hiểm nhất, vì ghép nhầm biến thể làm sai tồn kho.

Tín hiệu phụ cho sheet tồn: mã nội bộ có dạng `<gốc>-<2 ký tự>` (vd `GD0001-XA`). Các alias cùng `<gốc>` thường là cùng sản phẩm. Dùng như điểm thưởng +0,05 khi mọi SKU cùng gốc trỏ về cùng `product_id`, **không dùng làm luật**: file thật có thể khác.

### 5.3 Gán 1–1 và ngưỡng

1. Chặn ứng viên (blocking): chỉ so alias với SKU có ít nhất 1 token sản phẩm chung (không tính token biến thể). Giảm từ 300×300 xuống vài chục cặp mỗi alias.
2. Trong mỗi nguồn, giải **bài toán gán** (`scipy.optimize.linear_sum_assignment` trên ma trận `1 − score`) để một alias chỉ ghép một SKU và ngược lại. Combo được phép không có dòng tồn.
3. Ngưỡng:

| Điểm | Trạng thái | Hành vi |
|---|---|---|
| ≥ 0,90 và hơn ứng viên thứ hai ≥ 0,05 | `auto_accepted` | Dùng ngay |
| 0,65–0,90, hoặc chênh ứng viên thứ hai < 0,05 | `pending` | Vào hàng chờ, UI hiện top 3 ứng viên |
| < 0,65 | `pending` (không gợi ý) | Hỏi người dùng; AI có thể gợi ý (embedding) |

4. **Embedding (tùy chọn, bật khi `OPENAI_MODEL_EMBED` có giá trị):** với alias còn `pending`, nhúng `norm_name` của alias và ứng viên, rồi dùng cosine làm gợi ý thứ hai. Embedding chỉ được **gợi ý**, không tự `auto_accepted`. Cache embedding theo `norm_name`.
5. Người dùng xác nhận → `status=confirmed`, `method=user`. Lần nhập sau dùng lại alias đã xác nhận (khớp chính xác `raw_code`/`raw_name`).

API hàng chờ: `GET /api/v1/ingest/sku-queue`, `POST /api/v1/ingest/sku-queue/{alias_id}` ([08](08-api.md)).

## 6. `hourly_panel`: bảng gốc SKU × giờ

Một dòng cho mỗi `(sku_id, hour_utc)` từ giờ đầu tiên của lịch sử tới `as_of`. 300 SKU × 2.880 giờ ≈ 864 nghìn dòng (demo), 2.000 SKU ≈ 5,8 triệu dòng (tải nặng).

```sql
CREATE TABLE IF NOT EXISTS hourly_panel (
  sku_id            VARCHAR NOT NULL,
  hour_utc          TIMESTAMPTZ NOT NULL,
  hour_local        TINYINT NOT NULL,           -- 0..23 giờ VN
  dow_local         TINYINT NOT NULL,           -- 0=CN .. 6=T7
  day_local         DATE NOT NULL,
  -- bán hàng
  units_gross       INTEGER NOT NULL,           -- mọi đơn trừ cancelled
  units_provisional INTEGER NOT NULL,           -- phần thuộc đơn chưa cuối
  units_cancelled   INTEGER NOT NULL,
  orders_count      INTEGER NOT NULL,
  revenue_net       BIGINT NOT NULL,            -- Σ unit_price·qty trừ cancelled, trừ returned
  -- giá
  list_price        INTEGER,
  eff_price         INTEGER,
  discount_pct      DOUBLE,
  -- tồn
  stock_begin       INTEGER,                    -- ước lượng đầu giờ
  stock_end         INTEGER,
  stock_source      VARCHAR,                    -- snapshot_hour|reconstructed|interpolated
  stockout          BOOLEAN NOT NULL,           -- nhu cầu có thể bị chặn trong giờ này
  stockout_conf     DOUBLE,                     -- 0..1
  low_stock         BOOLEAN NOT NULL,           -- tồn < 2 × nhu cầu ngày gần nhất
  restock_arrived   INTEGER DEFAULT 0,
  -- ads (chia từ chiến dịch về SKU)
  campaign_id       VARCHAR,
  ad_status         VARCHAR,                    -- running|paused|budget_exhausted|unknown|none
  ad_spend_alloc    DOUBLE,                     -- NULL khi unknown
  ad_alloc_share    DOUBLE,
  attributed_orders_alloc DOUBLE,
  -- lịch / sự kiện
  is_double_day     BOOLEAN NOT NULL,
  is_pre_double_day BOOLEAN NOT NULL,
  is_post_double_day BOOLEAN NOT NULL,
  is_payday         BOOLEAN NOT NULL,           -- ngày 25
  in_flash_window   BOOLEAN NOT NULL,
  days_to_tet       INTEGER,                    -- Tết 2027-02-06
  days_to_gift_peak INTEGER,                    -- tới 20/10 hoặc 20/11 gần nhất
  -- chất lượng
  data_flags        VARCHAR[],                  -- {'ADS_GAP_DAY','PROVISIONAL'}
  panel_version     VARCHAR NOT NULL,           -- batch_id gần nhất ảnh hưởng dòng này
  PRIMARY KEY (sku_id, hour_utc)
);
```

### 6.1 Dựng tồn theo giờ (`panel/stock.py`)

Lịch sử chỉ có tồn **cuối ngày**. Dựng lại theo giờ:

```
Với mỗi SKU không phải combo, mỗi ngày d (giờ VN):
  S_prev  = on_hand(cuối ngày d-1)       # NULL → nội suy tuyến tính giữa hai snapshot hợp lệ kề
  R_d     = Σ restock_receipts(d)         # "Nhập {qty}" trong ghi chú
  A_d     = điều chỉnh trong ghi chú (nếu parse được số), còn lại để vào sai lệch
  Đi xuôi từng giờ h:
     stock_begin[h] = S_prev + R_d·[h ≥ giờ nhập] − Σ_{h'<h} units_gross[h']
  Giờ nhập không biết → giả định 10:00 VN (trong mock: 10:00–16:00)
  Sai lệch e = on_hand(cuối ngày d) − stock_end[23]
  |e| ≤ max(3, 5%) → phân bổ e đều vào các giờ có bán; lớn hơn → STOCK_JUMP, tin snapshot cuối ngày
     và đặt stock_end[23] = on_hand(d)
Combo: stock = min(stock của thành phần / qty)
Live: có snapshot cuối mỗi giờ → stock_source='snapshot_hour', dùng trực tiếp
```

**Cờ hết hàng:** `stockout = stock_begin ≤ 0` hoặc `(stock_begin ≤ units_gross và units_gross > 0)` (bán hết giữa giờ). `stockout_conf` = 1 khi có snapshot theo giờ, 0,8 khi dựng lại, 0,5 khi nội suy.

Vì sao quan trọng: giờ hết hàng có `units_gross = 0` nhưng **nhu cầu thật > 0**. Model không xử lý sẽ dự báo thấp ([03](03-lop-mo-hinh.md) §4).

### 6.2 Chia chi ads chiến dịch về SKU (`panel/allocation.py`)

Báo cáo ads chỉ có số theo chiến dịch. Chia về SKU theo tỷ trọng bán gần đây, chỉ tính SKU còn hàng:

```
w_i(t) = (units_gross_i trong 7 ngày trước t + 1) · [stockout_i(t) = false]
share_i(t) = w_i(t) / Σ_j∈campaign w_j(t)
ad_spend_alloc_i(t) = spend_campaign(t) · share_i(t)
```

`+1` để SKU chưa bán vẫn nhận một phần nhỏ. Khi mọi SKU trong chiến dịch hết hàng, chia đều (sàn vẫn tiêu tiền). `attributed_orders_alloc` chia cùng tỷ trọng, **chỉ để hiển thị ROAS sàn**, không dùng để ước lượng hiệu quả.

Ghi chú cho ML: `ads_effect` ước lượng ở **cấp chiến dịch** ([04](04-hieu-qua-ads.md)), nên sai số của cách chia chỉ ảnh hưởng hiển thị theo SKU, không ảnh hưởng iROAS chiến dịch.

### 6.3 Giá và sự kiện

- `price_events`: mỗi (SKU, ngày) lấy trung vị `unit_price` của đơn không hủy. Ngày không có đơn thì mang giá gần nhất (`carried_forward`). Ngày 11.11 lấy `committed_price` nếu có.
- `is_double_day`: tháng = ngày (8.8, 9.9, 10.10, 11.11). `is_pre_double_day` / `is_post_double_day`: ngày trước / sau.
- `in_flash_window`: từ `sale_commitments(kind='flash_window')`.

### 6.4 Cập nhật tăng dần

`build_hourly_panel(sku_ids: list[str] | None, start: datetime, end: datetime)` chỉ tính lại khoảng bị ảnh hưởng: batch đơn hàng → giờ của các đơn đó tới `as_of`; batch tồn → từ ngày của snapshot sớm nhất; batch ads → giờ của dòng ads. Tính bằng SQL DuckDB (aggregate) + pandas cho phần dựng tồn tuần tự theo giờ.

Đã làm (PNL-04, xem [M1-pipeline](../quyet-dinh/M1-pipeline.md) "Hoàn thiện M1"):

- **Khoảng ghi** (pipeline, `_panel_start`): giờ sớm nhất của dòng mới, lùi về nửa đêm VN của ngày đó (giá là trung vị cả ngày), về snapshot hợp lệ gần nhất trước nó (sai lệch ở snapshot sau được rải cho cả đoạn) và về đầu lỗ ads ngay trước dòng ads mới (`ADS_LAG` thành `ADS_MISSING_HOUR`/`ADS_GAP_DAY`). Chỉ SKU bị chạm (đã đóng theo combo/thành phần và chiến dịch); SKU khác chỉ ghi phần giờ mới.
- **Khoảng tính** (panel, chế độ cửa sổ, mặc định khi có `start`): từ nửa đêm VN trước `start − 168 giờ` và trước snapshot hợp lệ cuối cùng ≤ `start` của mỗi SKU có tồn (không nằm giữa ngày của một phiếu nhập). Trạng thái vắt qua đầu cửa sổ được mang vào: độ dài lỗ ads đã có trước cửa sổ và giá mang theo (`carried_forward`) của từng SKU. Dòng ghi ra giống hệt dựng toàn bộ (có test so từng ô).

Mục tiêu hiệu năng: dựng toàn bộ panel demo < 20 giây; cập nhật một giờ live cho 300 SKU < 2 giây.

## 7. Live: nạp tăng dần

`replay.js` ghi lại **toàn bộ** file của ngày 11.11 tính tới giờ hiện tại (không phải phần mới) và cập nhật `trang_thai.json`:

```json
{"ngay":"2026-11-11","don_hang_va_ton_den_het":"14:59","ads_den_het":"12:59","cap_nhat_luc":"..."}
```

`ingest_live_delta(live_dir)`:
1. Đọc `trang_thai.json`. Không đổi so với lần trước → bỏ qua.
2. Đọc 3 file, upsert theo khóa: đơn `order_id`, ads `(campaign_id, hour_utc)`, tồn `(sku_id, snapshot_at)`. Upsert tự lọc phần đã có.
3. `as_of` của nguồn đơn/tồn = `don_hang_va_ton_den_het`; của ads = `ads_den_het`. Hai mốc khác nhau thì hiện **huy hiệu độ mới riêng cho từng nguồn** (ads thường trễ 2 giờ).
4. Giờ chưa có ads trong khoảng `(ads_den_het, don_hang_va_ton_den_het]` → `ad_status='unknown'`. Simulator dùng trạng thái chiến dịch đã biết gần nhất và gắn cờ.
5. Phát `DataUpdated(skus_changed=...)`.

File được `replay.js` ghi bằng cách ghi file tạm rồi đổi tên, nên không bao giờ đọc phải file dở.

## 8. Nghiệm thu bằng `ground_truth/` (script `backend/eval/eval_data.py`)

| Kiểm tra | Cách tính | Ngưỡng đạt |
|---|---|---|
| Ghép SKU, nguồn tồn | So `sku_aliases(inventory)` với `sku_that.csv(sku, ma_noi_bo)` | Độ chính xác của `auto_accepted` ≥ 99%; tổng `auto_accepted + pending` phủ 100%; `pending` ≤ 10% |
| Ghép SKU, nguồn giá vốn | So với `sku_that.csv(sku, ten_san_pham, phan_loai)` | Như trên. **Không có ca ghép nhầm biến thể nào** ở trạng thái `auto_accepted` |
| Đơn trùng | Số `DUP_ORDER` | = `manifest.loi_cai_san[trung_don].so_dong` |
| Tồn âm | Tập `(ngày, mã nội bộ)` của `NEG_STOCK` | = `manifest.loi_cai_san[ton_am].chi_tiet` (3 dòng) |
| Thiếu giá vốn | Tập SKU `MISSING_COST` | = `manifest.loi_cai_san[thieu_gia_von].sku` |
| Múi giờ ads | Offset phát hiện | = +7 |
| Ads thiếu ngày, trễ giờ | `ADS_GAP_DAY` = 2026-09-20; `ADS_LAG` = 3 giờ | Đúng |
| Dữ liệu cá nhân | `dropped_columns` | Có `Người mua`, `Số điện thoại`, `Địa chỉ nhận hàng`; `grep "Khách Test" data/megaday.duckdb` không thấy |
| Bẫy injection | `orders.note_flagged` | Đúng đơn `manifest.loi_cai_san[prompt_injection].ma_don` |
| Số bán theo ngày | `Σ units_gross` (đã loại đơn hủy, giữ đơn trả) theo (SKU, ngày) so với `nhu_cau_theo_ngay.ban_rong` | Khớp tuyệt đối tới ngày 08/11; ngày 09–10/11 lệch ≤ 2% (do đơn "Chờ lấy hàng" sẽ hủy sau) |
| Tồn cuối ngày | `stock_end` lúc 23:00 so với `ton_cuoi_ngay` | Khớp tuyệt đối trừ 3 dòng tồn âm (nội suy, lệch ≤ 5%) |
| Cờ hết hàng | `stockout` so với `nhu_cau_theo_gio.het_hang` trong các cửa sổ có dữ liệu giờ | Precision ≥ 0,9, recall ≥ 0,9 |

Báo cáo `eval_data.py` in bảng trên và ghi `backend/eval/reports/data_<ngày>.md`. Phần này dùng làm slide "app hiểu dữ liệu bẩn".

## Tiêu chí hoàn thành

- [ ] 6 adapter đọc đúng file `demo`, nhận diện bằng header, idempotent theo hash.
- [ ] `parse_vnd` qua 7 ca test ở §2.4.
- [ ] Phát hiện offset +7 cho file ads, có hiển thị để người dùng xác nhận.
- [ ] PII không bao giờ vào DB (test grep).
- [ ] Hàng chờ ghép SKU có API, có hiển thị top 3 ứng viên.
- [ ] `hourly_panel` dựng xong < 20 giây cho `demo`; cập nhật live 1 giờ < 2 giây.
- [ ] `eval_data.py` đạt mọi ngưỡng ở §8.

## Task gợi ý

| Mã | Việc | Người | nd |
|---|---|---|---|
| ING-01 | `adapters/base.py`, `detect.py`, `pii.py` (bỏ cột + che ghi chú) | D1 | 0,5 |
| ING-02 | Adapter đơn hàng + ánh xạ trạng thái + `DUP_ORDER` | D1 | 0,5 |
| ING-03 | Adapter ads + `timezone_check.py` + `ADS_GAP_DAY`/`ADS_LAG` | D1 | 1 |
| ING-04 | Adapter tồn kho (ngày + giờ), parse ghi chú nhập/điều chỉnh, `restock_plans` | D1 | 1 |
| ING-05 | Adapter giá vốn + `parse_vnd` + test | D1 | 0,5 |
| ING-06 | Adapter chiến dịch + chương trình sale (`sale_commitments`) | D1 | 0,5 |
| ING-07 | `quality.py`: toàn bộ luật §3.1 + ghi `data_quality_issues` | D1 | 1 |
| ING-08 | `sku_matching.py`: chuẩn hóa, điểm, gán 1–1, ngưỡng, hàng chờ | D1 | 1,5 |
| ING-09 | Embedding gợi ý cho alias khó (bật bằng env) | D3 | 0,5 |
| WH-01 | `schema.sql` lớp dữ liệu + `repo.py` upsert | D1 | 0,5 |
| WH-02 | `bundle_components` + màn khai báo combo (API) | D1 | 0,25 |
| PNL-01 | Aggregate đơn → panel (SQL) + giá + lịch sự kiện | D1 | 1 |
| PNL-02 | `stock.py`: dựng tồn theo giờ, cờ hết hàng, combo | D1 | 1,5 |
| PNL-03 | `allocation.py`: chia chi ads về SKU | D1 | 0,5 |
| PNL-04 | Cập nhật tăng dần + `ingest_live_delta` | D1 | 1 |
| EVD-01 | `eval/eval_data.py` theo §8 | D1 | 0,75 |

## Snapshot bằng chứng mô phỏng (migration 5)

`scenario_calculation_traces(scenario_id VARCHAR PRIMARY KEY, trace JSON NOT NULL,
results JSON NOT NULL)` lưu bằng chứng và kết quả đầy đủ của một lần mô phỏng.
Ghi cùng transaction với scenario/recommendation; không cập nhật snapshot khi
nguồn nhập, giá vốn hoặc tham số thay đổi sau đó. `trace.version` quản lý phiên
bản hợp đồng; `input_hash` liên kết với đầu vào của scenario. `results` giữ cả
điều kiện mục tiêu, xác suất đạt và kết quả riêng SKU để đọc lại không mất nghĩa.

Schema mới và migration đều tạo bảng theo cách idempotent. Các scenario cũ
không có snapshot vẫn đọc được, trả `calculation_trace=null`; không backfill bằng
dữ liệu hiện tại. Tham chiếu batch chỉ gồm các bản ghi nguồn liên quan có thể
xác định được; chưa có liên kết batch chính xác cho mọi giá vốn/cam kết sale.
Không lưu nội dung CSV hoặc dữ liệu cá nhân vào trace.

## Bản mô phỏng kế hoạch sale (migration 6)

`sale_plan_previews` lưu mã bản thử, chiến dịch, mục tiêu nháp không hoạt động,
scenario, snapshot mục tiêu gốc/mục tiêu hiệu lực và mốc áp dụng. Snapshot giữ
phiên bản gốc để phát hiện sửa đồng thời; dấu vết scenario vẫn thuộc migration 5.
Mô phỏng thử không thay mục tiêu/đề xuất đang áp dụng. Chỉ thao tác apply có xác
nhận mới kích hoạt mục tiêu, tăng phiên bản và ghi nhận apply trong cùng transaction.
Mô phỏng lỗi có thể để lại mục tiêu nháp không hoạt động, nhưng không thay mục tiêu cũ.
Khả năng áp dụng được kiểm tra lại với phiên bản mục tiêu và dấu vết dữ liệu/mô hình;
thử lại apply cùng bản không kích hoạt hoặc tăng phiên bản lần nữa.
