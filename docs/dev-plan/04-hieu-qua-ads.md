# 04 · Hiệu quả thật của ads (iROAS)

Module `ads_effect` ước lượng **mỗi đồng chi ads tạo thêm bao nhiêu doanh thu** (iROAS), cho từng chiến dịch, kèm khoảng p10–p90, từ các lần ads bật/tắt trong lịch sử. Đây là đầu vào quyết định của simulator, và là câu chuyện khác biệt nhất khi pitch: ROAS sàn báo cáo trung bình **gấp 1,47 lần** hiệu quả thật trong bộ demo.

## 1. Định nghĩa

| Đại lượng | Công thức | Ai dùng |
|---|---|---|
| ROAS sàn | `Σ attributed_revenue / Σ spend` (từ báo cáo ads) | Chỉ để hiển thị so sánh |
| iROAS trung bình | `Σ doanh thu tăng thêm nhờ ads / Σ spend` trên một giai đoạn | Đánh giá chiến dịch, slide |
| iROAS biên tại mức chi hiện tại | `d(doanh thu tăng thêm)/d(spend)` tại lịch chi hiện tại | Quyết định giảm/tăng ngân sách |
| iROAS hòa vốn | `eff_price / m` (m = đóng góp/sp, [05](05-kinh-te-va-mo-phong.md) §2) | iROAS < ngưỡng thì ads lỗ |

Ví dụ hòa vốn: `COCMEME`, giá 89.000đ, giá vốn 32.000đ, phí sàn 12%, đóng gói 3.000đ → m ≈ 43.300đ → hòa vốn ở iROAS ≈ 2,05. iROAS thật của chiến dịch là 0,64, tức ads đang lỗ dù ROAS sàn báo 0,88.

## 2. Vì sao ROAS sàn phóng đại

Sàn quy cho ads mọi đơn có tương tác với quảng cáo, kể cả người **vẫn sẽ mua** khi không có ads (khách tìm đúng tên shop rồi bấm vào kết quả quảng cáo). Trong mock, tỷ lệ đơn được quy cho ads là `min(0,9; 1,8 × u/(1+u))`, với `u` là mức tăng thật. Kết quả: ROAS sàn ≈ 1,5 lần iROAS.

Hệ quả: **không bao giờ dùng `attributed_orders` để ước lượng hiệu quả.** Cột này chỉ để hiển thị cạnh iROAS.

## 3. Nguồn biến thiên dùng được

Muốn biết ads tạo thêm bao nhiêu, cần các giờ "giống nhau trừ việc có ads hay không". Trong dữ liệu có 3 nguồn:

| Nguồn | Nhận diện trong `ad_hourly` | Mức sạch |
|---|---|---|
| Hết ngân sách cuối ngày | `status='budget_exhausted'` từ khoảng 19–22h; giờ hết khác nhau giữa các ngày | Sạch: thời điểm hết phụ thuộc chi, không phụ thuộc nhu cầu tương lai |
| Tắt ban đêm | `paused` 0–5h đều đặn ở một số chiến dịch; **ngày đôi không tắt** | Sạch, nhưng trùng giờ thấp điểm: cần offset nhịp giờ tốt |
| Seller tự tạm dừng | Đoạn `paused` 6–48 giờ giữa ngày thường | Sạch **trừ khi** tạm dừng vì sắp hết hàng |
| Tạm dừng vì sắp hết hàng | `paused` bắt đầu khi SKU chính `low_stock` | **Gây nhiễu, phải loại** (§4) |

## 4. Bẫy gây nhiễu và cách loại

Nhiều seller tắt ads **đúng lúc sắp hết hàng**, khi đó số bán thấp vì thiếu hàng chứ không phải vì tắt ads. So thẳng giờ bật với giờ tắt sẽ thổi phồng hiệu quả ads. Trong mock, 60% chiến dịch ngẫu nhiên có luật này (tồn SKU chính < 15).

Luật loại giờ (`ads_effect/dataset.py`), áp dụng theo chiến dịch:

1. Loại giờ `ad_status='unknown'` (ngày 20/09, giờ trễ cuối dữ liệu).
2. Loại giờ có **bất kỳ SKU nào chiếm ≥ 30% tỷ trọng bán** của chiến dịch đang `stockout` hoặc `low_stock`.
3. Đánh dấu **đoạn tạm dừng do tồn**: một đoạn `paused` liên tục mà trong 6 giờ trước khi bắt đầu có SKU chính `low_stock`. Loại cả đoạn và 6 giờ sau đó.
4. Loại 3 ngày đầu sau khi chiến dịch bắt đầu hoặc SKU ra mắt (nhu cầu chưa ổn định).
5. Ghi số giờ bị loại theo từng lý do vào `iroas_estimates.excluded_hours` để hiện ở tầng 3 của UI.

## 5. Mô hình

### 5.1 Dạng

Đơn vị: chiến dịch `c`, giờ `t`. `Y_ct` = tổng `units_gross` của các SKU trong chiến dịch (combo tính theo đơn combo).

```
Y_ct ~ Poisson-quasi( μ_ct )
log μ_ct = α_{c,d(t)}                      # mức của chiến dịch trong ngày (fixed effect theo ngày)
         + log B_{h(t), w(t)}              # nhịp giờ × thứ (offset, cố định)
         + log(1 + a_c · (1 − exp(−A_ct / K_c)))      # hiệu ứng ads, bão hòa
A_ct = spend_ct + δ · A_{c,t−1}            # adstock: ads còn tác dụng sau khi tắt
```

- **Fixed effect theo ngày** `α_{c,d}` hấp thụ mọi thứ thay đổi theo ngày: giá/voucher, ngày đôi, mùa vụ, trend, thứ hạng tự nhiên (thay đổi chậm). Nhờ vậy chỉ cần biến thiên **trong ngày** để nhận diện ads, tức đúng 3 nguồn ở §3.
- **Offset nhịp giờ `B_{h,w}`** ước lượng từ **SKU không chạy ads** (khoảng 30% sản phẩm): nhịp tự nhiên không lẫn ads. Thiếu SKU không ads trong cùng nhóm thì dùng nhịp shop từ các giờ `paused` ngày thường. Giả định: SKU có ads và không ads cùng nhịp giờ. Đúng trong mock; với dữ liệu thật phải kiểm theo ngành hàng.
- `K_c = κ · s̄_c`, với `s̄_c` = chi trung bình mỗi giờ khi chạy. `κ` không thứ nguyên, nên grid dùng chung cho mọi chiến dịch.

### 5.2 Ước lượng: posterior trên lưới (đơn giản, minh bạch, đủ nhanh)

```
Lưới:  δ ∈ {0,4; 0,5; 0,6; 0,7; 0,8}          (chung cho shop, ước lượng trước)
       κ ∈ {0,5; 1; 1,5; 2,5; 4; 6}
       a ∈ {0; 0,05; …; 3,0}                   (61 giá trị)
Bước 1 (δ chung): với mỗi δ, cộng profile log-likelihood tốt nhất của mọi chiến dịch → chọn δ̂
                  (mock: δ = 0,6; kỳ vọng δ̂ ∈ {0,5; 0,6; 0,7})
Bước 2 (từng chiến dịch, δ = δ̂): với mỗi (κ, a):
     g_t = 1 + a·(1 − exp(−A_t/K))
     α_d có nghiệm đóng (MLE Poisson): α_d = log( Σ_{t∈d} Y_t / Σ_{t∈d} B_t·g_t )
     ll(κ, a) = Σ_t [ Y_t·log μ_t − μ_t ]
Prior:  a ~ LogNormal(log 0,4; 0,8) cắt [0; 3] (a = 0 nhận khối lượng của ô đầu), κ đều
Posterior(κ, a) ∝ exp( ll / φ ) · prior,   φ = hệ số quá phân tán (Pearson χ²/df của mô hình tốt nhất)
```

Chia `ll` cho `φ` (quasi-likelihood) để khoảng tin cậy không quá hẹp, vì số đơn dao động mạnh hơn Poisson.

Mỗi ô lưới cho ra một iROAS:

```
inc_t(κ,a)  = e^{α_d} · B_t · (g_t − 1)                 # đơn tăng thêm ở giờ t
iROAS(κ,a)  = Σ_t inc_t · eff_price_t / Σ_t spend_t
iROAS_biên  = Σ_t e^{α_d}·B_t·a·exp(−A_t/K)/K · (∂A_t/∂spend) · eff_price / … (tính số bằng sai phân +1% chi)
```

Trung bình có trọng số posterior → `iroas_mean`; quantile có trọng số → `iroas_p10`, `iroas_p90`. Lưới 6 × 61 = 366 ô × 50 chiến dịch × ~2.000 giờ: vector hóa numpy theo trục `a`, chạy < 10 giây cho demo.

### 5.3 Kiểm tra chéo bằng so sánh ghép cặp

Một ước lượng thô để cảnh giác khi model sai dạng. Với mỗi giờ `t` mà chiến dịch `budget_exhausted` ở ngày `d`, ghép với **cùng giờ** ở các ngày thường khác có chi:

```
ratio = Σ Y(giờ hết ngân sách) / Σ Ŷ_cùng-giờ(ngày có chi, đã chuẩn hóa theo α_d)
```

Nếu `1/ratio − 1` (mức tăng ước lượng thô) lệch quá 2 lần so với `a` của model, gắn cờ `MODEL_CHECK_DISAGREE` và hạ nhãn xuống `reference`.

### 5.4 Từ chiến dịch xuống SKU

Trong một chiến dịch không tách được `a` riêng cho từng SKU, vì ngân sách chung bật/tắt cùng lúc. Giả định mọi SKU cùng `a_c`; phần tăng thêm của SKU `i` tỷ lệ với nhu cầu nền của nó. Simulator dùng `a_c, K_c, δ̂` cho từng SKU với `K_i = K_c · share_i`.

UI ghi rõ ở tầng 3: "Hiệu quả ads ước lượng theo chiến dịch, chia cho SKU theo tỷ trọng bán".

### 5.5 Giới hạn phải nói rõ

- iROAS ở đây là **ngắn hạn**: phần tăng thêm trong vài giờ có chi và ngay sau đó. Ads còn đẩy **thứ hạng tự nhiên** qua nhiều ngày; phần này bị fixed effect theo ngày hấp thụ, nên **không được tính**. iROAS dài hạn có thể cao hơn. Simulator có tham số `rank_loss_after_pause` ở dạng kịch bản ([05](05-kinh-te-va-mo-phong.md) §3).
- Mock dùng cùng nhịp giờ cho mọi SKU nên offset rất chuẩn. Dữ liệu thật sẽ nhiễu hơn, nên bắt buộc giữ kiểm tra chéo §5.3.

## 6. Nhãn tin cậy cho iROAS

```python
def iroas_label(n_clean_off_hours: int, rel_width: float, check_ok: bool) -> Label:
    # n_clean_off_hours: số giờ không chi (paused/budget_exhausted) còn lại sau khi loại nhiễu
    # rel_width = (p90 - p10) / mean
    if n_clean_off_hours < 12:
        return "scenario"            # gần như chỉ còn prior
    if n_clean_off_hours >= 48 and rel_width <= 0.6 and check_ok:
        return "verified"
    return "reference"
```

Kỳ vọng: chiến dịch `MAYHUTMINI` (mới 7 ngày, ít giờ tắt) ra `scenario`. Các chiến dịch có tắt ban đêm và hết ngân sách đều đặn ra `verified`.

## 7. Bảng kết quả

```sql
CREATE TABLE IF NOT EXISTS iroas_estimates (
  estimate_id        VARCHAR PRIMARY KEY,
  campaign_id        VARCHAR NOT NULL,
  period_start       TIMESTAMPTZ NOT NULL,
  period_end         TIMESTAMPTZ NOT NULL,
  model_id           VARCHAR NOT NULL,          -- "adstock_grid@1.0.0"
  delta              DOUBLE NOT NULL,           -- δ̂
  kappa_mean         DOUBLE NOT NULL,
  a_mean             DOUBLE NOT NULL,
  posterior          JSON NOT NULL,             -- ô lưới có trọng số > 1e-4: [{kappa,a,w}]
  spend_total        BIGINT NOT NULL,
  inc_revenue_mean   BIGINT NOT NULL,
  iroas_mean         DOUBLE NOT NULL,
  iroas_p10          DOUBLE NOT NULL,
  iroas_p90          DOUBLE NOT NULL,
  marginal_iroas     DOUBLE,                    -- tại lịch chi 7 ngày gần nhất
  platform_roas      DOUBLE,                    -- ROAS sàn cùng giai đoạn, để so
  n_hours_used       INTEGER NOT NULL,
  n_clean_off_hours  INTEGER NOT NULL,
  excluded_hours     JSON NOT NULL,             -- {"unknown":24,"low_stock":130,"stock_pause":48,"launch":0}
  check_ratio        DOUBLE,                    -- §5.3
  confidence         VARCHAR NOT NULL,
  panel_version      VARCHAR NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL
);
```

```python
# app/ads_effect/iroas.py
class IroasEstimate(BaseModel):
    campaign_id: str
    iroas_mean: float; iroas_p10: float; iroas_p90: float
    marginal_iroas: float | None
    platform_roas: float | None
    breakeven_iroas: float | None          # điền khi có giá vốn (economics)
    confidence: Label
    reasons: list[str]                      # "130 giờ sắp hết hàng đã bị loại", "δ=0,6 dùng chung shop"
    def uplift(self, adstock: np.ndarray, share: float = 1.0, draws: int | None = None) -> np.ndarray:
        """Trả g−1 cho simulator; draws=n → lấy n mẫu (κ, a) từ posterior để mô phỏng bất định."""
```

Chạy lại: sau mỗi lần ingest lịch sử (`cli estimate-ads`) và mỗi đêm. Trong ca **không** ước lượng lại; chỉ dùng kết quả gần nhất, vì 1 giờ live thêm vào gần như không đổi được ước lượng.

## 8. Thử nghiệm switchback (giai đoạn sau, chỉ thiết kế)

Khi dữ liệu lịch sử không đủ giờ tắt sạch, app đề xuất seller chạy thử nghiệm có kiểm soát:

| Thông số | Giá trị đề xuất |
|---|---|
| Khi nào | Ngày thường, 09:00–23:00; không trong ngày đôi ±1, không trong khung flash sale hay cam kết |
| Khối | 3 giờ; ngẫu nhiên bật/tắt, cân bằng trong ngày (mỗi ngày tối thiểu 2 khối tắt) |
| Thời lượng | 7–10 ngày (≈ 30–45 khối) |
| Phân tích | Cùng mô hình §5 (fixed effect theo ngày + adstock), dữ liệu chỉ lấy khối thử nghiệm; không bỏ giờ đầu khối vì adstock đã mô hình hóa carryover |
| Rào chắn | Seller đồng ý trên UI; giới hạn doanh thu có thể mất ≤ 5% doanh thu ngày; dừng ngay nếu SKU chính `low_stock` |
| Ước lượng cỡ mẫu | Chạy mô phỏng trên `mock-data` (cần thêm chế độ chạy theo chính sách cho bộ sinh) để xem bao nhiêu khối thì `rel_width ≤ 0,4` |

Giao diện và API cho phần này không làm ở hackathon. Chỉ trình bày trên slide "Lộ trình".

## 9. Nghiệm thu (`backend/eval/eval_ads.py`)

So `iroas_estimates` (giai đoạn lịch sử) với `ground_truth/chien_dich_that.csv` (`iroas_that`, `roas_san_bao_cao`):

| Kiểm tra | Ngưỡng |
|---|---|
| Phủ khoảng: chiến dịch có chi lịch sử ≥ 2 triệu đ có `iroas_that ∈ [p10, p90]` | ≥ 70% chiến dịch |
| Sai số tương đối trung vị `|iroas_mean − iroas_that| / iroas_that` | ≤ 25% |
| 6 chiến dịch nhân vật (CD001–CD006) | Mỗi chiến dịch sai ≤ 30% |
| Chiều: `iroas_mean < roas_san_bao_cao` | 100% chiến dịch có `iroas_that < roas_san_bao_cao` |
| Quyết định lỗ/lời: dấu của `iroas_mean − hòa vốn` trùng dấu `iroas_that − hòa vốn` | ≥ 90% chiến dịch; `CD004` (cốc meme) ra "lỗ" |
| Bẫy gây nhiễu: chạy lại **không** loại giờ sắp hết hàng (§4 tắt) | Sai số trung vị tăng rõ trên các chiến dịch có luật tắt theo tồn (in số cho slide) |
| δ̂ | ∈ {0,5; 0,6; 0,7} |
| Hiệu năng | Ước lượng toàn bộ chiến dịch demo < 10 giây |

Bảng cho slide (lấy số thật sau khi chạy): `chiến dịch | ROAS sàn | iROAS ước lượng [p10–p90] | iROAS thật | hòa vốn`. Số đã biết từ `ground_truth` của bộ demo seed `20261111`: CD002 nồi chiên ROAS sàn 8,11, iROAS thật 5,27; CD004 cốc meme 0,88 và 0,64.

## Tiêu chí hoàn thành

- [ ] `dataset.py` loại giờ theo đủ 5 luật, ghi `excluded_hours`.
- [ ] Offset nhịp giờ ước lượng từ SKU không ads.
- [ ] Posterior lưới cho mọi chiến dịch, ghi `iroas_estimates`, có nhãn.
- [ ] Kiểm tra chéo §5.3 chạy và gắn cờ khi lệch.
- [ ] `IroasEstimate.uplift()` dùng được trong simulator (có chế độ lấy mẫu posterior).
- [ ] `eval_ads.py` đạt các ngưỡng §9, xuất bảng cho slide.

## Task gợi ý

| Mã | Việc | Người | nd |
|---|---|---|---|
| ADS-01 | `dataset.py`: tổng hợp theo chiến dịch × giờ, 5 luật loại giờ, đoạn tạm dừng do tồn | D2 | 1 |
| ADS-02 | Offset nhịp giờ `B_{h,w}` từ SKU không ads | D2 | 0,5 |
| ADS-03 | `model.py`: posterior lưới (δ chung, κ, a), quasi-likelihood, vector hóa | D2 | 1,5 |
| ADS-04 | `iroas.py`: iROAS trung bình, biên, quantile, nhãn, `uplift()` | D2 | 0,75 |
| ADS-05 | Kiểm tra chéo ghép cặp giờ hết ngân sách | D2 | 0,5 |
| ADS-06 | DDL `iroas_estimates` + CLI `estimate-ads` | D1 | 0,25 |
| EVA-01 | `eval/eval_ads.py` + bảng slide + chạy thử "không loại nhiễu" | D2 | 0,75 |
