# backend/eval · Chấm điểm bằng đáp án

Thư mục này chấm kết quả của app so với đáp án trong `mock-data/out/<cỡ>/ground_truth/`. Đây là **nơi duy nhất** được đọc `ground_truth/`. Code trong `backend/app/` không được nhắc tới đường dẫn đó và không được import `eval`; test `tests/unit/test_no_ground_truth_leak.py` chặn việc này trong CI.

Plan: [11 · Kiểm thử và chấm điểm](../../docs/dev-plan/11-kiem-thu-va-cham-diem.md) mục 2.

## Lệnh (khi các phần đã có)

```bash
cd mock-data && node generate.js --size demo
cd ../backend
uv run python -m eval.run_all --size demo --out eval/reports/      # chạy hết
uv run python -m eval.run_all --size demo --only ingest,forecast   # chạy một phần
```

## Cấu trúc dự kiến

| File / thư mục | Nội dung | Task |
|---|---|---|
| `__init__.py` | Đường dẫn dùng chung: `mock_dir(size)`, `ground_truth_dir(size)`, `REPORTS_DIR` | INF-06 (đã có) |
| `run_all.py` | Gọi lần lượt các phần, gộp báo cáo `.md` + `.json` | EVAL-02 |
| `eval_data.py` | (1) Ghép SKU, (2) bắt 12 lỗi cài sẵn, số bán/tồn/hết hàng của panel, PII, thời gian nạp lịch sử và từng giờ live (02 §8). **Đã có**: `uv run python -m eval.eval_data --size demo` | EVAL-02 / EVD-01 |
| `live_replay.py` | Bản Python của một bước `replay.js` (ghi `live_stream/` vào thư mục tạm), dùng cho test và đo PNL-04 | M1 |
| `eval_forecast.py` | (3) WAPE, bias, coverage, pinball, lệch do hết hàng (03 §10) | EVAL-02 / EVF-01 |
| `eval_ads.py` | (4) iROAS so với `chien_dich_that.csv`, câu cho slide | EVAL-07 |
| `eval_decision.py` | (5) Regret, cần chế độ `--policy` của mock-data | EVAL-03 |
| `eval_ai.py`, `ai/*.yaml` | Bộ eval AI (07 §10), chạy `AI_MODE=replay` trong CI | EVAL-05 |
| `ai_recordings/` | Bản ghi `AI_MODE=record` cho demo offline | AI-* |
| `loadtest.py` | Kiểm tải cỡ `tai_nang` (06 §8) | EVAL-06 |
| `policies/` | Chính sách cho mô phỏng ngược (11 §2 phần 5) | EVAL-03 |
| `reports/` | Báo cáo sinh ra (`score_demo_<YYYYMMDD-HHMM>.md/.json`), **không commit** (`.gitignore`) | — |

## Quy ước

- Mở DuckDB bằng `duckdb.connect(path, read_only=True)` và chỉ khi backend đã dừng (DuckDB chỉ cho một tiến trình ghi).
- Mỗi chỉ số in kèm ngưỡng và cột "Đạt". Con số nào chưa kiểm bằng mô phỏng ngược phải ghi rõ "ước lượng bằng mô hình của app".
- Mọi số đưa lên slide lấy từ báo cáo ở đây và ghi "trên dữ liệu mô phỏng".
