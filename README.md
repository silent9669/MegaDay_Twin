# MegaDay Twin

[Live demo](https://megadaytwin.dangphuc.me) · [CI](https://github.com/silent9669/MegaDay_Twin/actions/workflows/ci.yml)

Trợ lý ra quyết định quảng cáo cho người vận hành shop trên Shopee. Khi nhu cầu và tồn kho thay đổi giữa mùa sale, app trả lời câu hỏi **"lúc này nên tiếp tục hay tạm dừng quảng cáo?"**. Câu trả lời dựa trên tồn kho, giá vốn, lịch hàng về và mục tiêu của shop, chứ không chỉ dựa trên ROAS sàn báo.

Điểm khác biệt cốt lõi: **AI không thể bịa số**. Lớp AI (OpenAI) hiểu câu hỏi tiếng Việt, điều tra và giải thích. Mọi con số nó nói ra đều đến từ công cụ đã được chấm điểm, được kiểm tự động trước khi hiển thị, và bấm vào được để xem nguồn. AI không tự bật/tắt quảng cáo; mọi hành động cần người duyệt.

> Dự án cho SEA × OpenAI Hackathon. Phạm vi bản hackathon: 1 shop, 1 sàn (Shopee), dữ liệu nạp bằng file, kịch bản mega sale 11.11 mô phỏng.

## Trạng thái

| Phần | Trạng thái | Ở đâu |
|---|---|---|
| Tài liệu sản phẩm (tính năng, hoàn cảnh, điều kiện sử dụng) | Có | [MegaDay-Twin-Tinh-nang-hoan-canh-va-dieu-kien-su-dung.pdf](MegaDay-Twin-Tinh-nang-hoan-canh-va-dieu-kien-su-dung.pdf) |
| Plan kỹ thuật đầy đủ (14 file) | Có | [docs/dev-plan/](docs/dev-plan/README.md) |
| Bộ sinh dữ liệu giả lập có đáp án | Có, chạy được | [mock-data/](mock-data/README.md) |
| Dữ liệu đã sinh sẵn (cỡ `nho` và `demo`, seed 20261111) | Có | `mock-data/out/` |
| M0 · Khung: FastAPI, DuckDB đủ bảng, React 8 màn, CI một cổng `ci`, deploy Cloudflare, lệnh `node scripts/dev.mjs` | Xong | [backend/](backend/README.md), [web/](web/README.md), [ci.yml](.github/workflows/ci.yml) |
| M1 · Dữ liệu: adapter 7 loại file, bỏ PII, ghép SKU 3 nguồn, kiểm 12 lỗi cài sẵn, `hourly_panel`, `ingest` + nạp live, `eval_data` | Xong (chấm trên `demo` bên dưới) | `backend/app/ingest`, `backend/app/panel`, [nhật ký](docs/quyet-dinh/M1-pipeline.md) |
| M2 · Dự báo: 6 model, phân nhóm, backtest, nhãn tin cậy (PIT, WAPE so với sàn nhiễu), `backtest` / `fit` | Xong: tốt hơn baseline 46% trên 10.10; verified 44 / reference 242 / scenario 14 | `backend/app/forecast`, [nhật ký](docs/quyet-dinh/M2-du-bao.md) |
| M3 · Quyết định: iROAS, kinh tế, mô phỏng 5 hành động, API, màn Tổng quan và So sánh nối dữ liệu thật | Xong: nồi chiên 11.11 → chỉ chạy khung flash sale, hơn chạy tiếp ~4 triệu | `backend/app/{ads_effect,economics,simulator,api}`, [nhật ký](docs/quyet-dinh/M3-mo-phong.md) |
| M4 · AI và chạy trực tiếp | Đang làm: nền lớp AI xong (công cụ, kiểm số, chống injection), chưa nối giao diện | `backend/app/ai`, [nhật ký](docs/quyet-dinh/M4-ai.md) |
| Màn Nhập dữ liệu, Cảnh báo, Nhật ký quyết định, Trợ lý AI, Cài đặt | Chưa có (trang chờ) | `web/src/pages` |

Kết quả chấm lớp dữ liệu trên `demo` (`uv run python -m eval.eval_data --size demo`, dữ liệu mô phỏng, seed 20261111):

| Tiêu chí M1 | Kết quả | Ngưỡng |
|---|---|---|
| Nạp 6 file lịch sử | 11,8–12,8 giây (thêm 3,1 giây cho cả ngày live 11.11) | < 60 giây |
| Ghép SKU tự động đúng / sai | 97,1 % / 0 % trên mã nội bộ + giá vốn (98,1 % trên cả 3 nguồn); 17 alias giá vốn chờ xác nhận | ≥ 90 % / ≤ 1 % |
| Lỗi cài sẵn bắt được | 12/12 | 12/12 |
| `hourly_panel` | 300 SKU × 2.880 giờ | 300 × 2.880 |
| PII trong DuckDB | 0 | 0 |
| Nạp 1 giờ live (PNL-04) | 2,2–2,5 giây khi máy rảnh, tới 7,5 giây khi máy bận; panel chiếm phần lớn, hướng tối ưu ở nhật ký §14 | < 5 giây |

## Kiến trúc tóm tắt

```
Người vận hành ── hỏi bằng lời / ảnh ──▶  LỚP AI (OpenAI: function calling + kiểm số)
                                              │ chỉ đọc số qua công cụ
       ┌──────────────────────────────────────┼──────────────────────────────┐
       ▼                                      ▼                              ▼
① Dữ liệu ──▶ ② Mô hình ──▶ ③ Quyết định ──▶ ⑤ Giao diện 3 tầng ──▶ người duyệt ──▶ thao tác (mô phỏng)
 adapter,      dự báo có      mục tiêu, tham số,   kết luận 3 giây,
 ghép SKU,     phiếu điểm,    mô phỏng 5 hành động, lý do, chi tiết
 kiểm lỗi,     iROAS,         ngưỡng hòa vốn
 bỏ PII        kinh tế
       ▲                                                                     │
       └──────────── kết quả thật sau ca quay về chấm điểm lại ◀─────────────┘
                     ④ Quản trị: nhật ký quyết định, phiên bản, phân quyền
```

Chi tiết: [docs/dev-plan/01-kien-truc-va-cong-nghe.md](docs/dev-plan/01-kien-truc-va-cong-nghe.md).

## Cấu trúc repo

```
.
├─ README.md                      # file này
├─ MegaDay-Twin-...pdf            # tài liệu sản phẩm gốc
├─ docs/
│  ├─ dev-plan/                   # plan kỹ thuật: kiến trúc, dữ liệu, mô hình, AI, API, UI, lộ trình
│  ├─ pitch/                      # bản nháp nội dung pitch
│  └─ so-do/                      # sơ đồ hệ thống (HTML)
├─ mock-data/                     # bộ sinh dữ liệu shop giả lập (Node.js, không thư viện ngoài)
│  ├─ generate.js                 # sinh dữ liệu → mock-data/out/<cỡ>/
│  ├─ replay.js                   # phát lại ngày 11.11 theo giờ
│  ├─ config.json
│  └─ src/
├─ backend/                       # Python 3.11, FastAPI, DuckDB
│  ├─ app/{core,ingest,warehouse,panel,forecast,ads_effect,economics,
│  │       simulator,orchestrator,governance,ai,live,api}/
│  ├─ app/warehouse/schema.sql    # hợp đồng DB: mọi bảng của plan
│  ├─ tasks.py                    # lệnh dev (lint, test, check, dev...), chạy được cả Windows
│  ├─ tests/{unit,integration,e2e}/
│  └─ eval/                       # chấm điểm bằng ground_truth (app/ không được đọc)
├─ web/                           # React 18, Vite, TypeScript, Tailwind, TanStack Query
│  └─ src/{pages,components,api,lib}/
├─ docs/quyet-dinh/               # nhật ký quyết định tự chốt khi dev
└─ .github/                       # CI (ci.yml) + mẫu PR
```

Cây thư mục chi tiết tới từng file nằm ở [01 · Kiến trúc và công nghệ, mục 4](docs/dev-plan/01-kien-truc-va-cong-nghe.md).

## Bắt đầu nhanh

### Yêu cầu

| Công cụ | Phiên bản | Dùng cho |
|---|---|---|
| Git | bất kỳ | |
| Node.js | ≥ 18 (team đang dùng v24) | `mock-data`, `web` |
| Python | 3.11 | `backend` |
| [uv](https://docs.astral.sh/uv/) | mới nhất | quản lý môi trường Python |
| OpenAI API key | | lớp AI (app vẫn chạy đủ khi tắt AI) |

Cài trên Windows:

```powershell
winget install --id Python.Python.3.11 -e
winget install --id astral-sh.uv -e
# mở terminal mới rồi kiểm tra
python --version
uv --version
node --version
```

### 1. Lấy code

```bash
git clone https://github.com/AIVIETNAM-AIO-QP09/sea-x-openai-hackathon.git
cd sea-x-openai-hackathon
```

### 2. Sinh dữ liệu giả lập

Hai bộ `nho` và `demo` đã có sẵn trong repo ở `mock-data/out/`, clone về là dùng được ngay. Chỉ cần chạy lại khi sửa bộ sinh hoặc `config.json`; cùng seed thì ra cùng một bộ (trừ thời điểm tạo ghi trong `manifest.json`).

```bash
cd mock-data
node generate.js --size nho      # 30 SKU, < 1 giây — dùng khi dev
node generate.js --size demo     # 300 SKU, ~2 giây, ~60 MB — dùng để demo và chấm điểm
```

Kết quả nằm ở `mock-data/out/<cỡ>/`. Cuối lệnh có phần "Kiểm nhanh kịch bản 11.11" để xác nhận dữ liệu đúng.

### 3. Phát lại ngày 11.11 (chế độ live)

```bash
node replay.js --size demo --interval 20    # mỗi giờ mô phỏng = 20 giây
node replay.js --size demo --from 9         # bắt đầu từ 9h sáng cho demo ngắn
```

Dữ liệu "đang về" được ghi vào `mock-data/out/demo/live_stream/`, kèm `trang_thai.json` cho biết đã phát tới giờ nào. Báo cáo ads trễ 2 giờ như thật.

### 4. Backend và frontend

Cài một lần:

```powershell
uv python install 3.11                                  # nếu máy chưa có Python 3.11
cd backend; uv sync; copy .env.example .env; cd ..      # điền OPENAI_API_KEY khi dùng AI (M4)
cd web; npm ci; cd ..
```

Chạy cả backend và web bằng **một lệnh** ở gốc repo (Windows, Mac, Linux đều dùng được):

```powershell
node scripts/dev.mjs                  # API http://localhost:8000/docs · Web http://localhost:5173
node scripts/dev.mjs --fixtures       # chỉ web, dữ liệu mẫu, không cần backend
node scripts/dev.mjs --db data/demo.duckdb --api-port 8001 --web-port 5174
```

Script tự migrate database trước khi bật API, gắn nhãn `[api]` / `[web]` cho log, và **Ctrl+C tắt cả hai**. Máy có `make` thì `make dev` / `make dev-fixtures` / `make check` gọi lại đúng các lệnh này.

Nạp dữ liệu demo và chạy các bước tính (cần cho màn Tổng quan và So sánh có số thật):

```powershell
cd backend
uv run python -m app.cli reset --yes
uv run python -m app.cli ingest --dir ../mock-data/out/demo   # ~15–40 giây
uv run python -m app.cli backtest                              # ~1–5 phút
uv run python -m app.cli fit
uv run python -m app.cli estimate-ads
```

Chuẩn bị demo pitch bằng **một lệnh** (dựng DB **đầy đủ** mới tới 01:00 ngày 11.11: backtest + fit + iROAS, ~3–5 phút, giống hệ thống đo số pitch; đặt mục tiêu CD002, làm nóng màn So sánh, bật backend với AI replay + web), xem [runbook](docs/demo/runbook.md):

```powershell
node scripts/demo.mjs                                   # hoặc --db <file .duckdb có sẵn>, --api-port/--web-port; --light = bản nhẹ, số khác pitch
node scripts/demo.mjs jump 13                           # nhảy dữ liệu tới 13:59; home = về 01:00
cd backend; uv run python -m eval.rehearsal --base-url http://localhost:8000   # diễn tập 2 lượt, ĐẠT/LỖI từng cảnh
```

Bản demo Cloudflare: <https://megadaytwin.dangphuc.me>. Deploy dùng `cf` CLI,
Worker `megadaytwin` và D1 `megadaytwin-db`; hiện frontend dùng dữ liệu mẫu,
backend Python/DuckDB chưa deploy. Nhánh `main` được bảo vệ bởi check `ci`; sau khi merge một thay đổi đã qua CI,
workflow tự đẩy đúng commit sang nhánh `cloudflare-production`. Cloudflare Workers
Builds tự kéo nhánh đó, build và deploy. Job `deploy` chờ kết quả Cloudflare và kiểm tra
health/access trên website trước khi đánh dấu môi trường `production` thành công.
Cấu hình và lệnh ở [web/README.md](web/README.md#cloudflare).


Kiểm tra trước khi push (giống hệt CI):

```bash
cd backend && uv run python tasks.py check     # ruff + mypy + pytest
cd web && npm run lint && npm run typecheck && npm test && npm run build
```

### Biến môi trường

Backend đọc `backend/.env`. Mẫu đầy đủ ở [01 · mục 6](docs/dev-plan/01-kien-truc-va-cong-nghe.md). Các biến quan trọng:

| Biến | Ý nghĩa |
|---|---|
| `OPENAI_API_KEY` | Key OpenAI. **Không commit.** |
| `OPENAI_MODEL_REASONING` / `OPENAI_MODEL_FAST` / `OPENAI_MODEL_EMBED` | Tên model theo từng việc. Không hardcode trong code; kiểm bảng giá hiện tại trước khi chọn |
| `AI_ENABLED` | `false` thì app vẫn chạy đủ, chỉ tắt phần hỏi đáp |
| `AI_DEMO_CACHE` | Dùng câu trả lời đã cache cho kịch bản demo |
| `DATA_DIR`, `LIVE_DIR` | Thư mục dữ liệu lịch sử và dữ liệu live |
| `DUCKDB_PATH` | Mặc định `data/megaday.duckdb` |

## Dữ liệu giả lập

Một shop đồ gia dụng và quà tặng trên Shopee, có 120 ngày lịch sử theo giờ (14/07 → 10/11/2026) và ngày 11.11 để phát lại. Doanh số sinh theo cơ chế giống thật: nhịp giờ, ngày đôi, mùa vụ, trend, voucher, quảng cáo có bão hòa và tác dụng kéo dài, hết hàng, thứ hạng tự nhiên.

- **File đầu vào** (cái app được đọc): đơn hàng, báo cáo ads, sheet tồn kho, giá vốn gõ tay, cấu trúc chiến dịch, cam kết sale 11.11. Các file được làm bẩn có chủ ý (múi giờ UTC, đơn trùng, tồn âm, tên lệch, PII giả, một ghi chú prompt injection).
- **Đáp án** (`ground_truth/`): nhu cầu thật kể cả lúc hết hàng, iROAS thật, bảng ghép SKU đúng, danh sách lỗi cài sẵn. ROAS sàn phóng đại trung bình khoảng 1,47 lần so với iROAS thật.
- **6 nhân vật demo** cài sẵn câu chuyện cho 11.11: bình nước viral, nồi chiên sắp cạn, áo size M hết hàng, cốc meme trend nguội, đèn Tết tồn nhiều, máy hút bụi mới ra.

Chi tiết: [mock-data/README.md](mock-data/README.md).

## Tài liệu

| Tài liệu | Dành cho |
|---|---|
| [docs/dev-plan/README.md](docs/dev-plan/README.md) | Mục lục plan kỹ thuật, đọc theo vai trò |
| [00 · Tổng quan](docs/dev-plan/00-tong-quan.md) | Mọi người, đọc đầu tiên |
| [12 · Lộ trình và phân việc](docs/dev-plan/12-lo-trinh-va-phan-viec.md) | Mọi người: mốc, bảng task, người phụ trách, lịch 10 ngày |
| [11 · Kiểm thử và chấm điểm](docs/dev-plan/11-kiem-thu-va-cham-diem.md) | Chấm điểm bằng `ground_truth`, kịch bản diễn tập demo |
| [13 · Rủi ro và câu hỏi mở](docs/dev-plan/13-rui-ro.md) | Những điều team cần chốt |
| [mock-data/README.md](mock-data/README.md) | Định dạng file, lỗi cài sẵn, mô hình sinh dữ liệu |
| [Cẩm nang pitch](https://claude.ai/artifact/NuSEaHjNHnzrhKknD4smWU) | Pitch lead (cần chủ doc chia sẻ quyền xem) |
| [docs/pitch/noi-dung-pitch.md](docs/pitch/noi-dung-pitch.md) | Bản nháp nội dung pitch, đọc được ngay trên GitHub |
| [docs/so-do/so-do-he-thong.html](docs/so-do/so-do-he-thong.html) | Sơ đồ hệ thống 5 lớp + lớp AI; tải về rồi mở bằng trình duyệt ([bản online](https://claude.ai/artifact/WT3zRbF5WXiJwzZ6w79hg5)) |

## Mốc

| Mốc | Qua mốc khi |
|---|---|
| M0 · Khung dự án | `backend` + `web` chạy, CI xanh, DuckDB đủ bảng |
| M1 · Dữ liệu sạch | Nạp đủ 7 file, ghép SKU tự động ≥ 90%, bắt đủ 12 lỗi cài sẵn |
| M2 · Dự báo và chấm điểm | Tốt hơn baseline ≥ 15% WAPE trên đợt 10.10, mọi SKU có nhãn tin cậy |
| M3 · Quyết định | Mô phỏng < 1 giây; ca nồi chiên 11.11 ra đúng `schedule_hours` |
| M4 · AI và live | Phát lại 11.11 chạy, AI điều tra đúng ca size M, kiểm số 100% |
| M5 · Demo | Diễn tập đạt 2 lần liên tiếp, có video dự phòng |

Plan hiện vượt công suất 4 dev × 10 ngày. Xem 3 phương án xử lý ở đầu [file 12](docs/dev-plan/12-lo-trinh-va-phan-viec.md).

## Quy ước làm việc

Team làm **tuần tự từng mốc** (M0 → M1 → … → M5). Bên trong một mốc, nhiều người hoặc nhiều AI agent làm song song các task, mỗi người một nhóm file riêng.

| Việc | Quy ước |
|---|---|
| Nhánh | **Mỗi mốc một nhánh**: `feat/M1-du-lieu`, `feat/M2-du-bao`… Sửa lỗi gấp: `fix/<mô-tả>` |
| Commit | Dòng đầu ≤ 72 ký tự, có mã task, ví dụ `ING-03: chuyển giờ báo cáo ads từ UTC` |
| Pull request | **Mỗi mốc một PR** vào `main`, điền đủ mẫu PR. Chỉ mở khi đạt tiêu chí mốc ở file 12 |
| CI | Job cổng `ci` phải xanh. Job này tự chạy phần bị đụng: backend, web, kiểm bộ sinh dữ liệu |
| `main` | Chỉ nhận code qua PR có `ci` xanh; cấm push thẳng và force push |
| Quyết định | Vấn đề plan chưa nói rõ thì tự chốt theo tinh thần plan, ghi vào `docs/quyet-dinh/`. Đổi hợp đồng (schema, API, kiểu) thì sửa `docs/dev-plan/` trong cùng PR |
| Definition of Done | Code + test + `ci` xanh + đã chạy với dữ liệu `demo` + đạt tiêu chí mốc |

Mã task (`INF`, `ING`, `PNL`, `FC`, `ADS`, `ECO`, `SIM`, `ORC`, `AI`, `API`, `WEB`, `GOV`, `LIVE`, `EVAL`, `DEMO` + số) lấy từ bảng task trong file 12.

## CI

Một workflow duy nhất, [.github/workflows/ci.yml](.github/workflows/ci.yml), chạy trên mọi PR và mọi lần push vào `main`:

| Job | Chạy khi PR đụng | Kiểm tra |
|---|---|---|
| `backend` | `backend/`, `mock-data/` | ruff, ruff format, mypy, pytest (gồm test chống rò `ground_truth`) |
| `web` | `web/` | eslint, tsc, vitest, build |
| `mock-data` | `mock-data/` | Sinh lại cỡ `nho` và `demo`, phải khớp y hệt bản đã commit |
| `ci` | luôn chạy | Cổng: đỏ nếu job nào lỗi. Đây là check bắt buộc của nhánh `main` |
| `deploy` | push web/data lên `main`, hoặc chạy tay trên `main`, sau khi `ci` xanh | Chờ Cloudflare deploy đúng commit, kiểm health và access, cập nhật trạng thái `production` |

Repo public nên GitHub chạy CI miễn phí. GitHub cũng quét và chặn ngay lúc push nếu ai lỡ commit API key.

## Nguyên tắc bắt buộc

1. **AI không bịa số.** Mọi con số AI nói ra phải đến từ kết quả công cụ và qua bước kiểm số. Xem [07 · Lớp AI](docs/dev-plan/07-lop-ai.md).
2. **Không rò đáp án.** Code trong `backend/app/` không được đọc `mock-data/out/*/ground_truth/`. Chỉ `backend/eval/` được đọc, và có test chặn.
3. **Người duyệt mọi hành động.** AI chỉ đề xuất tham số (`set_by=ai_suggested`); không tự tạo mục tiêu, không tự thao tác quảng cáo.
4. **Bỏ PII ngay khi nhập.** Tên, số điện thoại, địa chỉ người mua không được lưu vào DB và không gửi lên OpenAI.
5. **Dữ liệu ngoài là dữ liệu, không phải lệnh.** Ghi chú khách, tên sản phẩm, nội dung file luôn được bọc nhãn untrusted khi đưa cho AI.
6. **Không commit API key.** `.env` và `backend/data/` (DuckDB local) nằm trong `.gitignore`. Mọi thứ khác, kể cả dữ liệu giả lập trong `mock-data/out/`, đều được commit.
7. **Trung thực về độ bất định.** Mọi con số có nhãn Đã kiểm / Tham khảo / Kịch bản; dữ liệu mô phỏng luôn được gọi là mô phỏng.

## Việc đầu tiên

1. Đọc [00 · Tổng quan](docs/dev-plan/00-tong-quan.md), [12 · Lộ trình](docs/dev-plan/12-lo-trinh-va-phan-viec.md) và file của vai trò mình (bảng "Đọc theo vai trò" trong [docs/dev-plan/README.md](docs/dev-plan/README.md)).
2. Đọc nhật ký quyết định M0 trong [docs/quyet-dinh/](docs/quyet-dinh/): có vài chỗ code lệch có chủ ý so với plan (ví dụ cột `"at"` phải đặt trong ngoặc kép trong SQL).
3. Chạy được backend và web theo mục "Bắt đầu nhanh".
4. Làm M1 (lớp dữ liệu) trên nhánh `feat/M1-du-lieu`. Hợp đồng DB nằm ở [backend/app/warehouse/schema.sql](backend/app/warehouse/schema.sql).
