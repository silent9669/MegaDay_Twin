> **Lưu ý (30/10):** số liệu trong file này đo **trước** khi sửa lỗi (V mặc định cũ = 0,5 × giá vốn, runner-up trùng, ngưỡng 5% phồng). Số đã chốt để dùng cho kịch bản pitch nằm ở mục "Sau sửa lỗi M3-whatif" cuối [M3-mo-phong.md](M3-mo-phong.md).

# What-if cho kịch bản pitch (M3) · kiểm bằng bộ mô phỏng thật

Script: `backend/eval/demo_whatif.py`.

```bash
cd backend
uv run python -m eval.demo_whatif            # ~1–2 phút, 30 biến thể
uv run python -m eval.demo_whatif --scan     # thêm quét 49 chiến dịch × 5 bối cảnh (~20 phút)
uv run python -m eval.demo_whatif --only 1,4g --json out.json
```

Dựng DB như `tests/e2e/test_noichien_1111.py`: lịch sử `demo` + live 11.11 tới 01:00, `estimate_all` (iROAS thật của app). Không backtest dự báo (cờ `--backtest` nếu cần), nên nhãn là `scenario`. Seed 20261111, 1.000 mẫu, `persist=False`, `use_cache=False`. Cột "thật" chỉ lấy từ `ground_truth/chien_dich_that.csv` (chỉ `eval/` được đọc).

Số dưới đây là E[J] của app: đóng góp − chi ads + V × tồn cuối. "tr" = triệu đ, "k" = nghìn đ.

## 1. Kết quả CD002 nồi chiên, 11.11 01:00 → 24:00

Gốc: tồn 76 nồi, nồi hết khoảng 13:00 (cầu p50 ~156). Lãi mỗi nồi 9.280đ, ads cần iROAS ≥ 57 mới có lời.

| Hành động | E[J] gốc | Chi ads | Bán (sp) |
|---|---|---|---|
| continue | −1,57 tr | 5,30 tr | 106,3 |
| pause (CẤM) | 3,78 tr | 0 | 95,4 |
| reduce_budget 30% | 0,10 tr | 3,62 tr | 105,3 |
| reduce_budget 50% | 1,21 tr | 2,50 tr | 104,1 |
| **schedule_hours 00–02h, 12–14h** | **2,51 tr** | 1,18 tr | 98,9 |
| pause_until_restock (CẤM) | 3,78 tr | 0 | 95,4 |

| # | Biến thể | Best | Đổi kết luận? | Lợi hơn continue | Ghi chú |
|---|---|---|---|---|---|
| 1 | Gốc, `profit` | schedule_hours (recommend, p = 1,00) | — | **+4,08 tr** | runner-up reduce 50%, +1,30 tr. Không có ZoneBar, không có điều kiện đổi |
| 2a | Hàng về sớm 1 ngày (11/11 10:00, +400 nồi) | schedule_hours | **Không** | +12,69 tr | Số phồng vì V = 227.500đ × ~290 nồi dư |
| 2b | Hàng về sớm 2 ngày (ngay 01:00) | schedule_hours | Không | +12,70 tr | Như 2a |
| 2c | Hàng về trễ 2 ngày | schedule_hours | Không | +4,08 tr | Hàng về sau hạn: y hệt gốc |
| 2d | Về sớm 1 ngày + V = 0 | schedule_hours | Không | +3,52 tr | Số sạch nhất cho câu "hàng về sớm" |
| 2e | Về sớm 1 ngày + V = đóng góp ngày thường | schedule_hours | Không | +9,69 tr | |
| 3a | V = 0 | schedule_hours | Không | +3,99 tr | ZoneBar hiện, x* = 0 → "pause tốt hơn", nhưng pause bị cấm |
| 3b | V = 20.000đ | schedule_hours | Không | +4,13 tr | |
| 3c | V = đóng góp ngày thường (nồi 149.200đ, giấy lót 18.600đ) | schedule_hours | Không | +4,13 tr | |
| 4f | `sell_target` N = 95 | **reduce_budget 50%** | **Có** | +2,78 tr | schedule chỉ đạt 68% < 80%. Mất 1,30 tr so với schedule |
| 4g | `sell_target` N = 97 | **reduce_budget 30%** | **Có** | +1,67 tr | reduce 50% đạt 77% < 80%. Mất 2,40 tr so với schedule |
| 4a–c | `sell_target` N = 100 / 103 / 105 | — (`none_meets_goal`) | **Có** | — | Cao nhất: continue 71% / 59% / 52% |
| 4h | N = 100, chấp nhận xác suất 70% | **continue** (undetermined) | **Có** | 0 | Mất 4,08 tr so với schedule. p hiển thị 0,00 (xem §5) |
| 4d | `clear_stock`, lỗ tối đa 0 | **reduce_budget 30%** (recommend, p = 0,71) | **Có** | +1,67 tr | continue bị loại vì E[J] < 0 |
| 4e | `clear_stock`, chấp nhận lỗ 2 tr | **continue** (undetermined, p = 0,61) | **Có** | 0 | Bán thêm 0,9 sp, tốn 1,67 tr |
| 5 | Bỏ ràng buộc flash | **pause** (undetermined) | **Có** | +5,34 tr | **Giá của cam kết: 3,78 − 2,51 = 1,27 tr**. "undetermined" chỉ vì runner-up là pause_until_restock giống hệt (§5) |
| 5b | Bỏ flash + hàng về sớm 1 ngày | pause (recommend) | Có | +18,83 tr | Phồng vì V |
| 5c | Hạn tới hết 12/11 (hàng về 12/11 10:00 nằm trong hạn) | schedule_hours (undetermined) | Không | +6,12 tr | "undetermined" vì ngưỡng 5% × E[J] = 4,6 tr (§5) |

## 2. Ngày thường

| # | Biến thể | Best | Lợi hơn continue | Thật |
|---|---|---|---|---|
| 6a | CD002 05/11 09:00 → 24:00, V mặc định | pause (undetermined) | +1,79 tr | iROAS thật 5,27 > hòa vốn 4,96 |
| 6b | CD002 06/11 cả ngày, V mặc định | pause (undetermined) | +2,47 tr | ″ |
| 6c | CD002 06/11, V = 0 | reduce_budget 50% (undetermined, p = 0,75) | +384 k | ″ |
| 6e | CD002 06/11, V = đóng góp ngày thường | pause (undetermined) | +1,75 tr | ″ |
| 6d | CD002 06–09/11 (72 giờ), V = 0 | reduce_budget 50% (undetermined, p = 0,70) | +1,03 tr | ″ |
| 7a | CD004 cốc meme 05/11 09:00 | pause (undetermined, p = 0,94) | +89 k | iROAS thật 0,64 < hòa vốn 2,12: đúng |
| 7b | CD004 06/11 cả ngày | pause (undetermined, p = 0,92) | +96 k | đúng |
| 7c1/7d1 | CD005 áo giữ nhiệt 06/11 (iROAS biên 4,75 > hòa vốn 2,43), V mặc định / V = 0 | pause / **continue** (p = 0,54, +22 k) | — | iROAS thật 3,85 > 2,43 |
| 7c2/7d2 | CD001 bình nước 06/11 | pause / reduce 50% | — | thật 2,85 > 2,77 (sát) |
| 7c3/7d3 | CD023 hộp quà hoa sáp 06/11 | pause / reduce 50% | — | thật 4,47 > 3,00 |

**CD002 ngày thường: app không nói "chạy tiếp".**
- iROAS ước lượng 4,42 [3,33–5,39], iROAS biên 1,67; hòa vốn 4,96 (ads), 4,6 (simulator, theo giá 690.000đ và m = 149.200đ). Thật 5,27.
- Với V mặc định, app chọn pause chỉ vì "giữ hàng lợi hơn bán" (V 227.500đ > m 149.200đ).
- Với V = 0, app chọn giảm 50% ngân sách, lợi hơn continue 384 k/ngày, nhưng `undetermined`.
- Đáp án chỉ có iROAS **trung bình**; quyết định "giảm hay giữ" phụ thuộc iROAS **biên**, đáp án không có. Không nói được app đúng hay sai ở đây. Chỉ chắc một điều: không được nói "ngày thường thì app khuyên chạy tiếp".

**Không có ca ngày thường nào "continue thắng rõ".** Quét 49 chiến dịch, ngày 06/11, V = 0: không chiến dịch nào ra `recommend continue`. Lý do: ước lượng iROAS của app lệch thấp có hệ thống (trung vị ước lượng/thật 0,82, M3-ads B5) và iROAS biên thấp hơn nhiều so với trung bình (bão hòa). Ca gần nhất: CD005, V = 0 (continue, p = 0,54) và CD005 11.11 V = 0 (continue, p = 0,82, +179 k, vẫn `undetermined` vì < 200 k).

## 3. V mặc định là gì, có hợp lý không

- Mặc định: `leftover_value_per_unit = 0,5 × giá vốn` (`parameters.py`, `inputs.py`): nồi 227.500đ, giấy lót 9.000đ, cốc 16.000đ.
- J tính đóng góp **đã trừ giá vốn** (m = giá − phí − vốn − đóng gói). Vậy sp dư được tính V, sp bán được tính m, cùng một gốc là "so với lúc chưa bỏ vốn".
- Theo gốc đó: V = 0 nghĩa là sp dư **đáng đúng giá vốn** (bán sau hòa vốn), không phải "vô giá trị". Vô giá trị thật là V = −giá vốn (danh mục chặn V < 0).
- V đúng kinh tế = đóng góp tương lai của sp đó (đã chiết khấu, trừ chi phí giữ hàng). Nó **không thể lớn hơn m ngày thường**. Nồi: V ≤ 149.200đ, mà mặc định là 227.500đ. **Mặc định sai, quá cao.**
- Hàng nhập đều mỗi tuần (nồi): sp dư hôm nay chỉ thay cho một sp phải nhập tuần sau. V ≈ 0 (trừ chi phí vốn). Hàng mùa vụ / ế: V < 0.
- Hệ quả của mặc định: ngày thường gần như mọi chiến dịch ra pause với lý do "giữ hàng lợi hơn bán" (quét 06/11: 27/31 chiến dịch có kết quả ra pause; với V = 0 còn 16/31). Số E[J] phồng lên hàng chục triệu (CD002 06/11: 63 tr, trong đó 60 tr là V × tồn).
- **Đề xuất cho chủ plan (Q6 M3-mo-phong):** mặc định V = 0 cho hàng nhập đều. Giải thích trên UI: "0đ = bán sau vẫn hòa vốn".

Kết luận 11.11 không phụ thuộc V: schedule_hours thắng ở mọi V đã thử (0; 20.000đ; đóng góp ngày thường; mặc định).

## 4. Đề xuất cho kịch bản pitch

### (a) What-if nên dùng trên sân khấu

**Câu 1: "Nếu sếp giao chỉ tiêu bán đủ 97 sp hôm nay?"** (đổi kết luận, số rõ)
- Thao tác: đổi mục tiêu sang "Bán đủ số lượng", N = 97, hạn 24:00 11/11.
- Kết quả: đề xuất đổi từ "Chỉ chạy khung giờ" sang **"Giảm ngân sách 30%"**.
- Lý do hiện trên app: khung giờ chỉ đạt 57%, giảm 30% đạt 81% (≥ 80%).
- Câu nói: "Thêm vài sản phẩm bán được phải trả 2,4 triệu tiền ads. App cho sếp thấy cái giá đó trước khi quyết."
- Mở rộng (tùy thời gian): N = 95 → giảm 50% (tốn 1,3 tr). N = 100 → "không cách nào đạt 100 sp với xác suất 80%; chạy tiếp cao nhất 71%".

**Câu 2: "Cam kết flash sale với sàn đang tốn mình bao nhiêu?"** (cái giá của ràng buộc)
- Thao tác: so dòng "Tạm dừng" (bị cấm, vẫn hiện số) với đề xuất.
- Kết quả: Tạm dừng 3,78 tr, Chỉ chạy khung giờ 2,51 tr → **cam kết tốn ~1,3 triệu**. Chạy tiếp cả ngày tốn thêm 4,1 triệu nữa.
- Câu nói: "App không cho tắt hẳn vì đã cam kết với sàn. Nhưng nó nói rõ cam kết đó tốn 1,3 triệu, để lần sau sếp đàm phán."
- Không cần sửa DB khi demo: dòng pause bị cấm vẫn có E[J].

**Câu 3: "Nếu hàng về sớm một ngày thì sao?"** (kết luận VỮNG, dùng để chứng minh app không đổi ý vô cớ)
- Thao tác: đặt "Hàng về trễ (kịch bản)" = −1 ngày (11/11 10:00, +400 nồi), nên đặt V = 0 cùng lúc.
- Kết quả: vẫn "Chỉ chạy khung giờ". Lợi hơn chạy tiếp 3,5 triệu (V = 0).
- Câu nói: "Có thêm 400 nồi cũng không cứu được ads, vì mỗi nồi giá 11.11 chỉ lãi 9.280đ, ads phải đạt iROAS 57 mới có lời."

### (b) Câu KHÔNG được nói

- ✗ "Nếu hàng về sớm một ngày thì kết luận đổi." Sai: không đổi ở mọi V (2a, 2b, 2d, 2e).
- ✗ "Ngày thường app khuyên chạy tiếp ads nồi chiên." Sai: app ra pause (V mặc định) hoặc giảm 50% (V = 0), đều `undetermined`.
- ✗ "App đề xuất tạm dừng ads cốc meme chắc chắn." Kết quả là pause nhưng `undetermined` (lợi 33–41 k < ngưỡng 200 k). Nói: "App nghiêng về dừng, chênh nhỏ vì chiến dịch nhỏ".
- ✗ Đọc số E[J] tuyệt đối khi V mặc định ("lời 78 triệu"). Số đó gần hết là V × tồn dư. Chỉ đọc **chênh lệch** giữa hai hành động.
- ✗ "Thanh vùng của nồi chiên 11.11." Không có thanh vùng ở V mặc định. Ở V = 0 có thanh nhưng nói "pause tốt hơn", mà pause bị cấm: gây rối.
- ✗ "Đáp án thật xác nhận app đúng ở nồi chiên ngày thường." Đáp án chỉ có iROAS trung bình 5,27; quyết định cần iROAS biên.

### (c) Ví dụ cho thanh vùng (ZoneBar)

Luôn đặt V = 0 trước khi trình diễn thanh vùng. Với V mặc định, nhiều thanh ra x* = 0 chỉ vì "giữ hàng lợi hơn bán", không phải vì ads.

| Dùng cho | Ca | Thanh (x* và khoảng pause p10–p90, sp/giờ) | Verdict | Đáp án thật |
|---|---|---|---|---|
| **Chính: "nên dừng"** | CD020 Đèn lồng đỏ, 06/11 cả ngày, V = 0 | x* = 0,65; khoảng 0,67–1,21 | `pause_better` | iROAS thật 2,98 < hòa vốn 3,59: đúng. Best pause (p = 0,91, lợi hơn continue 107 k, `undetermined` vì < 200 k) |
| Bản 3 ngày (số to hơn) | CD020, 06–09/11, V = 0 | x* = 0,72; khoảng 0,79–1,17 | `pause_better` | đúng. Best pause, lợi hơn continue 308 k, p = 0,95 |
| **"Chưa rõ" (thể hiện trung thực)** | CD002 nồi chiên, 06/11, V = 0 | x* = 1,44; khoảng 1,08–1,96 | `undetermined` | thật 5,27 > 4,96 (sát hòa vốn) |
| "Nên chạy" | CD023 Hộp quà hoa sáp, 11.11, V = 0 | x* = 6,55; khoảng 3,74–5,87 | `continue_better` | thật 4,47 > 3,00: đúng. Nhưng best là giảm 50%, không phải continue: chỉ dùng nếu giải thích được "chạy tốt hơn dừng, giảm bớt còn tốt hơn" |

Chạy lại riêng hai ca: `uv run python -m eval.demo_whatif --only 8a,8b`.

Câu nói gợi ý (CD020): "Nếu dừng ads mà mỗi giờ vẫn bán được hơn 0,65 cái thì dừng lợi hơn. Dự báo khi dừng là 0,67–1,21 cái/giờ, nằm hẳn bên phải vạch. Nên dừng."

Không dùng: cốc meme CD004 (thanh `undetermined`, x* = 0,16–0,23 nằm trong khoảng, dù ads lỗ thật) và nồi chiên 11.11 (không có thanh, hoặc thanh nói "pause" trong khi pause bị cấm).

### (d) Giới hạn cần nói

- Số là mô phỏng trên dữ liệu giả lập, nhãn "Kịch bản" (dự báo chưa backtest trong DB demo).
- Ads: app ước lượng iROAS lệch thấp (trung vị ×0,82). Nồi chiên: ước lượng 4,42, thật 5,27. Kết luận 11.11 không phụ thuộc điểm này; kết luận ngày thường thì có.
- Giá trị hàng dư V là giả định của người bán. Mặc định hiện tại quá cao (§3).
- Đề xuất chỉ "chắc" khi chênh ≥ 200 k và ≥ 5% |E[J]|, xác suất ≥ 70%. Chiến dịch nhỏ thường ra "chưa đủ rõ".

## 5. Lỗi/điểm lạ phát hiện (không sửa, ngoài ranh giới)

1. **Runner-up trùng hệt best → p = 0,00, `undetermined`.** Khi `pause` và `pause_until_restock` cùng lịch chi (hàng về sau hạn), runner-up là bản sao, `P(J_best > J_runner) = 0` (bằng nhau). Gặp ở biến thể 5, CD011, CD012, CD019, CD023 (06/11 và 11.11). Sửa: bỏ hành động trùng lịch chi khỏi xếp hạng, hoặc so `≥`.
2. **`sell_target` khi chỉ một hành động đạt:** runner-up là hành động không đạt mục tiêu (4h: runner-up schedule_hours, gain −4,08 tr, p = 0). Nên: runner-up = None → `recommend`, hoặc so với hành động đạt kế tiếp.
3. **Ngưỡng 5% × |E[J]| bị V × tồn làm phồng.** CD002 hạn 12/11 (5c): chênh 2,19 tr, p = 1,00 vẫn `undetermined` vì 5% × 91 tr = 4,6 tr. Nên tính 5% trên phần J không gồm V × tồn, hoặc trên chênh đóng góp.
4. **ZoneBar khi pause bị cấm** (CD002 11.11, V = 0): thanh vẫn hiện "pause tốt hơn". Nên ẩn hoặc ghi "pause không được phép".
5. **V mặc định** (§3).
6. `clear_stock` 4d: lý do ghi "chênh tiền −1.108.713đ" so với runner-up: đúng định nghĩa nhưng dễ đọc nhầm là lỗ.

## 6. Quét 49 chiến dịch (`--scan`)

18/49 chiến dịch bị chặn (thiếu giá vốn, `blocked`). Trên 31 chiến dịch còn lại:

| Bối cảnh | pause | continue | `recommend` | Thanh `pause_better` | Thanh `continue_better` |
|---|---|---|---|---|---|
| 11.11 01:00, V mặc định | 25 | 2 | 2 (CD002 schedule, CD018 pause) | 12 | 0 |
| 11.11, V = 0 | 9 | 3 | 2 (CD002 schedule, CD015 pause) | 3 | 1 (CD023) |
| 06/11, V mặc định | 27 | 2 | 0 | 16 | 0 |
| 06/11, V = 0 | 16 | 5 | 0 | 1 (CD020) | 0 |
| 06–09/11 (72 giờ), V = 0 | 15 | 4 | 0 | 2 (CD015, CD020) | 1 (CD023, sát) |

- "continue" có p = 0,00 ở CD008, CD033 (và CD007 11.11): chiến dịch không chi ads trong horizon, continue ≡ pause. Không phải "ads lời".
- Ngày thường gần như mọi ca là `undetermined`: chiến dịch nhỏ (ngân sách 150–600 k/ngày), chênh < 200 k.
- Ca đáng chú ý cho slide "ads lỗ thật": CD018 Đèn lồng đỏ (mẫu 2) 11.11: `recommend pause`, p = 1,00, lợi 497 k so với giảm 50%, 936 k so với chạy tiếp. iROAS 0,29 (thật 0,38), hòa vốn 2,70.
