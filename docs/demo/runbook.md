# Runbook demo MegaDay Twin (pitch 6 phút)

Dùng khi đứng pitch. Kịch bản lời nói đầy đủ ở [Cẩm nang pitch](https://claude.ai/artifact/NuSEaHjNHnzrhKknD4smWU), mục "Kịch bản trình bày 6 phút". File này chỉ ghi: lệnh, màn hình/endpoint, con số đúng, câu cấm, xử lý sự cố. Mọi số là **trên dữ liệu mô phỏng** (shop `demo`, seed 20261111).

Phân vai: một người nói, một người bấm. Người bấm không nói; người nói không chạm máy.

> **Phụ thuộc giao diện (chưa chốt):** giao diện cuối có thể là `web/` của repo này hoặc app "city" của Phúc. Runbook viết theo API + `web/` hiện có. Hiện `web/` mới có thật màn **Tổng quan ca** (`/`) và **So sánh lựa chọn** (`/campaign/CD002/compare`); Nhập dữ liệu, Cảnh báo, Trợ lý AI, Nhật ký còn là trang chờ. Cảnh nào ghi "[UI?]" thì khi chốt giao diện phải điền lại nút bấm; trước đó dùng endpoint đã ghi.

## 1. Chuẩn bị trước giờ pitch (T−1 ngày hoặc sáng hôm đó)

Máy demo đã `uv sync` trong `backend/`, `npm ci` trong `web/`. Không cần sửa `backend/.env`: mọi biến demo được `demo.mjs` truyền thẳng cho tiến trình con.

1. **Một lệnh dựng DB + bật demo** (ở gốc repo):

   ```powershell
   node scripts/demo.mjs
   ```

   Script làm lần lượt và in ĐẠT/LỖI từng bước:
   - dựng DB **đầy đủ** mới `backend/data/pitch-<thời gian>/demo.duckdb` (không bao giờ ghi đè): lịch sử + 11.11 tới 00:59 **qua đúng thư mục phát lại**, rồi **backtest + fit + ước lượng iROAS** (`app.demo_import --replay-hour 0 --models`). Đo 31/10: **~3,5 phút** (máy bận có thể 5–10 phút); script in từng bước (`step 1/3: backtest...`) và mỗi 30 giây "đang dựng DB, … s". Đây đúng là hệ thống đo các số pitch (WAPE, nhãn 44 verified…) và 3 bản ghi AI ("Phương án A" trong [M5-chuan-bi-demo.md](../quyet-dinh/M5-chuan-bi-demo.md));
   - bật backend với `APP_ENV=demo`, `AI_ENABLED=true`, `AI_MODE=replay`, `DEMO_MODE=true`, `LIVE_ENABLED=true`, `LIVE_DIR=<thư mục DB>/live_stream` (riêng, không lẫn `live_stream/` cũ của `replay.js`);
   - lead-1 đặt mục tiêu profit CD002 (hạn 24:00 ngày 11.11) nếu chưa có;
   - làm nóng màn So sánh CD002 (`POST /scenarios/run`, lần đầu ~10–25 giây; phải ra `schedule_hours:0-2,12-14`, hơn chạy tiếp 3,97 tr);
   - bật web trỏ proxy vào backend; in URL, lệnh nhảy giờ, 3 câu AI.

   Lần sau dùng lại DB đã dựng: `node scripts/demo.mjs --db backend/data/pitch-<thời gian>/demo.duckdb`. Cổng khác: `--api-port 8001 --web-port 5174`. Bỏ bước: `--skip-goal`, `--skip-warm`, `--no-web`.

   Cần nhanh để thử giao diện: `node scripts/demo.mjs --light` (không backtest/fit, ~1–3 phút). Script in **CẢNH BÁO "bản nhẹ, số khác pitch"**: số what-if khác, bản ghi AI có thể ra "ngoại tuyến". **Không dùng bản nhẹ để pitch.**

2. **Diễn tập tự động** (mục 6) trên server vừa bật: `cd backend; uv run python -m eval.rehearsal --base-url http://localhost:8000`. Hai lượt phải ĐẠT hết. Diễn tập kết thúc ở 01:00.

3. **Kiểm con số pitch** (một lần, đo 31/10 ~6 phút; máy bận 10–20 phút), trên bản sao DB pitch:

   ```powershell
   cd backend
   uv run python -m eval.pitch_numbers --reuse-db data/pitch-<thời gian>/demo.duckdb
   ```

   Mọi dòng phải `ĐẠT`. Dòng `LỆCH` thì **không đọc số đó trên sân khấu** cho tới khi rõ lý do. Bảng lưu ở `eval/reports/pitch_numbers.json`. Script chép DB sang thư mục tạm, không ghi vào file gốc. Không có `--reuse-db` thì script tự dựng DB đầy đủ như `demo.mjs` (thêm ~3–5 phút). Chạy nhanh khi chỉ đổi what-if: thêm `--skip data,forecast,ads`. Dòng thời gian `/scenarios/run` phụ thuộc tải máy.

4. **AI chạy bản ghi (không cần mạng, không cần key)**: `demo.mjs` đã đặt `AI_MODE=replay`. Có sẵn 3 bản ghi trong `backend/eval/ai_recordings/`. **Gõ đúng nguyên văn câu hỏi**, đúng `flow`, đúng mốc dữ liệu:

   | `flow` | Câu hỏi (gõ đúng) | Mốc dữ liệu cần | Kết quả đúng |
   |---|---|---|---|
   | `whatif` | Nếu sếp giao chỉ tiêu 97 cái hôm nay thì sao? | 01:00 ngày 11.11, mục tiêu profit CD002 | **không phương án nào đạt chắc 80%**; cao nhất là chạy tiếp 70% (khung giờ 50%); chạy tiếp lời kém khung giờ ~3,97 triệu; gợi ý hạ chỉ tiêu hoặc chấp nhận rủi ro. Kiểm số 17/17 |
   | `explain` | Vì sao nồi chiên chỉ chạy khung giờ? | 01:00 | 3 lý do + điều làm đổi kết luận, 9/9 |
   | `investigate` | Vì sao áo giữ nhiệt bán chậm? | **14:00** (`node scripts/demo.mjs jump 13`) | AOGIUNHIET-M hết hàng từ 13:00, 10/10 |

   Các bản ghi này tạo bằng **FakeClient** (kịch bản cố định, công cụ và kiểm số chạy thật), **chưa chạy OpenAI thật**. Chúng được ghi trên DB **đầy đủ** dựng như `demo.mjs` mặc định (31/10, phương án A). DB bản nhẹ (`demo.mjs --light`) hoặc DB kiểu cũ (`demo_import --until …`) cho số khác, nên `explain`/`whatif` có thể ra "ngoại tuyến". Nếu đội có key và muốn bản thật: chạy backend với biến môi trường `AI_MODE=record` trên đúng DB demo, hỏi 3 câu trên đúng mốc, kiểm file mới trong `eval/ai_recordings/<flow>/`, rồi chạy lại `node scripts/demo.mjs --db …` (replay).

5. **Quay video dự phòng** toàn bộ demo theo đúng kịch bản, lưu ở máy demo + một USB.

## 2. Từng cảnh

Mốc giờ theo kịch bản 6 phút. Màn hình hiện tại = `web/` trừ khi ghi khác.

| Mốc | Cảnh | Màn / endpoint | Con số đúng (đọc được) |
|---|---|---|---|
| 0:00–1:45 | Mở đầu, Vấn đề, Giải pháp | Slide | Nồi chiên: ROAS sàn **8,11**, iROAS thật **5,27**; áo **6,17 / 3,85**; cốc meme **0,88 / 0,64**; ROAS sàn phóng đại trung bình **~1,47 lần**. Nồi chiên đầu ngày còn **83** cái, hết khoảng **15 giờ** (đáp án mô phỏng) |
| 1:45–2:10 | K1: dữ liệu lộn xộn | [UI?] màn Nhập dữ liệu chưa có trong `web/`. Tạm: `POST /api/v1/ingest/files`, hoặc nói "đã nạp từ trước" và mở `GET /api/v1/data-quality/issues` | Ghép SKU tự động đúng **96,4%** (mã nội bộ + giá vốn), **97,6%** cả 3 nguồn, **0** ghép sai; bắt **12/12** lỗi cài sẵn; lệch giờ ads **+7 tiếng**; **0** PII trong DB |
| 2:10–2:40 | K2: phát lại 11.11, bình nước | Người bấm chạy `node scripts/demo.mjs jump 10` **trước cảnh** (dữ liệu tới 10:59, chờ ~20 giây–3 phút tùy tải máy, mục 3). Cảnh báo "Bình nước giữ nhiệt 500ml bán vượt dự báo 3 giờ liền": [UI?] màn Cảnh báo chưa có trong `web/`; tạm `GET /api/v1/alerts?status=open` hoặc dòng in ra của lệnh `jump` | Nói "**khoảng 11 giờ** app báo bình nước bán vượt dự báo". Nhu cầu 11.11 **514** cái so với ~**23** cái/ngày |
| 2:40–3:20 | K3: AI điều tra áo | [UI?] màn Trợ lý chưa có trong `web/`. API: `POST /api/v1/ai/chat` `flow=investigate` (hoặc `/ai/investigate`). **Cần dữ liệu tới 14:00**: `node scripts/demo.mjs jump 13`. Cảnh báo "Áo giữ nhiệt nam (Size M) đã hết hàng nhưng quảng cáo CD005 vẫn chạy" bật ở dữ liệu **13:59** | Size **M** hết hàng từ khoảng **13 giờ**; S/L vẫn bán; ads CD005 vẫn chạy; không kết luận "hết trend" |
| 3:20–4:15 | K4: nồi chiên | Trước cảnh: `node scripts/demo.mjs home` (về 01:00, ~10–60 giây). `web/` `/campaign/CD002/compare`; API `POST /api/v1/scenarios/run` | Đề xuất **chỉ chạy khung giờ 00–02h và 12–14h** (`recommend`, p ≈ 1,00). Hơn chạy tiếp **~3,97 triệu** (nói "khoảng 4 triệu"). Hơn giảm 50% **~1,22 triệu** (nói "khoảng 1,2 triệu"). Tạm dừng và tạm dừng tới khi hàng về **bị cấm** (cam kết flash). Lãi mỗi nồi giá 11.11 **9.280đ**; ads cần iROAS ≥ **57** mới có lời |
| 3:20–4:15 | K4 what-if: "chỉ tiêu 97 cái" | [UI?] Trợ lý; API `POST /api/v1/ai/chat` `flow=whatif`, câu ở mục 1.4 | **Không phương án nào đạt chắc 80%** (`none_meets_goal`). Cao nhất là **chạy tiếp cả ngày: 70%** (giảm 30%: 67%, giảm 50%: 64%, khung giờ: 50%; tạm dừng bị cấm). Cái giá: chạy tiếp lời kém khung giờ **~3,97 triệu** (nói "gần 4 triệu"). Câu nói: "Sếp giao 97 cái, hệ thống không chiều theo chỉ tiêu: không cách nào chắc đạt; cách có khả năng nhất là chạy ads cả ngày, cũng chỉ 70%, và mất gần 4 triệu lãi so với chỉ chạy khung giờ. Hạ chỉ tiêu hay chấp nhận rủi ro là quyết định của người, nhưng cái giá đã nằm trên bàn." |
| 4:15–4:45 | K5: bẫy lừa AI | [UI?] Trợ lý, `flow=chat` | Ghi chú ở đơn `2611099UF5GLZH` bị coi là dữ liệu khách; AI không làm theo; mọi thao tác cần người duyệt. **Chưa có bản ghi replay** cho câu này: tập trước, nếu AI ngoại tuyến thì bỏ cảnh (xem "khi trễ giờ") |
| 4:45–5:30 | Chứng minh | Slide | Dự báo 10.10: WAPE **~37%** so với cách đơn giản **68,6%**; phân nhóm đúng **88,3%**; nhãn tin cậy **44** đã kiểm / **242** tham khảo / **14** kịch bản. iROAS: sai số trung vị **~24,8%**, gần đúng hơn ROAS sàn ở **40/49** chiến dịch; nồi chiên app ước lượng **~4,4–4,5** (thật 5,27, sàn 8,11) |
| 5:30–6:00 | Chốt | Slide | — |

Câu dự phòng cho câu hỏi ban giám khảo (đều đã kiểm bằng `eval.pitch_numbers`):

- "Cam kết flash tốn bao nhiêu?" → bỏ ràng buộc thì app chọn **tạm dừng**; cam kết tốn khoảng **1,1 triệu**.
- "Hàng về sớm một ngày thì sao?" → **kết luận không đổi**, vẫn chỉ chạy khung giờ (hơn chạy tiếp ~3,46 triệu, nói "khoảng 3,5 triệu"): thêm hàng cũng không cứu được ads khi mỗi nồi chỉ lãi 9.280đ.
- "Có ví dụ app bảo dừng hẳn không?" → thanh vùng CD020 (đèn lồng đỏ) ngày 06/11, hàng dư V = 0: **"tạm dừng tốt hơn"** (ngưỡng ~0,57 sp/giờ, dự báo khi dừng ~0,58–1,25).
- "Sếp giao 97 cái thì app có tìm được cách không?" → **không**: không phương án nào đạt chắc 80%; cao nhất chạy tiếp 70%, đổi lại mất ~4 triệu lãi so với khung giờ (cảnh K4 what-if).
- "Tính có nhanh không?" → `/scenarios/run` khoảng **0,3 giây** khi dự báo đã có. Nạp lịch sử < **60 giây**, một giờ live < **5 giây** (phụ thuộc máy).

## 3. Phát lại 11.11 và cảnh báo

Lệnh chạy ở terminal thứ hai (gốc repo), thêm `--api-port` nếu đổi cổng. Mỗi lệnh gọi `POST /api/v1/live/control` với quyền lead (cần `DEMO_MODE=true`) và **chờ tính lại + cảnh báo xong** (`wait=true`) rồi in các cảnh báo đang mở.

| Việc | Lệnh | Ghi chú |
|---|---|---|
| Nhảy tới giờ H (dữ liệu tới H:59) | `node scripts/demo.mjs jump 10` | API: `{"command":"jump","hour":10,"wait":true}`. Một lần nhảy ~10–25 giây, tới ~3 phút khi máy bận (tính lại ~200 SKU): nhảy trước cảnh, không nhảy lúc đang nói |
| Về 01:00 (cảnh nồi chiên) | `node scripts/demo.mjs home` | `reset` + `jump 0`: đúng trạng thái lúc dựng DB |
| Phát lại tự động | `node scripts/demo.mjs replay 9 20` | từ 9 giờ, 20 giây mỗi giờ (`start`); dừng: `node scripts/demo.mjs stop`, dữ liệu đã nạp giữ nguyên |
| Xem trạng thái | `node scripts/demo.mjs status` hoặc `GET /api/v1/live/status` | SSE: `GET /api/v1/stream` |

Giờ cảnh báo đã đo (diễn tập `eval.rehearsal`):

- **Bình nước** `BINHNUOC500` "bán vượt dự báo 3 giờ liền" (`sales_above_forecast`): bật khi dữ liệu tới **10:59** (`jump 10`). Nói "khoảng 11 giờ".
- **Áo giữ nhiệt size M** "đã hết hàng nhưng quảng cáo CD005 vẫn chạy" (`stockout_risk`, mức cao): bật khi dữ liệu tới **13:59** (`jump 13`). Lúc 10:59 đã có "sắp hết".
- Màn hiện cảnh báo: [UI?] `web/` chưa có màn Cảnh báo (trang chờ). Tạm dùng `GET /api/v1/alerts?status=open` hoặc dòng in ra của `demo.mjs jump`.

`LIVE_ENABLED=true` và `LIVE_DIR=<thư mục DB>/live_stream` do `demo.mjs` đặt. Không chạy `node replay.js` của `mock-data` khi demo (nó ghi vào `mock-data/out/demo/live_stream`, server demo không đọc thư mục đó).

**Huy hiệu "Ads trễ" lúc 01:00–02:00** (người dùng chưa quyết, giữ nguyên dữ liệu): DB của `demo.mjs` đi đúng đường phát lại, nên lúc **01:00 và 02:00** Ads hiện "cũ hơn ngưỡng" (trễ 4 và 5 giờ, ngưỡng 3 giờ; 3 giờ ads cuối ngày 10/11 không bao giờ về, xem Q1 của [M5-chuan-bi-demo.md](../quyet-dinh/M5-chuan-bi-demo.md)). Từ **03:00** Ads trễ đúng **2 giờ**. Nếu bị hỏi: "Hệ thống tự báo khi số liệu quảng cáo về chậm; từ 03:00 trễ đúng 2 giờ." Đề xuất CD002 không đổi.

## 4. Câu KHÔNG được nói

- ✗ "Hàng về sớm một ngày thì kết luận đổi." Sai: **không đổi**.
- ✗ "Chỉ tiêu 97 cái thì giảm ngân sách 30% là đạt (81%)" / "cái giá 2,3 triệu". Đó là số cũ của DB không backtest/fit. Trên DB demo: **không phương án nào đạt chắc 80%**, giảm 30% chỉ 67%, cao nhất là chạy tiếp 70%, cái giá ~3,97 triệu.
- ✗ "Ngày thường app khuyên chạy tiếp ads nồi chiên." Sai: app ra giảm 50%, chưa đủ chắc (`undetermined`).
- ✗ "AI đã chạy bằng OpenAI thật" / "đây là GPT trả lời trực tiếp", **trừ khi** đội đã ghi lại bằng key thật (mục 1.4) và đang chạy bản đó. Bản ghi hiện có là kịch bản cố định; nói "câu trả lời ghi sẵn từ buổi diễn tập, công cụ và bước kiểm số chạy thật".
- ✗ Chỉ vào thanh vùng của nồi chiên 11.11 và nói "nên tạm dừng". Thanh ghi "tạm dừng tốt hơn nhưng bị cấm": đó là cái giá của cam kết, không phải đề xuất.
- ✗ Đọc E[J] tuyệt đối ("lời X triệu"). Chỉ đọc **chênh lệch** giữa hai lựa chọn.
- ✗ "App giúp shop tăng X% lợi nhuận", "đã thử với seller", đọc số mô phỏng như số thị trường.
- ✗ "App đề xuất dừng cốc meme chắc chắn." Nói: "app nghiêng về dừng, chênh nhỏ vì chiến dịch nhỏ".
- ✗ "Nồi chiên còn 83 cái" khi đang ở màn 01:00: lúc đó app thấy tồn **76** (đã bán trong giờ đầu). 83 là tồn **đầu ngày** trên slide.

## 5. Xử lý sự cố

| Sự cố | Làm gì |
|---|---|
| Backend chết | Người nói chuyển sang phần slide. Người bấm: Ctrl+C rồi chạy lại `node scripts/demo.mjs --db backend/data/pitch-<thời gian>/demo.duckdb` (~20–30 giây; DB còn nguyên, dữ liệu ở đúng giờ đã nhảy). Không dùng `dev.mjs` cho demo (đọc `.env`, AI có thể không ở replay) |
| Cổng 8000 hoặc 5173 bận | `node scripts/demo.mjs --db … --api-port 8001 --web-port 5174` (và `--api-port 8001` cho mọi lệnh `jump/home`). Tìm tiến trình giữ cổng: `Get-NetTCPConnection -LocalPort 8000` |
| DuckDB báo file đang bị khóa | Còn một server/script khác mở cùng file. Tắt nó, hoặc dựng thư mục DB mới (mục 1.1) |
| Mất mạng / OpenAI lỗi | `demo.mjs` luôn chạy `AI_MODE=replay`, không ảnh hưởng. Nếu đang chạy bản `live`/`record` tự bật: tắt, chạy lại `node scripts/demo.mjs --db …`. Câu không có bản ghi → nói "trợ lý đang ngoại tuyến", đi tiếp màn chính. Riêng `investigate` không bao giờ lỗi 503: không có AI thì hiện "Tóm tắt tự động từ checklist" |
| AI trả lời chậm > 5 giây | Người bấm mở câu trả lời ghi sẵn; người nói nói tiếp, không chờ |
| Replay không khớp, AI ra `gave_up` / "ngoại tuyến" | Dữ liệu khác mốc của bản ghi (vd hỏi `investigate` ở 01:00, `explain` sau `jump 13`) hoặc DB bản nhẹ (`--light`) / DB kiểu cũ. Về đúng mốc (`home` cho 01:00, `jump 13` cho 14:00), hoặc dựng lại DB đầy đủ (`node scripts/demo.mjs`), hoặc bỏ cảnh |
| `jump` lâu > 3 phút | Máy đang tải. Người nói kể tiếp bằng slide; không bấm lại lệnh (lệnh mới dừng lệnh cũ và nạp lại) |
| Máy demo hỏng | Chiếu video dự phòng từ mốc đang dở, lời nói giữ nguyên |
| Trễ giờ | Trễ > 20 giây ở mốc 2:40: bỏ K5. Trễ > 40 giây: bỏ thêm K1 ("dữ liệu đã nạp và làm sạch từ trước"). **Không bao giờ bỏ K3 và K4** |

## 6. Diễn tập

Chạy trên server demo đang bật, hoặc tự dựng app trong process trên bản sao DB:

```powershell
cd backend
uv run python -m eval.rehearsal --base-url http://localhost:8000       # server của demo.mjs
uv run python -m eval.rehearsal --db data/pitch-<thời gian>/demo.duckdb # không cần server
```

Hai lượt liên tiếp; mỗi lượt chạy đúng thứ tự cảnh và kết thúc bằng `reset` + `jump 0`. In bảng ĐẠT/LỖI + thời gian, ghi `eval/reports/rehearsal.json`, thoát mã 1 nếu có LỖI. Các bước: (a) độ mới 01:00 + Tổng quan ca; (b) CD002 khung giờ 0–2, 12–14, hơn chạy tiếp ~3,97 tr, tạm dừng bị cấm, có ZoneBar; (c) AI replay `explain` + `whatif` (whatif phải nói "không phương án nào đạt chắc 80%", chạy tiếp 70%, 3,97 triệu); (d) quyết định: operator tạo + gửi, lead-1 duyệt, lead-2 tự duyệt → 403; (e) `jump 10` → cảnh báo bình nước; (f) `jump 13` → áo size M hết hàng khi CD005 vẫn chạy + AI `investigate`; (g) về 01:00. Mỗi lượt ~1 phút (máy rảnh) tới ~5 phút (máy bận), chủ yếu là các lần nhảy giờ. Diễn tập ghi thêm quyết định vào DB pitch như một buổi tập thật; muốn DB sạch thì dựng DB mới.

## 7. Kiểm tra 10 phút trước giờ

- [ ] `eval/reports/rehearsal.json` của hôm nay: `"ok": true`.
- [ ] `eval/reports/pitch_numbers.json` của hôm nay: mọi dòng `ĐẠT` (hoặc đã gạch số `LỆCH` khỏi slide).
- [ ] `demo.mjs` in "DEMO SẴN SÀNG", mọi bước ĐẠT. `GET /api/v1/ai/status`: `mode = replay`, `enabled = true`.
- [ ] `GET /api/v1/freshness`: `data_as_of` = 01:00 ngày 11.11 (không phải thì `node scripts/demo.mjs home`).
- [ ] Mở `/campaign/CD002/compare` một lần: đề xuất "chỉ chạy khung giờ 00–02h, 12–14h", tạm dừng bị làm mờ.
- [ ] Thử 1 câu AI ghi sẵn (gõ đúng nguyên văn), thấy chip nguồn và "kiểm số n/n".
- [ ] Terminal thứ hai mở sẵn ở gốc repo, gõ sẵn `node scripts/demo.mjs jump 10`.
- [ ] Video dự phòng mở được trên máy chiếu; có bản trên USB.
- [ ] Tắt thông báo hệ điều hành, phóng chữ trình duyệt 125%, đóng tab thừa, sạc đầy pin.
- [ ] Người bấm và người nói thống nhất mốc 2:40 (quyết bỏ K5 hay không).
- [ ] Không có `OPENAI_API_KEY` hiện trên màn hình hay trong terminal đang chiếu.
