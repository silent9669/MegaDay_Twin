# 09 · Giao diện

Web app có 8 màn hình. Người vận hành đọc được kết luận trong 3 giây, bấm để xem lý do, bấm tiếp để kiểm dữ liệu. Mọi con số luôn đi kèm độ mới và nhãn tin cậy. Màn quan trọng nhất cho demo là **So sánh lựa chọn** với thanh vùng quyết định.

Stack: React 18 + Vite + TypeScript + Tailwind + TanStack Query, client API sinh từ OpenAPI (08-api). Thư mục `web/src/{pages,components,api,lib}`.

---

## 1. Bản đồ màn hình

| # | Màn | Route | Mục đích | Vai trò |
|---|---|---|---|---|
| 1 | Tổng quan ca | `/` | SKU nào cần xem ngay, theo thứ tự ưu tiên | mọi vai trò |
| 2 | Chi tiết SKU | `/sku/:sku` | Đường bán, tồn, dự báo, chiến dịch, lịch sử quyết định | mọi vai trò |
| 3 | So sánh lựa chọn | `/sku/:sku/compare`, `/campaign/:id/compare` | 3 tầng: kết luận, lý do, chi tiết; tạo quyết định | operator, lead |
| 4 | Nhập dữ liệu | `/import` | Wizard tải file, ghép cột, ghép SKU, xem lỗi | operator |
| 5 | Cảnh báo | `/alerts` | Danh sách cảnh báo gom theo tình huống, kết quả điều tra | mọi vai trò |
| 6 | Nhật ký quyết định | `/decisions` | Duyệt, theo dõi, xem lại; ghi chú bàn giao | mọi vai trò |
| 7 | Trợ lý AI | panel bên phải, mở ở mọi màn (`Ctrl+K`) | Hỏi what-if, hỏi "vì sao" | operator, lead |
| 8 | Cài đặt | `/settings` | Mục tiêu, tham số, phiếu điểm model, điều khiển demo | lead (sửa), khác (xem) |

Khung chung: thanh trên cùng (tên shop, đồng hồ dữ liệu, huy hiệu độ mới, số cảnh báo, người dùng và vai trò), menu trái thu gọn được, panel Trợ lý bên phải.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ MegaDay Twin · Shop Gia Dụng Demo   Dữ liệu tới 14:59  [Đơn ●][Tồn ●][Ads ◐] │
│                                     Siêu sale 11.11    [Cảnh báo 3]  Lan · Vận hành │
├────────┬─────────────────────────────────────────────────────┬───────────────┤
│ Tổng   │                                                     │ Trợ lý        │
│ quan   │                 (nội dung màn hình)                 │ (thu gọn)     │
│ Cảnh   │                                                     │               │
│ báo    │                                                     │               │
│ Nhật ký│                                                     │               │
│ Nhập   │                                                     │               │
│ Cài đặt│                                                     │               │
└────────┴─────────────────────────────────────────────────────┴───────────────┘
```

Icon dùng SVG từ `lucide-react`, không dùng emoji.

---

## 2. Wireframe từng màn

### 2.1 Tổng quan ca

```
Cần xem ngay (5)                                          Sắp xếp: [Ưu tiên ▾]  Lọc: [Có cảnh báo]
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ ● Nồi chiên không dầu mini 2L     Còn 76 sp · hết khoảng 13:00   Đề xuất: Chỉ chạy khung FS   │
│   CD002 · Đang chạy                [Tham khảo]                    Lợi hơn ~4,3 tr  [So sánh →]│
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ ● Áo giữ nhiệt nam (3 phân loại)  Bán thấp hơn dự báo 3 giờ       Size M hết hàng từ 12:00    │
│   CD005 · Đang chạy                [Đã kiểm]                      [Xem điều tra →]           │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ ● Bình nước giữ nhiệt 500ml       Bán gấp 6 lần dự báo            Có thể hết hàng tối nay     │
│   CD001 · Đang chạy                [Kịch bản]                     [So sánh →]                │
└──────────────────────────────────────────────────────────────────────────────────────────┘

Toàn bộ SKU (300)   [Tìm SKU...]
┌──────────────┬────────┬───────────┬──────────┬───────────────┬───────────┬──────────┐
│ SKU          │ Tồn    │ Đủ bán    │ Bán hôm  │ Đề xuất       │ Tin cậy   │ Cập nhật │
│              │        │ (giờ)     │ nay      │               │           │          │
├──────────────┼────────┼───────────┼──────────┼───────────────┼───────────┼──────────┤
│ NOICHIEN2L   │ 76     │ 12        │ 7        │ Chỉ khung FS  │ Tham khảo │ 01:02    │
│ ...          │        │           │          │               │           │          │
└──────────────┴────────┴───────────┴──────────┴───────────────┴───────────┴──────────┘
```

- Khối "Cần xem ngay" = top 5 theo `priority_score`, chỉ SKU có cảnh báo mở hoặc `zone` khác `continue_better`.
- Nhóm theo sản phẩm khi nhiều biến thể cùng có vấn đề (áo giữ nhiệt gom 3 phân loại).
- Dòng đang tính lại: chữ mờ + nhãn "Đang cập nhật".

### 2.2 Chi tiết SKU

```
← Tổng quan   Nồi chiên không dầu mini 2L · NOICHIEN2L       Nhóm: Bán ổn định (tự nhận) [Sửa]
┌───────────────────────────────────────────────┬──────────────────────────────────────────┐
│ Bán theo giờ, 7 ngày + 24 giờ tới              │ Tồn khả dụng   41 sp                     │
│  ▁▂▃▅▇▆▄▃▂▁ ... ▁▂▅█▇▅│░░▒▒▓▓▒▒░░ (dự báo p10–p90)│ Đang về        400 sp · 12/11 10:00      │
│  ■ bán  ▨ hết hàng  ░ khoảng dự báo            │ Giá hôm nay    531.000đ (niêm yết 690.000đ)│
│  ── chi ads (trục phải)                        │ Combo dùng tồn COMBO-NOICHIEN            │
│  [7 ngày][30 ngày][120 ngày]  [Theo ngày]      │ Chiến dịch     CD002 · Đang chạy         │
│                                               │   chung với GIAYLOT50, COMBO-NOICHIEN    │
│                                               │ Cam kết sale   Không tắt 12:00–14:00     │
├───────────────────────────────────────────────┴──────────────────────────────────────────┤
│ Phiếu điểm dự báo: seasonal_hourly v2026-11-11.1 · sai số 24% · khoảng 80% chứa 81%      │
│ [Đã kiểm] trên đợt 10.10                                                                 │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ Vấn đề dữ liệu (0)    Quyết định gần đây (2)    Cảnh báo (1)                              │
└──────────────────────────────────────────────────────────────────────────────────────────┘
                                                              [So sánh lựa chọn →]
```

Biểu đồ: vẽ SVG tay hoặc Recharts. Giờ hết hàng tô sọc để người xem hiểu "số 0 vì hết hàng, không phải vì không ai mua".

### 2.3 So sánh lựa chọn (3 tầng)

Ca chính là CD002 ngày 11.11 (05 mục 10). Số trong wireframe là minh họa theo lập luận đó.

```
← Nồi chiên mini 2L   So sánh lựa chọn cho chiến dịch CD002 (Nồi chiên + giấy lót + combo)
Mục tiêu: Lợi nhuận trong ngày 11.11 [Đổi]        Dữ liệu tới 01:00 · tính lúc 01:02 [Tham khảo]

TẦNG 1 ─────────────────────────────────────────────────────────────────────────────────
  Chỉ chạy ads trong khung flash sale (0–2h, 12–14h). Lợi hơn chạy cả ngày khoảng 4,3 triệu đ.

  Tiếp tục tốt hơn ◀━━━━━━━━┃━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━▶ Pause tốt hơn
                       ngưỡng 3,1     [=====●=====]   ← lượng bán/giờ nếu pause 4,2–7,0
  Pause tốt hơn tiếp tục, nhưng không được tắt hẳn vì cam kết flash sale.
  Phương án tốt hơn cả hai: Chỉ chạy khung flash sale.

  ┌───────────────┬───────────────┬───────────────┬───────────────┬───────────────┐
  │ Tiếp tục      │ Pause         │ Giảm 30%      │ Chỉ khung FS ★│ Pause tới khi │
  │               │ Không làm được│               │               │ hàng về       │
  │ −4,5 tr       │ vi phạm cam   │ −3,1 tr       │ −0,2 tr       │ Không làm được│
  │ (−4,9 … −4,1) │ kết flash sale│ (−3,5 … −2,7) │ (−0,4 … −0,05)│ vi phạm cam kết│
  │ Hết hàng 09:00│               │ Hết hàng 10:00│ Hết hàng 13:00│               │
  └───────────────┴───────────────┴───────────────┴───────────────┴───────────────┘
                                                    [Lưu bản nháp quyết định] [Gửi duyệt]

TẦNG 2 (bấm "Vì sao?") ──────────────────────────────────────────────────────────────────
  • Hàng sẽ hết khoảng 13:00: ads không bán thêm được khi hết hàng [chip: Mô phỏng scn-2291]
  • Ở giá 11.11 mỗi nồi chỉ lãi 9.280đ [chip: Đóng góp NOICHIEN2L]
  • Không được tắt hẳn vì đã cam kết với sàn trong khung flash sale [chip: Chương trình 11.11]
  Điều làm đổi kết luận: nếu hàng về trong hôm nay, chạy tiếp có lợi hơn.
  Thử: Hàng về sớm [——●————] 0 ngày        (kéo để tính lại)

TẦNG 3 (bấm "Chi tiết kiểm tra") ─────────────────────────────────────────────────────────
  Dữ liệu: Đơn tới 01:00 · Tồn tới 01:00 · Ads tới 23:00 hôm trước (ước lượng từ đó)
  Model: dự báo theo nhóm (03) · phiếu điểm 10.10 · nhãn Đã kiểm
  iROAS: 04 · khoảng p10–p90 · nhãn Tham khảo
  Tham số: param_version 12 · mục tiêu goal-cd002-1111 (người xác nhận: Hà)
  Mô phỏng: 1.000 mẫu · seed 20261111 · khóa cache (CD002, fc-…, ie-37, 12, goal-…, 01:00)
```

Màn này cũng phải chạy đúng **ví dụ trang 10** (120 sp, 4 giờ, 45.000đ/sp): ngưỡng 22,5 với khoảng 18–31 → "Chưa phân định"; kéo giá trị hàng dư lên 20.000đ → ngưỡng 16,5 → "Pause tốt hơn". Dùng làm story và test của ZoneBar (mục 4).

Quy tắc:
- Tầng 1 luôn hiện. Tầng 2, 3 thu gọn mặc định, nhớ trạng thái mở bằng `localStorage` (bọc try/catch).
- Hành động không làm được (vi phạm ràng buộc) vẫn hiện thẻ, xám, ghi lý do. Không ẩn.
- Kéo thanh tham số: trình duyệt cập nhật ZoneBar ngay bằng công thức hòa vốn đóng (05 mục 7, < 100 ms), đồng thời gọi lại `POST /scenarios/run` với `samples=300` (debounce 250 ms, < 400 ms) để cập nhật thẻ hành động. Thả tay → gọi lại với `samples=1000`.
- Khi `best` là `schedule_hours` hoặc `reduce_budget`: dưới thanh ghi "Phương án tốt hơn cả hai: …" (05 mục 7).
- `Recommendation.status = none_meets_goal` → thay tầng 1 bằng: "Các lựa chọn này chưa giải quyết được mục tiêu. Cần người có quyền xem phương án khác." và nút "Báo lead".

### 2.4 Nhập dữ liệu (wizard)

```
Bước 1 Tải file ─── 2 Ghép cột ─── 3 Ghép sản phẩm ─── 4 Kiểm tra ─── 5 Xong

┌──────────────────────────────────────────────────────────────┐
│   Kéo thả file vào đây hoặc [Chọn file]                       │
│   CSV, Excel, JSON, ảnh chụp bảng (PNG, JPG)                  │
└──────────────────────────────────────────────────────────────┘
 gia_von_nhap_tay.csv        Giá vốn · 283 dòng        ✓ Nhận diện 93%
 ton_kho_google_sheet.csv    Tồn kho · 35.880 dòng     ✓ Nhận diện 97%

Bước 2: Ghép cột — gia_von_nhap_tay.csv
 Cột trong file      →  Trường chuẩn          Cách đọc              Độ chắc
 "tên sp"            →  Tên sản phẩm (giá vốn)  giữ nguyên            95%
 "giá vốn"           →  Giá vốn                 đọc tiền VND          97%
 "phí đóng gói"      →  Phí đóng gói            đọc tiền VND          90%
 ⓘ Cột giá vốn có 4 kiểu viết (45000, 45.000, 45k, 45.000đ), đã chuẩn hóa.

Bước 3: Ghép sản phẩm   Tự ghép 266 · Cần bạn xem 17 · Chưa ghép 0
 Tên trong file                         Gợi ý                    Độ chắc
 binh nuoc giu nhiet 500ml den          BINHNUOC500-DEN ▾        86%  [Đúng]
 Bình nước GN 500ml - xanh              BINHNUOC500-XANH ▾       92%  ✓

Bước 4: Kiểm tra
 ✓ Đã bỏ 3 cột thông tin cá nhân người mua (tên, số điện thoại, địa chỉ)
 ⚠ 3 dòng tồn âm, đã gắn cờ và bỏ qua
 ⚠ 17 sản phẩm chưa có giá vốn: chưa tính được lợi nhuận cho các mã này
 ⓘ File ads ghi giờ UTC, đã đổi về giờ Việt Nam (+7)
                                                               [Nạp dữ liệu]
```

### 2.5 Cảnh báo

```
Đang mở (3)   Đã xử lý   Tạm ẩn           Sắp xếp: [Ảnh hưởng (đ) ▾]
┌──────────────────────────────────────────────────────────────────────────────┐
│ ▌Cao  Áo giữ nhiệt bán thấp hơn dự báo 3 giờ liền          ảnh hưởng ~1,85 tr │
│       13:04 · AOGIUNHIET (3 phân loại)                                        │
│       Điều tra: Size M hết hàng từ 12:00; S và L bán đúng nhịp; ads vẫn chạy │
│       [Xem chi tiết] [So sánh CD005] [Đã xem] [Ẩn 1 giờ]                       │
├──────────────────────────────────────────────────────────────────────────────┤
│ ▌Cao  Bình nước bán gấp 6 lần dự báo, có thể hết hàng tối nay                 │
│ ▌TB   Báo cáo ads trễ hơn 2 giờ                                               │
└──────────────────────────────────────────────────────────────────────────────┘
Đã ẩn 4 cảnh báo mức thấp trong ca này. [Xem]
```

Quy tắc cảnh báo (AlertEvaluator, ORC-05):

| `kind` | Điều kiện bật | Mức |
|---|---|---|
| `stockout_risk` | `hours_of_cover.p50 < 6` và còn chi ads | Cao |
| `sales_below_forecast` | tổng bán 3 giờ < tổng p10 3 giờ | Cao nếu ảnh hưởng > 1 tr, còn lại TB |
| `sales_above_forecast` | tổng bán 3 giờ > tổng p90 3 giờ | Cao nếu có nguy cơ hết hàng |
| `budget_exhausted` | chiến dịch "Hết ngân sách" trước 20:00 | TB |
| `data_stale` | nguồn vượt ngưỡng stale | TB |
| `data_blocking` | issue `blocking` chạm SKU có ads | Cao |
| `recommendation_changed` | `zone` đổi | TB |
| `compute_failed` | stage lỗi 3 lần | Cao |
| `drift` | nhận `DriftDetected` (CUSUM, 03-lop-mo-hinh); P1, mức sàn dùng `sales_below_forecast`/`sales_above_forecast` | TB |

Ngân sách cảnh báo: tối đa 8 cảnh báo mở hiển thị mỗi ca. Phần còn lại theo ảnh hưởng (đ) vào "Đã ẩn". Gom theo `group_key` (sản phẩm hoặc chiến dịch).

### 2.6 Nhật ký quyết định

```
[Chờ duyệt (1)] [Đã duyệt] [Đã gửi] [Tất cả]          [Tạo ghi chú bàn giao]
┌───────┬────────────┬──────────────────────┬──────────────┬──────────┬────────────────┐
│ Giờ   │ Đối tượng  │ Hành động            │ Trạng thái   │ Người    │                │
├───────┼────────────┼──────────────────────┼──────────────┼──────────┼────────────────┤
│ 15:04 │ CD002      │ Giảm ngân sách 30%   │ Chờ duyệt    │ Lan      │ [Duyệt][Từ chối]│
│ 13:20 │ CD005      │ Giảm ngân sách 50%   │ Đã kiểm lại ✓│ Lan → Hà │ [Xem]          │
└───────┴────────────┴──────────────────────┴──────────────┴──────────┴────────────────┘
Chi tiết: phương án, lý do, mục tiêu lúc chọn, dữ liệu tới, model, tham số, kết quả so với thực tế sau ca.
"Thao tác mô phỏng — chưa gửi lên sàn" luôn hiện với hành động chế độ simulated.
```

Lead không được duyệt quyết định do chính mình tạo (nút xám, tooltip giải thích).

### 2.7 Trợ lý AI

```
┌ Trợ lý ─────────────────────────────── [×] ┐
│ Đang xem: CD002 · Nồi chiên mini 2L         │
│                                             │
│ Bạn: Nếu hàng về sớm 1 ngày thì sao?        │
│                                             │
│                                             │
│ ⋯ Đang xem tồn kho NOICHIEN2L               │
│ ⋯ Đang chạy mô phỏng                         │
│                                             │
│ Trợ lý: Nếu hàng về trong hôm nay thì chạy  │
│ tiếp cả ngày lợi hơn [Mô phỏng scn-2292].   │
│ Đây là giả định: hàng đang dự kiến về 12/11 │
│ [Tồn kho 01:00]                             │
│ Đã kiểm số 4/4 ✓   [Kịch bản giả định]      │
│ [Ghi chú cho lead]                          │
│                                             │
│ Gợi ý: [Vì sao chưa phân định?]             │
│        [Nếu hàng dư còn 20.000đ thì sao?]   │
│ ┌─────────────────────────────────────────┐ │
│ │ Hỏi về SKU, chiến dịch, quyết định...    │ │
│ └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

- Gửi `context.sku`/`screen` theo màn đang mở.
- Hiện từng bước công cụ (event `tool_call`) dạng dòng "Đang xem…" để người xem thấy AI đi lấy số.
- "Đã kiểm số 6/6" bấm được → bảng đối chiếu số ↔ nguồn.
- `AI_ENABLED=false` hoặc lỗi 503 → panel hiện "Trợ lý đang ngoại tuyến. Mọi số liệu vẫn xem được ở các màn chính."

### 2.8 Cài đặt

Bốn tab:
1. **Mục tiêu**: mục tiêu shop mặc định + ghi đè theo SKU/chiến dịch. Form theo `kind` (08-api 3.7).
2. **Tham số**: bảng danh mục tham số (khóa, nhãn, giá trị, mặc định, khoảng, ai được sửa, nguồn). Ô ngoài khoảng → lỗi tại chỗ.
3. **Phiếu điểm model**: bảng model × nhóm nhu cầu × đợt backtest, chỉ ra champion.
4. **Demo** (chỉ khi `DEMO_MODE=true`): nút "Nạp sẵn lịch sử", "Bắt đầu phát lại 11.11 từ [9]h, mỗi giờ [10] giây", "Dừng", "Đặt lại", chọn `AI_MODE`.

---

## 3. Component tree

```
App
├─ AppShell
│  ├─ TopBar ── ShopName, DataClock, FreshnessBadges, AlertBell, UserMenu
│  ├─ SideNav
│  ├─ <Routes/>
│  └─ AssistantPanel ── ChatThread, ToolStepList, AnswerBubble(GroundingBadge, CitationChip*), ChatInput
├─ pages/
│  ├─ ShiftOverviewPage ── NeedsAttentionList(SkuAttentionCard*), SkuTable
│  ├─ SkuDetailPage ── SalesForecastChart, StockPanel, ScoreCardStrip, Tabs(Issues, Decisions, Alerts)
│  ├─ ComparePage ── GoalBar, Tier1(ZoneBar, ActionCards(ActionCard*), DecisionButtons),
│  │                 Tier2(ReasonList(CitationChip*), FlipCondition, ParamSlider),
│  │                 Tier3(DataLineage, ModelInfo, ParamTable)
│  ├─ ImportWizardPage ── Dropzone, ColumnMappingTable, SkuMatchTable, QualityReview
│  ├─ AlertsPage ── AlertCard*(InvestigationSummary)
│  ├─ DecisionsPage ── DecisionTable, DecisionDrawer, HandoffDraft
│  └─ SettingsPage ── GoalsForm, ParametersTable, ScoresTable, DemoControls
└─ components/ dùng chung
   ConfidenceBadge, FreshnessBadge, ComputeStateTag, CitationChip, Money, Units, Range, TimeVN, EmptyState, ErrorState
```

`lib/`: `format.ts` (tiền, số, giờ Việt Nam), `breakeven.ts` (tính nhanh trên trình duyệt), `sse.ts` (EventSource + map invalidate), `queryKeys.ts`.

Query keys (TanStack Query): `['skus', filters]`, `['sku', sku]`, `['panel', sku, range]`, `['forecast', sku, action, h]`, `['scenario', id]`, `['alerts', status]`, `['decisions', filter]`, `['freshness']`, `['parameters', scope]`. SSE event → `invalidateQueries` theo bảng ở 08-api mục 4.

---

## 4. Thành phần hiển thị bắt buộc

### Nhãn tin cậy (`ConfidenceBadge`)

| Giá trị | Chữ | Kiểu | Tooltip |
|---|---|---|---|
| `verified` | Đã kiểm | viền + nền xanh lá nhạt, chấm đặc | "Dự báo đã được kiểm trên dữ liệu cũ và đạt ngưỡng sai số" |
| `reference` | Tham khảo | viền vàng, chấm nửa | "Đã kiểm nhưng chưa đạt ngưỡng, hoặc bối cảnh mới" |
| `scenario` | Kịch bản | viền xám nét đứt, chấm rỗng | "Chưa đủ dữ liệu, kết quả dựa trên giả định" |

Không chỉ dùng màu: luôn có chữ và hình chấm khác nhau (đặc, nửa, rỗng) cho người mù màu.

### Huy hiệu độ mới (`FreshnessBadge`)

`[Đơn ●]` xanh = fresh, `[Ads ◐]` vàng = trễ nhưng trong ngưỡng hoặc partial, `[Tồn ○]` đỏ = stale/missing. Hover: "Ads tới 12:59, trễ 2 giờ 4 phút (ngưỡng 3 giờ)".

### Chip nguồn (`CitationChip`)

Chip nhỏ cạnh số, chữ = nhãn ngắn ("Mô phỏng 15:01", "Tồn kho 14:59", "iROAS CD002"). Bấm mở popover: loại nguồn, id, `data_as_of`, link tới màn chứa nguồn (tầng 3 hoặc Chi tiết SKU).

### Thanh vùng (`ZoneBar`)

Props lấy thẳng từ schema `ZoneBar` (05 mục 7), sinh type từ OpenAPI:

```ts
type ZoneBar = {
  axis_label: string;                       // "Lượng bán mỗi giờ nếu pause (sp)"
  threshold: number;                        // 22.5
  interval: [number, number, number];       // p10, p50, p90: [18, 24, 31]
  verdict: 'pause_better' | 'continue_better' | 'undetermined';
  alt_thresholds: { label: string; threshold: number }[];   // [{label: 'hàng dư 20.000đ', threshold: 16.5}]
};
type ZoneBarProps = { bar: ZoneBar; domain?: [number, number] };   // domain mặc định [0, max(p90, threshold) × 1,4]
```

- Trục ngang = lượng bán mỗi giờ nếu pause. Trái ngưỡng = "Tiếp tục tốt hơn", phải = "Pause tốt hơn".
- Ngưỡng chính vạch liền, ngưỡng thay thế vạch đứt.
- Khoảng p10–p90 là thanh dày, p50 là chấm tròn.
- Câu kết luận dưới thanh lấy từ `verdict`:
  - `undetermined`: "Chưa phân định: khoảng dự báo nằm cả hai phía ngưỡng {threshold} sp/giờ."
  - `pause_better`: "Pause tốt hơn: cả khoảng dự báo nằm trên ngưỡng."
  - `continue_better`: "Tiếp tục tốt hơn: cả khoảng dự báo nằm dưới ngưỡng."
- `aria-label` đọc đủ câu kết luận và ba con số.

Ví dụ kiểm tra (dùng làm story Storybook hoặc test ảnh): ngưỡng 22,5 với dự báo 18–31 → `undetermined`; ngưỡng 16,5 (hàng dư 20.000đ) với cùng dự báo → `pause_better`.

### Trạng thái màn hình

| Trạng thái | Hiện gì |
|---|---|
| Đang tải lần đầu | skeleton đúng hình bố cục, không spinner giữa trang |
| Có dữ liệu cũ, đang tính lại | dữ liệu cũ mờ 60% + `ComputeStateTag` "Đang cập nhật · dữ liệu lúc 13:05" |
| Dữ liệu cũ vượt ngưỡng | băng vàng trên cùng: "Dữ liệu tồn kho cũ hơn 1 giờ. Kết quả có thể không còn đúng." |
| Kịch bản | băng xám: "Kết quả dựa trên giả định, chưa phải dự báo đã kiểm." |
| Thiếu dữ liệu chặn | `EmptyState`: "Chưa tính được lợi nhuận vì thiếu giá vốn của 3 mã. [Nhập giá vốn]" |
| Lỗi API | `ErrorState`: nói lỗi gì, làm gì tiếp, nút "Thử lại". Không xin lỗi chung chung |
| Không có gì cần xem | "Không có SKU nào cần xem ngay. Lần tính gần nhất lúc 15:01." |

---

## 5. Copy tiếng Việt chuẩn

| Khái niệm | Dùng | Tránh |
|---|---|---|
| p10–p90 | khoảng dự báo | p10, p90, quantile |
| p50 | mức giữa | median |
| iROAS | hiệu quả thật của ads | iROAS (chỉ ở tầng 3) |
| ROAS sàn | ROAS sàn báo | — |
| horizon | trong N giờ tới | horizon |
| stockout | hết hàng | stockout |
| pause | tạm dừng ads (nút: "Tạm dừng") | tắt hẳn |
| reduce_budget | giảm ngân sách {x}% | — |
| schedule_hours | chỉ chạy {giờ} | — |
| pause_until_restock | tạm dừng tới khi hàng về | — |
| verified/reference/scenario | Đã kiểm / Tham khảo / Kịch bản | — |
| Nút | động từ cụ thể: "Lưu bản nháp", "Gửi duyệt", "Duyệt", "Từ chối", "Nạp dữ liệu" | "OK", "Submit" |

Định dạng: tiền `531.000đ`, triệu `10,1 tr` trong thẻ hẹp và `10,1 triệu` trong câu; số thập phân dấu phẩy `22,5`; giờ `19:00`; ngày `12/11`. Mọi chữ số trong bảng dùng `font-variant-numeric: tabular-nums`.

---

## 6. Responsive và accessibility

- Ưu tiên laptop 1280–1440 px (màn hình demo). Tối thiểu dùng được ở 1024 px.
- Dưới 768 px: menu trái thành thanh dưới, Trợ lý thành trang riêng, bảng SKU thành danh sách thẻ. Không cần tối ưu sâu cho điện thoại trong hackathon.
- Bàn phím: mọi nút với tới bằng Tab, focus có viền rõ; `Ctrl+K` mở Trợ lý; `Esc` đóng panel/drawer.
- Tương phản chữ ≥ 4.5:1 cả nền sáng và tối. Hỗ trợ dark mode bằng biến CSS (Tailwind `dark:`).
- Biểu đồ và thanh vùng có `role="img"` + `aria-label` chứa kết luận.
- Tôn trọng `prefers-reduced-motion`: tắt nhấp nháy khi đề xuất đổi.

---

## Tiêu chí hoàn thành

- [ ] 8 màn hình chạy với dữ liệu demo, đúng wireframe về nội dung (bố cục được tinh chỉnh).
- [ ] Mọi con số hiện `FreshnessBadge`/`ComputeStateTag` và `ConfidenceBadge` khi API trả.
- [ ] `ZoneBar` đúng 2 ví dụ (22,5 → chưa phân định; 16,5 → pause tốt hơn); thanh trượt tính lại < 300 ms.
- [ ] Hành động vi phạm cam kết hiện xám kèm lý do.
- [ ] SSE cập nhật Tổng quan ca, Cảnh báo, Nhật ký mà không cần tải lại trang.
- [ ] Wizard nhập dữ liệu chạy hết 5 bước với 2 file demo trong < 45 giây thao tác.
- [ ] Trợ lý hiện bước công cụ, chip nguồn, "Đã kiểm số", và chạy được khi AI ngoại tuyến.
- [ ] Kiểm bàn phím và tương phản trên 3 màn chính.

## Task gợi ý

Người, ước lượng và ưu tiên theo 12-lo-trinh-va-phan-viec. Khởi tạo `web/` và sinh type OpenAPI là INF-05.

| Mã | Việc | Người | Người-ngày | Ưu tiên | Phụ thuộc |
|---|---|---|---|---|---|
| WEB-01 | Router + AppShell + TanStack Query + fixture JSON | D4 | 0,5 | P0 | INF-05 |
| WEB-02 | Thành phần dùng chung (badge, chip, format, trạng thái) | D4 | 0,75 | P0 | WEB-01 |
| WEB-03 | Tổng quan ca | D4 | 0,75 | P0 | WEB-02, API-04 |
| WEB-04 | Chi tiết SKU + biểu đồ bán/dự báo/hết hàng | D4 | 1 | P1 | WEB-02, API-04 |
| WEB-05 | So sánh 3 tầng + ZoneBar + thanh trượt (samples=300) | D4 | 1,5 | P0 | WEB-02, API-05 |
| WEB-06 | Wizard nhập dữ liệu (gồm hàng chờ SKU, khai báo combo) | D4 | 1 | P0 | API-03 |
| WEB-07 | Cảnh báo + Nhật ký quyết định + luồng duyệt | D4 | 0,75 | P0 | API-06 |
| WEB-08 | Panel Trợ lý | D4 | 0,75 | P0 | API-07 |
| WEB-09 | Cài đặt (mục tiêu, tham số, phiếu điểm, điều khiển demo) | D4 | 1 | P1 | API-05, API-07 |
| WEB-10 | SSE client + invalidate + dark mode + kiểm a11y | D4 | 0,5 | P0 | API-07 |
