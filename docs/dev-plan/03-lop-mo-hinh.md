# 03 · Lớp mô hình: dự báo nhu cầu

Lớp này dự báo **nhu cầu thật theo giờ** (không phải số bán) cho từng SKU, kèm khoảng p10–p90. Mỗi dự báo gắn nhãn tin cậy lấy từ backtest thật. Bản hackathon dùng 6 model thống kê đơn giản, cắm qua cùng một interface. Model nào làm "champion" cho từng (nhóm nhu cầu, bối cảnh) là do backtest quyết định, không do người viết code chọn.

## 1. Biến mục tiêu và quy ước

- **Mục tiêu dự báo:** `demand(sku, giờ)` = số sp khách *muốn* mua trong giờ (đơn không hủy), **dưới chính sách ads hiện tại** (status quo, tức lịch chi như các ngày gần đây).
- Giờ hết hàng không quan sát được nhu cầu, vì `units_gross` bị chặn bởi tồn. Đây là dữ liệu bị kiểm duyệt (censored), xử lý ở §4.
- Tách bạch với lớp ads: forecast trả nhu cầu status quo `λ_sq`; module `ads_effect` trả phần **tăng thêm** do ads `inc(spend)`. Simulator ghép lại: `λ_policy(t) = λ_sq(t) − inc_sq(t) + inc_policy(t)` ([05](05-kinh-te-va-mo-phong.md) §5).
- Horizon: tới **72 giờ** theo giờ (dùng cho quyết định trong ca) và **14 ngày** theo ngày (cho xả hàng, hàng Tết).
- Hạt dự báo theo giờ, nhưng nhiều SKU bán < 1 sp/giờ. Vì vậy metric chính tính ở hạt **SKU × ngày** (§7).

## 2. Interface

```python
# app/forecast/base.py
class ForecastRequest(BaseModel):
    sku_id: str
    origin: datetime                 # dữ liệu dùng tới (không gồm) mốc này, UTC
    horizon_hours: int = 72
    context: Literal["normal", "double_day", "gift_season", "new"] | None = None   # None = tự suy
    price_plan: dict[datetime, int] | None = None   # giá hiệu lực dự kiến theo giờ (11.11: giá cam kết)

class ForecastResult(BaseModel):
    forecast_id: str
    sku_id: str
    model_id: str                    # "profile_ewma@1.2.0"
    origin: datetime
    hours: list[datetime]            # UTC, len = horizon
    mean: list[float]                # λ_sq(t)
    p10: list[float]
    p50: list[float]
    p90: list[float]
    dispersion_k: float              # tham số Negative Binomial
    level_sigma: float               # độ bất định của mức nền (log-scale)
    cum_p10: list[float]             # quantile của tổng lũy kế từ giờ đầu (cho quyết định tồn)
    cum_p50: list[float]
    cum_p90: list[float]
    validated_scope: ValidatedScope  # model này đã backtest ở nhóm/bối cảnh nào, điểm bao nhiêu
    confidence: Literal["verified", "reference", "scenario"]
    reasons: list[str]               # vd "Chỉ có 7 ngày lịch sử", "Giờ 9–14 hôm qua hết hàng, đã hiệu chỉnh"
    data_as_of: datetime
    panel_version: str

class ForecastModel(Protocol):
    name: str
    version: str
    applicable_groups: set[str]      # nhóm nhu cầu model hỗ trợ
    def fit(self, panel: pd.DataFrame, sku_id: str, origin: datetime, ctx: FitContext) -> FittedState: ...
    def predict(self, state: FittedState, req: ForecastRequest) -> ForecastResult: ...
```

`FitContext` chứa thông tin dùng chung toàn shop, tính một lần mỗi lần dựng panel: nhịp giờ shop (`P_shop[h]`), hệ số thứ, đường sự kiện ngày đôi, đường mùa vụ theo loại, độ co giãn giá theo nhóm. Các model đọc `FitContext` thay vì tự ước lượng, để SKU ít dữ liệu vẫn có tham số ổn định (gộp thông tin, *pooling*).

Sinh quantile cho mọi model bằng cùng một hàm, để các model so sánh được:

```python
def nb_quantiles(mean: np.ndarray, k: float, level_sigma: float, rng, n=2000) -> Quantiles:
    """Hỗn hợp: mức nền ~ LogNormal(0, level_sigma), số đơn ~ NegBin(mean·L, k).
    Trả p10/p50/p90 theo giờ và của tổng lũy kế. Vector hóa: mẫu [n, T]."""
```

## 3. Các model

Ký hiệu: `h` = giờ VN trong ngày, `w` = thứ, `d` = ngày, `disc` = % giảm giá.

### 3.1 `profile_ewma`: model mặc định cho hàng ổn định

```
λ(t) = L · P_h · W_w · E(t) · (1 − disc_t)^β · S_kind(d)
```

| Thành phần | Ước lượng |
|---|---|
| `L` (sp/ngày) | Trung bình mũ (EWMA, nửa đời 7 ngày) của **nhu cầu đã hiệu chỉnh kiểm duyệt** theo ngày (§4), chỉ dùng ngày thường (bỏ ngày đôi ±1, ngày có voucher) |
| `P_h` | Nhịp giờ của SKU, co về nhịp shop: `P_h = (n_h + κ·P_shop[h]) / (N + κ)`, `κ = 50` sp. `n_h` = số bán ở giờ h trong 28 ngày, chỉ tính giờ còn hàng |
| `W_w` | Hệ số thứ của shop (7 số, chuẩn hóa trung bình 1), ước lượng từ tổng đơn ngày thường |
| `E(t)` | Hệ số sự kiện theo giờ: với ngày đôi, `E(h) = trung vị qua 8.8/9.9/10.10 của (bán giờ h ngày đôi) / (kỳ vọng giờ h ngày thường)`, gộp theo nhóm. Ngày trước ngày đôi, ngày sau, ngày 25 có một hệ số riêng |
| `β` | Độ co giãn giá theo nhóm (hồi quy log(bán ngày) theo log(1 − disc) trên ngày có voucher). Mặc định −1,8, giới hạn [−3, −0,5] |
| `S_kind(d)` | 1 (không mùa vụ). Lớp con `seasonal_calendar` thay bằng đường mùa vụ |

### 3.2 `seasonal_calendar`: hàng mùa vụ

Như `profile_ewma` nhưng `S_kind(d)` là đường theo **số ngày tới sự kiện**, ước lượng chung cho mọi SKU cùng loại (`qua_tang`, `tet`, `mua_dong`):

- `qua_tang`: đường tỷ lệ `bán(d) / L_trước_mùa` theo `d − 20/10`, lấy từ dữ liệu 01/10–31/10. Dùng lại **cùng hình dạng cho 20/11**, vì lịch sử chưa có đợt 20/11 (giả định này phải hiện trong `reasons`).
- `tet`: hàm `1 + a·exp(−days_to_tet/τ)`, fit `a, τ` trên dữ liệu tháng 9–11. Tết còn xa nên đường gần phẳng. `reasons` ghi rõ "chưa có dữ liệu mùa Tết, ngoại suy".
- `mua_dong`: logistic theo ngày, fit trên tháng 9–11.

Phân loại `kind` lấy từ phân nhóm tự động (§6) hoặc do người dùng gán.

### 3.3 `trend_damped`: hàng theo trend

Holt có tắt dần (damped trend) trên `log(1 + nhu cầu ngày)`, tham số `α=0,4, β=0,2, φ=0,85` (grid nhỏ chọn theo backtest). Phân bổ xuống giờ bằng `P_h`. Ghi pha vào `reasons`: `tăng` (độ dốc 7 ngày > +5%/ngày), `đỉnh`, `nguội` (< −3%/ngày).

`COCMEME-*` phải ra pha `nguội` từ khoảng 25/10.

### 3.4 `tsb`: hàng bán thưa

Teunter–Syntetos–Babai ở hạt ngày: `p_d` = xác suất có bán (cập nhật mỗi ngày, `α_p = 0,1`), `z_d` = cỡ bán khi có bán (cập nhật khi có bán, `α_z = 0,1`). `λ_ngày = p·z`, phân bổ xuống giờ bằng `P_shop`. Khoảng dự báo theo hỗn hợp Bernoulli × Poisson. Không dùng hệ số giờ riêng của SKU vì quá ít dữ liệu.

### 3.5 `analog`: hàng mới (< 14 ngày)

Mượn SKU tương tự: cùng `product_id` gốc nếu là biến thể mới; nếu không thì cùng nhóm template (tiền tố mã) hoặc cùng khoảng giá ±30% trong nhóm `moi`/`on_dinh`.

```
L_analog = trung vị mức bán ngày 7–21 sau ra mắt của các SKU tương tự (đã scale theo giá)
L = (n_obs·mean_obs + κ·L_analog) / (n_obs + κ),  κ = 7 ngày
level_sigma = 0,6 (rộng), giảm dần theo n_obs
```

Không có SKU tương tự thì `L_analog` = trung vị nhóm `moi`. **Nhãn luôn là `scenario`** khi < 14 ngày lịch sử, dù backtest tốt (`MAYHUTMINI`).

### 3.6 `nowcast`: nhu cầu đột biến trong ca

Chỉ dùng khi drift CUSUM báo động (§9). Ước lượng lại mức từ 3–6 giờ gần nhất với trọng số mũ (nửa đời 2 giờ), giữ nhịp giờ:

```
L_now = Σ_j w_j · y_j / Σ_j w_j · P_h(j) · E(j)    (j = các giờ gần nhất còn hàng)
λ(t) = L_now · P_h · E(t),  level_sigma = 0,5
```

Dự báo `nowcast` chỉ có nhãn tối đa `reference` và kèm lý do "nhu cầu đang lệch mạnh so với lịch sử". `BINHNUOC500-*` ngày 11.11 phải chuyển sang `nowcast` trong vòng 2–3 giờ sau khi bắt đầu viral (khoảng 12:00–15:00).

## 4. Xử lý nhu cầu bị chặn bởi tồn (censoring)

Không xử lý thì model học rằng "giờ hết hàng = không ai mua" và **dự báo thấp có hệ thống** đúng những SKU bán chạy nhất.

Ước lượng mức nền **chỉ trên giờ còn hàng**:

```
L̂ = Σ_{t ∉ stockout} y_t  /  Σ_{t ∉ stockout} P_h(t)·W_w(t)·E(t)·(1−disc_t)^β
```

Mẫu số chỉ cộng "phần kỳ vọng" của các giờ còn hàng, nên mức nền không bị kéo xuống. Giờ `stockout_conf < 0,7` (tồn nội suy) được tính nửa trọng số.

Nhu cầu ngày đã hiệu chỉnh (dùng cho EWMA và cho chấm điểm nội bộ):

```
demand_adj(d) = Σ_{t ∈ d, còn hàng} y_t + Σ_{t ∈ d, hết hàng} L̂·P_h·W·E·(…)
```

`reasons` thêm câu "N giờ hết hàng trong 7 ngày qua, đã hiệu chỉnh nhu cầu" khi N > 0.

Kiểm chứng (§10): trên các SKU có ≥ 5 giờ hết hàng trong cửa sổ backtest, độ lệch (bias) so với `nhu_cau_that` phải gần 0 hơn hẳn bản không hiệu chỉnh.

**Hết hàng kéo dài** (M2, nhật ký `M2-du-bao.md` "Hoàn thiện M2"). Khi cửa sổ 14 ngày còn < 3 ngày "phần kỳ vọng" còn hàng, `L̂` không ước lượng trên vài giờ cuối (nhiễu, cũ) nữa mà lấy:

```
L(d) = L_pre · g_sib · g_trend
L_pre   = mức trên 7 ngày phần kỳ vọng còn hàng gần nhất (lùi xa tùy cần) — mức trước khi hết
g_sib   = (Σ_J L_J(ℓ) / Σ_J L_J(t̄)) ^ (0,85^(d − ℓ))   J = biến thể cùng sản phẩm, ℓ = ngày cuối còn quan sát
g_trend = exp(slope · φ(1 − φⁿ)/(1 − φ)), φ = 0,85     chỉ nhóm trend, slope = OLS log L trước khi hết
```

Giờ hết hàng có `stockout_conf < 0,7` chỉ cộng nửa phần hiệu chỉnh. Nhu cầu thay đổi trong lúc **mọi** biến thể đều hết hàng thì không khôi phục được: trên `demo` (dd_1010) phần lệch còn lại −34,7% đến từ 9 SKU trend không có giờ còn hàng nào trong 14 ngày trước mốc và từ hiệu ứng chọn mẫu; phép thử "lịch sử hoàn hảo" cho −6,4%.

## 5. Bất định

- `k` (dispersion NegBin): ước lượng theo nhóm bằng method of moments trên phần dư giờ còn hàng: `k = mean² / (var − mean)`, cắt trong [0,5, 50]. Mock dùng `k = 3`. Ước lượng ra 2–5 là hợp lý.
- `level_sigma`: sai số chuẩn của `log L̂` (bootstrap theo ngày, 200 lần) cộng sàn 0,1. Ngày đôi cộng thêm 0,15, vì chỉ có 3 ngày đôi để học `E(t)`.
- Quantile tổng lũy kế quan trọng hơn quantile từng giờ khi quyết định về tồn ("đến 15:00 bán hết chưa?"), nên luôn trả `cum_p10/p50/p90`.

## 6. Phân nhóm nhu cầu tự động (`forecast/classify.py`)

Tính trên 56 ngày gần nhất, dùng `demand_adj`:

| Đặc trưng | Định nghĩa |
|---|---|
| `age_days` | Số ngày từ đơn đầu tiên |
| `ADI` | Khoảng cách trung bình giữa hai ngày có bán |
| `CV2` | (độ lệch chuẩn / trung bình)² của cỡ bán ngày có bán |
| `slope28` | Hệ số góc hồi quy `log(1 + demand)` theo ngày, 28 ngày gần nhất |
| `peak_ratio` | Trung bình 7 ngày cao nhất / trung vị 56 ngày |
| `seasonal_corr[kind]` | Tương quan giữa `demand_adj` và đường `S_kind` |

Luật theo thứ tự (dừng ở luật đầu tiên đúng):

1. `age_days < 14` → `moi`
2. `ADI > 1,32` → `ban_thua` (ngưỡng Syntetos–Boylan)
3. `max(seasonal_corr) ≥ 0,6` và `peak_ratio ≥ 2` → `mua_vu` (`kind` = argmax)
4. `peak_ratio ≥ 3` hoặc `|slope28| ≥ 0,04` → `trend`
5. Còn lại → `on_dinh`

Lưu vào `skus.demand_group`, `demand_group_source='auto'`, `demand_group_at`. Người dùng sửa được (`source='user'`, giữ nguyên tới khi chính họ đổi). Khi chưa đủ đặc trưng thì `unclassified` (đúng tài liệu gốc trang 3).

Nghiệm thu: so với `sku_that.nhom_nhu_cau`, độ chính xác ≥ 80% trên SKU có ≥ 28 ngày, và **đúng 100% với nhân vật demo**. `BINHNUOC500-*` là `on_dinh` (viral chỉ xảy ra 11.11), `COCMEME-*` là `trend`, `AOGIUNHIET-*` và `DENLEDTET-*` là `mua_vu`, `MAYHUTMINI` là `moi`.

## 7. Backtest

### 7.1 Protocol rolling-origin

Không bao giờ để dữ liệu sau `origin` lọt vào fit (kể cả `FitContext`: phải tính lại với dữ liệu < origin). Test `test_no_future_leak` thay dữ liệu sau origin bằng NaN và kiểm dự báo không đổi.

| Tên | Origin (giờ VN) | Horizon | Bối cảnh | Vì sao |
|---|---|---|---|---|
| `dd_0808` | 07/08 00:00 | 72 h (07–09/08) | double_day | Ngày đôi đầu tiên (chưa có ngày đôi nào để học `E`: phải dùng mặc định) |
| `dd_0909` | 08/09 00:00 | 72 h (08–10/09) | double_day | Có 1 ngày đôi để học |
| `dd_1010` | 09/10 00:00 | 72 h (09–11/10) | double_day | **Mốc chính** cho slide |
| `gift_1020` | 15/10 00:00 | 168 h (15–21/10) | gift_season | Đỉnh 20/10 |
| `normal_roll` | 28/10 → 10/11, mỗi ngày 00:00 | 24 h | normal | 14 lần, ước lượng ổn định cho ngày thường |
| `live_1111` | 11/11 00:00 | 24 h | double_day | Chấm sau khi phát lại xong |

Các khoảng này trùng đúng các cửa sổ có dữ liệu giờ trong `ground_truth/nhu_cau_theo_gio.csv`. `backend/eval/` chấm theo giờ được, còn `app/` chỉ tự chấm theo `hourly_panel` (số bán).

**Hai loại chấm điểm, không được nhầm:**
- **Trong app (`forecast/backtest.py`)**: so với dữ liệu app có, tức số bán quan sát. Giờ hết hàng bị bỏ khỏi metric. Kết quả ghi `model_scores`, dùng để chọn champion và gắn nhãn. Chạy được với dữ liệu thật.
- **Ngoài app (`backend/eval/eval_forecast.py`)**: so với `nhu_cau_that` trong `ground_truth`. Chỉ có với mock. Dùng để chứng minh trên slide và kiểm việc hiệu chỉnh censoring.

### 7.2 Metric

Với `y` = thực tế, `ŷ` = p50, tổng trên tập (SKU, giờ) của một backtest:

```
WAPE      = Σ|y − ŷ| / Σ y                         (hạt SKU × ngày: cộng giờ trong ngày trước)
Bias      = (Σŷ − Σy) / Σy
Pinball_q = mean( max(q·(y − ŷ_q), (q − 1)·(y − ŷ_q)) ),  q ∈ {0,1; 0,5; 0,9}   (hạt giờ)
Coverage  = tỷ lệ (SKU, ngày) có PIT ngẫu nhiên hóa của tổng ngày u ∈ [0,1; 0,9]   (mục tiêu 80%)
            u = F(y−1) + V·(F(y) − F(y−1)),  V ~ U(0, 1) có seed,  F = phân phối dự báo của tổng ngày
WAPE_floor = Σ E|Y − p50_Y| / Σ E[Y],  Y ~ NegBin(mean dự báo, k) theo giờ, cộng thành ngày
             (Monte Carlo có seed, KHÔNG gồm level_sigma), trên đúng tập (SKU, ngày) của WAPE
```

Tính theo `(model, nhóm nhu cầu, bối cảnh)`. Chỉ tính SKU có ≥ 1 sp/ngày cho WAPE và WAPE_floor (SKU thưa dùng pinball). Trong app, mọi đại lượng tính trên giờ còn hàng (§7.1) và không đọc đáp án.

**Vì sao đổi (M2, nhật ký `M2-du-bao.md` §D).** Cách đo cũ cho sai kết luận trên số đếm nhỏ: (1) `y ∈ [p10, p90]` đếm trọn khối xác suất ở hai mép, nên ngay cả phân phối **đúng** cũng phủ ≈ 90% (thử bằng λ thật của bộ sinh: 86–91%); PIT ngẫu nhiên hóa cho đúng 80% khi phân phối đúng. (2) "WAPE ≤ 30%" là ngưỡng tuyệt đối, nhưng nhiễu Poisson/NegBin của SKU 2–10 sp/ngày đã là ~29–35% WAPE (dự báo bằng λ thật trên `demo`: 35%); WAPE_floor là phần sai không tránh được, do app tự tính từ dự báo của mình. `level_sigma` không nằm trong floor: đó là độ mơ hồ của chính model, đưa vào thì model càng mơ hồ ngưỡng càng dễ (thử nghiệm: dự báo ×1,5 qua được ngày 10.10).

### 7.3 Nhãn tin cậy (`forecast/labels.py`)

```python
WAPE_FLOOR_TOL = 0.10   # WAPE được vượt mức sai không tránh được tối đa 10% (baseline ngây thơ phải trượt)

def confidence_label(scope: ScoreRow | None, history_days: int, drift_active: bool) -> Label:
    if history_days < 14 or scope is None:
        return "scenario"
    ok = (scope.wape <= (1 + WAPE_FLOOR_TOL) * scope.wape_floor   # §7.2
          and 0.72 <= scope.coverage <= 0.88                       # coverage PIT, §7.2
          and scope.n_sku_days >= 30)
    label = "verified" if ok else "reference"
    if drift_active and label == "verified":
        label = "reference"
    return label
```

`model_scores.wape_floor` lưu WAPE_floor của từng dòng điểm. Làm khoảng dự báo rộng ra không giúp qua nhãn: floor không đổi (không gồm `level_sigma`) mà coverage PIT vượt 0,88.

`scope` = điểm của champion trên **cùng nhóm + cùng bối cảnh** với yêu cầu. 11.11 có bối cảnh `double_day`, nên dùng điểm `dd_*`, không dùng điểm ngày thường. Không có điểm cho đúng bối cảnh thì mặc định `reference`, kèm lý do "chưa kiểm ở bối cảnh này".

## 8. Model registry và champion/challenger

```sql
CREATE TABLE IF NOT EXISTS model_registry (
  model_id     VARCHAR PRIMARY KEY,      -- "profile_ewma@1.2.0"
  name         VARCHAR NOT NULL,
  version      VARCHAR NOT NULL,
  kind         VARCHAR NOT NULL,         -- forecast|ads_effect
  params       JSON,                     -- siêu tham số mặc định
  code_hash    VARCHAR NOT NULL,         -- hash file model, để biết code đã đổi
  created_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS model_scores (
  score_id     VARCHAR PRIMARY KEY,
  model_id     VARCHAR NOT NULL REFERENCES model_registry(model_id),
  backtest     VARCHAR NOT NULL,         -- dd_1010, normal_roll, ...
  demand_group VARCHAR NOT NULL,
  context      VARCHAR NOT NULL,
  n_skus       INTEGER, n_sku_days INTEGER,
  wape DOUBLE, bias DOUBLE, pinball_10 DOUBLE, pinball_50 DOUBLE, pinball_90 DOUBLE, coverage DOUBLE,
  is_champion  BOOLEAN NOT NULL DEFAULT FALSE,
  panel_version VARCHAR NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS forecasts (
  forecast_id  VARCHAR PRIMARY KEY,
  sku_id       VARCHAR NOT NULL,
  model_id     VARCHAR NOT NULL,
  origin       TIMESTAMPTZ NOT NULL,
  horizon_hours INTEGER NOT NULL,
  payload      JSON NOT NULL,            -- ForecastResult (mean/p10/p50/p90/cum_*)
  confidence   VARCHAR NOT NULL,
  reasons      VARCHAR[],
  data_as_of   TIMESTAMPTZ NOT NULL,
  panel_version VARCHAR NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL
);
```

Chọn champion cho mỗi `(demand_group, context)`:
1. Trong các model có `applicable_groups` chứa nhóm, chọn model có **WAPE thấp nhất**; nếu chênh < 2 điểm % thì chọn pinball_50 thấp hơn.
2. Challenger thay champion khi tốt hơn ≥ 5% WAPE ở **2 backtest liên tiếp** cùng bối cảnh. Tránh đổi model chỉ vì một lần may.
3. Ghi `is_champion` và lưu lịch sử đổi champion (bảng `governance` phiên bản, [10](10-quan-tri-va-bao-mat.md)).

Thêm model mới = thêm file trong `forecast/models/`, đăng ký trong `registry.py`. Không sửa chỗ nào khác (tiêu chí mô-đun hóa). Foundation model chuỗi thời gian (Chronos, TimesFM) là ứng viên giai đoạn sau, cắm vào đúng interface này và để backtest quyết định.

## 9. Phát hiện lệch (drift) bằng CUSUM

Chạy mỗi giờ trong ca cho SKU đang có forecast, chỉ trên giờ còn hàng:

```
z_t  = (y_t − λ_t) / sqrt(λ_t + λ_t²/k)        # phần dư chuẩn hóa NegBin
S⁺_t = max(0, S⁺_{t−1} + z_t − 0,5)
S⁻_t = max(0, S⁻_{t−1} − z_t − 0,5)
Báo động khi S⁺ > 5 (cao hơn dự báo) hoặc S⁻ > 5 (thấp hơn). Sau báo động đặt lại về 0.
```

**Sản phẩm nhiều biến thể** (M2 "Hoàn thiện M2"): biến thể chung cú sốc nhu cầu, nên chiều `up` chạy trên sản phẩm bằng CUSUM của log-likelihood ratio NegBin "nhu cầu = 2× dự báo", cộng trên các biến thể còn hàng:

```
LLR_v(t) = y·log(2λ(λ + k) / (λ(2λ + k))) + k·log((λ + k)/(2λ + k))
S_t = max(0, S_{t−1} + Σ_v LLR_v(t)),   báo up (cho mọi biến thể còn hàng) khi S_t > 6
```

Ngưỡng 6 chọn để tổng số báo động trên ngày quá khứ không vượt bản z-CUSUM từng SKU. Chiều `down` và sản phẩm một biến thể giữ công thức trên.

Báo động thì:
- phát `DriftDetected(sku_id, direction, stat)` → cảnh báo có lý do ([06](06-dieu-phoi-va-hieu-nang.md));
- nếu `direction=up`: kích hoạt `nowcast` cho SKU đó trong 24 giờ;
- nếu `direction=down`: **không tự kết luận hết trend**. Cảnh báo kèm danh sách cần kiểm (giá/voucher đổi, hết biến thể, ads tắt, dữ liệu trễ) cho agent điều tra ([07](07-lop-ai.md)).

Kỳ vọng trên demo:
- `BINHNUOC500-*` báo `up` giữa 12:00 và 15:00 ngày 11.11. (Trên `demo` viral của bộ sinh bắt đầu 7–8h; app báo 10:00 cả ba biến thể, tức 2–3 giờ sau khi bắt đầu, xem `M2-du-bao.md` H4.)
- `AOGIUNHIET-M` không báo `down` sai: giờ hết hàng bị loại khỏi CUSUM, nên app cảnh báo đúng "hết hàng", không phải "nhu cầu giảm".

## 10. Nghiệm thu (`backend/eval/eval_forecast.py`)

| Kiểm tra | Cách tính | Ngưỡng |
|---|---|---|
| WAPE ngày thường | `normal_roll`, nhóm `on_dinh`, champion, so với `nhu_cau_that` | WAPE ≤ 1,10 × WAPE_floor (§7.2) |
| Phép thử cách đo | Oracle = λ thật của bộ sinh với NegBin k = 3; dự báo kém có chủ đích (champion ×1,5; baseline 7 ngày cùng giờ) | Oracle đạt cả WAPE/floor lẫn coverage PIT; WAPE_floor của app cách WAPE oracle ≤ 5 điểm; dự báo kém trượt |
| WAPE 10.10 | `dd_1010`, nhóm `on_dinh` + `mua_vu` | ≤ 40% (ghi số thật lên slide) |
| Coverage | PIT ngẫu nhiên hóa của tổng ngày (§7.2), `normal_roll` | 72–88% (in kèm số đo cũ `y ∈ [p10, p90]` để so) |
| Censoring | SKU có ≥ 5 giờ hết hàng trong `dd_1010`: `|bias|` bản hiệu chỉnh vs bản không hiệu chỉnh | Bản hiệu chỉnh `|bias|` ≤ 10%; bản không hiệu chỉnh âm rõ (in số thật cho slide) |
| So với chấm bằng `ban_duoc` | Cùng model, chấm với `ban_duoc` thay vì `nhu_cau_that` | Báo cáo chênh lệch: chứng minh "chấm bằng số bán đánh giá sai model" |
| Baseline | Champion so với "trung bình 7 ngày cùng giờ" (seasonal naive) | Champion tốt hơn ≥ 15% WAPE |
| Phân nhóm | So với `sku_that.nhom_nhu_cau` | ≥ 80% tổng; 100% nhân vật demo |
| Drift | Giờ báo động `BINHNUOC500-*` ngày 11.11 | Trong 12:00–15:00 |
| Không rò tương lai | `test_no_future_leak` | Pass |
| Hiệu năng | Fit + predict 72 h cho 300 SKU | < 30 giây fit, < 2 giây predict |

Báo cáo ghi `backend/eval/reports/forecast_<ngày>.md`, kèm bảng cho slide: WAPE champion vs baseline, coverage, minh họa censoring trên `NOICHIEN2L` ngày 10.10.

## Tiêu chí hoàn thành

- [ ] Interface `ForecastModel` + `ForecastResult` + `nb_quantiles` có test.
- [ ] 6 model chạy được trên `demo`, mỗi model có test với 1 nhân vật demo.
- [ ] `FitContext` tính từ dữ liệu < origin; `test_no_future_leak` pass.
- [ ] Backtest 6 mốc ghi `model_scores`, chọn champion, gắn nhãn đúng luật §7.3.
- [ ] CUSUM chạy theo giờ trong live, phát `DriftDetected`.
- [ ] `eval_forecast.py` đạt các ngưỡng §10 (hoặc ghi rõ số thật nếu chưa đạt, để quyết định có đưa lên slide không).

## Task gợi ý

| Mã | Việc | Người | nd |
|---|---|---|---|
| FC-01 | `base.py`, `nb_quantiles`, schemas, `FitContext` (nhịp shop, hệ số thứ, `E(t)`, β) | D2 | 1,5 |
| FC-02 | `censoring.py` + `profile_ewma` | D2 | 1 |
| FC-03 | `seasonal_calendar` (3 loại mùa vụ) | D2 | 1 |
| FC-04 | `trend_damped` + nhận pha | D2 | 0,75 |
| FC-05 | `tsb` cho hàng thưa | D2 | 0,5 |
| FC-06 | `analog` cho hàng mới | D2 | 0,5 |
| FC-07 | `nowcast` + `drift.py` (CUSUM) | D2 | 0,75 |
| FC-08 | `classify.py` phân nhóm + API sửa nhóm | D2 | 0,5 |
| FC-09 | `backtest.py` + `metrics.py` + `registry.py` + `labels.py` | D2 | 1,5 |
| FC-10 | DDL `model_registry`, `model_scores`, `forecasts` + repo | D1 | 0,25 |
| EVF-01 | `eval/eval_forecast.py` + báo cáo cho slide | D2 | 1 |
