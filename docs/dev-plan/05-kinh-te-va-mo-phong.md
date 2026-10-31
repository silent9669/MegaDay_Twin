# 05 · Kinh tế và mô phỏng quyết định

Lớp quyết định ghép **dự báo nhu cầu, hiệu quả ads, giá vốn, tồn kho và mục tiêu** để so sánh 5 hành động trên từng chiến dịch. Đầu ra là một đề xuất có nhãn tin cậy, kèm "điều gì làm đổi kết luận".

Mọi so sánh chạy trên cùng một bộ mẫu Monte Carlo, nên khi app nói "A tốt hơn B" thì kèm được xác suất. Khi xác suất không đủ cao, app nói "chưa phân định".

## 1. Đầu vào và đầu ra

```python
# app/simulator/engine.py
class SimRequest(BaseModel):
    campaign_id: str
    start: datetime                       # thường = giờ tiếp theo sau as_of
    horizon_hours: int                    # mặc định: tới hạn của goal, tối đa 72
    goal_id: str
    actions: list[ActionSpec] | None = None   # None = 5 hành động mặc định
    param_overrides: dict[str, float] = {}    # what-if của người dùng / AI
    samples: int = 1000
    seed: int = 20261111

class ActionSpec(BaseModel):
    kind: Literal["continue", "pause", "reduce_budget", "schedule_hours", "pause_until_restock"]
    pct: float | None = None              # reduce_budget: 0.3 = giảm 30%
    windows_local: list[tuple[int, int]] | None = None   # schedule_hours: [(0,2),(12,14)] giờ VN, [đầu, cuối)

class ScenarioResult(BaseModel):          # mỗi hành động một bản
    action: ActionSpec
    feasible: bool
    violations: list[str]                 # ràng buộc bị vi phạm
    objective: Dist                       # J (VND) theo mục tiêu; Dist = {mean, p10, p50, p90}
    contribution: Dist; spend: Dist; leftover_units: Dist; leftover_value: Dist
    units_sold: Dist
    stockout_hour_p50: datetime | None    # giờ hết hàng (trung vị) của SKU chính
    per_sku: list[SkuOutcome]

class Recommendation(BaseModel):
    recommendation_id: str
    campaign_id: str
    status: Literal["recommend", "undetermined", "none_meets_goal"]
    best: ActionSpec | None
    runner_up: ActionSpec | None
    p_best_beats_runner_up: float | None
    expected_gain_vnd: int | None
    zone_bar: ZoneBar | None              # thanh vùng cho tầng 1 (§7)
    flip_conditions: list[FlipCondition]  # "điều làm đổi kết luận" (§8)
    confidence: Label                     # min(nhãn forecast, nhãn iROAS, nhãn dữ liệu)
    reasons: list[str]
    inputs_ref: InputsRef                 # forecast_id[], estimate_id, param_version, goal_id, data_as_of
```

## 2. Đóng góp mỗi sản phẩm

```
m(sku, t) = eff_price(t) − unit_cost − platform_fee_rate · eff_price(t) − packaging_cost
```

| Thành phần | Nguồn | Ghi chú |
|---|---|---|
| `eff_price(t)` | `price_events`; ngày 11.11 lấy `committed_price` | Không được giả định tăng giá trong chương trình |
| `unit_cost`, `packaging_cost` | `cost_items` (bản mới nhất có `valid_from ≤ t`) | Thiếu → **chặn**, kết quả `scenario` + lý do "Thiếu giá vốn" |
| `platform_fee_rate` | `parameters` (mặc định 0,12) | |
| Combo | `eff_price(combo) − Σ unit_cost(thành phần) − phí − đóng gói(combo)` | |

Ví dụ (demo, ngày 11.11): `NOICHIEN2L` giá cam kết 531.000đ, giá vốn 455.000đ, phí 63.720đ, đóng gói 3.000đ → **m = 9.280đ/sp**. Ngày thường ở giá niêm yết 690.000đ thì m = 149.200đ. Giá sale làm biên lợi nhuận mỏng đi 16 lần, nên chi ads ngày 11.11 là bài toán hoàn toàn khác ngày thường.

## 3. Danh mục tham số

```sql
CREATE TABLE IF NOT EXISTS parameters (
  key          VARCHAR NOT NULL,
  scope        VARCHAR NOT NULL,          -- shop|sku|campaign|session
  scope_id     VARCHAR NOT NULL DEFAULT '*',
  value        DOUBLE,
  value_json   JSON,                      -- cho tham số dạng danh sách/bảng
  version      INTEGER NOT NULL,
  set_by       VARCHAR NOT NULL,          -- system|operator|lead|ai_suggested_confirmed
  set_at       TIMESTAMPTZ NOT NULL,
  reason       VARCHAR,
  PRIMARY KEY (key, scope, scope_id, version)
);
```

Định nghĩa tham số nằm trong code (`economics/parameters.py`), không nằm trong DB. DB chỉ lưu giá trị đã đặt.

```python
class ParamDef(BaseModel):
    key: str; label_vi: str; unit: str
    default: float; min: float; max: float
    scopes: set[str]; editable_by: set[str]; source_hint: str
```

| key | Nhãn | Đơn vị | Mặc định | Khoảng | Phạm vi | Ai sửa | Nguồn |
|---|---|---|---|---|---|---|---|
| `platform_fee_rate` | Phí sàn | tỷ lệ | 0,12 | 0–0,3 | shop | lead | seller |
| `leftover_value_per_unit` | Giá trị ròng 1 sp còn lại sau hạn, so với giá vốn (V) | đ/sp | 0 | −giá vốn – giá bán | sku, session | operator | seller |
| `horizon_end` | Hạn quyết định | thời điểm | hết ngày hiện tại | ≤ 72 giờ | session | operator | goal |
| `safety_stock` | Tồn an toàn không bán | sp | 0 | 0–∞ | sku | operator | seller |
| `stockout_penalty_per_unit` | Phạt mỗi sp thiếu hàng (hủy đơn, uy tín) | đ/sp | 0 | 0–∞ | shop | lead | kịch bản |
| `rank_loss_after_pause` | Đơn tự nhiên mất thêm sau khi pause (thứ hạng) | tỷ lệ/ngày | 0 | 0–0,3 | campaign | lead | kịch bản |
| `restock_assumed_hour` | Giờ hàng về trong ngày dự kiến | giờ VN | 10 | 0–23 | shop | operator | seller |
| `restock_delay_days` | Hàng về trễ (kịch bản) | ngày | 0 | 0–7 | sku | operator | kịch bản |
| `reduce_budget_options` | Mức giảm ngân sách để thử | danh sách | [0,3; 0,5] | 0,1–0,9 | shop | operator | system |
| `min_marginal_return` | Đóng góp tăng thêm tối thiểu / 1đ chi (mục tiêu efficiency) | tỷ lệ | 1,0 | 0–5 | campaign | lead | seller |
| `max_loss_vnd` | Lỗ tối đa chấp nhận khi xả hàng | đ | 0 | 0–∞ | session | lead | seller |
| `decision_min_prob` | Xác suất tối thiểu để đề xuất | tỷ lệ | 0,7 | 0,5–0,95 | shop | lead | system |
| `decision_min_gain_vnd` | Lợi ích tối thiểu để đề xuất | đ | 200.000 | 0–∞ | shop | lead | system |
| `sell_target_prob` | Xác suất cần đạt mục tiêu số lượng | tỷ lệ | 0,8 | 0,5–0,99 | session | operator | system |
| `mc_samples` | Số mẫu Monte Carlo | mẫu | 1000 | 200–5000 | shop | system | system |

Quy tắc:
- Mỗi lần đổi tham số tạo `version` mới. Mọi `scenario_results` lưu `param_version`, nên kết quả cũ luôn tái lập được.
- AI chỉ được **đề xuất** giá trị (`ai_suggested`). Người dùng xác nhận mới thành `ai_suggested_confirmed`.
- Tham số "kịch bản" (`rank_loss_after_pause`, `stockout_penalty_per_unit`, `restock_delay_days`) khác 0 thì nhãn kết quả không vượt quá `reference`. Lý do hiện trên UI: "đang dùng giả định".
- Giá trị ngoài `[min, max]` → `422` với thông báo tiếng Việt.

**Giá trị hàng dư V (sửa sau M3-whatif).** `V` là giá trị **ròng** của 1 sp còn lại sau hạn **so với giá vốn**: tiền thu hồi khi bán sau hạn, trừ giá vốn và chi phí bán tiếp (giữ hàng, vốn, phí). Cùng gốc với `m` (đã trừ giá vốn) trong `J` ở §4.
- `V = 0` (mặc định): sp dư bán sau vẫn hòa vốn. Đúng cho hàng nhập đều: sp dư hôm nay chỉ thay cho một sp phải nhập tuần sau.
- `V < 0`: thu hồi dưới giá vốn (hàng mùa vụ, hàng ế). `V = −giá vốn` là hàng dư mất trắng. Đây là chặn dưới.
- `V > 0`: sp dư còn mang về lãi sau hạn. Không bao giờ vượt `m` ngày thường. Chặn trên vẫn là giá bán.
- Mặc định cũ `0,5 × giá vốn` sai kinh tế: nồi chiên V = 227.500đ, lớn hơn cả lãi ngày thường 149.200đ. Hậu quả: 27/31 chiến dịch ngày thường ra pause chỉ vì "giữ hàng lợi hơn bán".

## 4. Mục tiêu → hàm mục tiêu + ràng buộc

```sql
CREATE TABLE IF NOT EXISTS goals (
  goal_id      VARCHAR PRIMARY KEY,
  scope        VARCHAR NOT NULL,           -- campaign|sku
  scope_id     VARCHAR NOT NULL,
  kind         VARCHAR NOT NULL,           -- profit|sell_target|clear_stock|efficiency
  deadline     TIMESTAMPTZ NOT NULL,
  target_units INTEGER,                    -- sell_target
  params       JSON,                       -- override riêng của goal (V, max_loss, ...)
  confirmed_by VARCHAR NOT NULL,           -- người xác nhận; AI không tự tạo goal
  created_at   TIMESTAMPTZ NOT NULL,
  active       BOOLEAN NOT NULL DEFAULT TRUE
);
```

Với mỗi mẫu `s` và hành động `a`, trên horizon tới `deadline`:

```
J_s(a) = Σ_t Σ_i m_i(t)·sold_{s,i,t}(a)           # đóng góp
       − Σ_t spend_{s,t}(a)                          # chi ads
       + Σ_i V_i · stock_end_{s,i}(a)                # giá trị hàng dư, RÒNG so với giá vốn (§3)
       − penalty · Σ_t Σ_i lost_{s,i,t}(a)           # nhu cầu không phục vụ được (mặc định 0)
```

| Mục tiêu | Chọn hành động | Ràng buộc thêm | Không hành động nào đạt |
|---|---|---|---|
| `profit` | max `E[J]` | — | Không xảy ra |
| `sell_target` | Trong các hành động có `P(Σ sold ≥ N) ≥ sell_target_prob`: max `E[J]` | Bán đủ N trước hạn | `none_meets_goal`, hiện xác suất đạt của từng hành động |
| `clear_stock` | max `E[Σ sold]` | `E[J] ≥ −max_loss_vnd` | `none_meets_goal` |
| `efficiency` | max `E[J]` | `(E[J(a)] − E[J(pause)]) / E[spend(a)] ≥ min_marginal_return` cho mọi phần chi thêm | Mặc định chọn pause |

`none_meets_goal` đúng như tài liệu gốc trang 7: app báo "các lựa chọn này chưa giải quyết được", chuyển người có quyền xem phương án khác. App **không tự** giảm giá hay đổi mục tiêu.

## 5. Bộ mô phỏng

### 5.1 Lịch chi theo hành động (`simulator/policies.py`)

`profile_spend_c[h]` = tỷ trọng chi theo giờ của chiến dịch, lấy trung bình 7 ngày cùng bối cảnh gần nhất. Ngày đôi dùng ngày đôi gần nhất (10.10).

| Hành động | Chi giờ t (trước khi chặn bởi ngân sách còn lại) |
|---|---|
| `continue` | `budget_day · profile_spend_c[h]` |
| `pause` | 0 |
| `reduce_budget(pct)` | `(1 − pct) · budget_day · profile_spend_c[h]` |
| `schedule_hours(windows)` | `budget_day · profile_spend_c[h]` nếu `h ∈ windows`, ngược lại 0 (không dồn ngân sách sang giờ khác) |
| `pause_until_restock` | 0 tới giờ hàng về dự kiến (`planned_date + restock_assumed_hour + restock_delay_days`), sau đó như `continue` |

`budget_day` lấy từ `ad_hourly.daily_budget` mới nhất (ngày đôi đã gấp 3). Ngân sách còn lại trong ngày = `budget_day − chi đã tiêu`. Mỗi ngày mới thì đặt lại.

### 5.2 Nhu cầu theo hành động

Với SKU `i` trong chiến dịch:

```
base_{i,t}   = λ_sq_{i,t} / (1 + u_sq_{i,t})            # nhu cầu không có ads
u_{i,t}(a)   = a_c · (1 − exp(−A_{i,t}(a) / K_i))
A_{i,t}(a)   = share_i · spend_t(a) + δ · A_{i,t−1}(a),   A_{i,t0−1} = adstock hiện tại từ panel
λ_{i,t}(a)   = base_{i,t} · (1 + u_{i,t}(a)) · rank_factor_t(a)
rank_factor  = 1 − rank_loss_after_pause · (số ngày đã tắt), chỉ khi tham số > 0
```

`u_sq` là uplift dưới lịch chi status quo, tức lịch `continue`, vì forecast học trên dữ liệu có ads ([03](03-lop-mo-hinh.md) §1).

### 5.3 Lấy mẫu (vector hóa, cùng số ngẫu nhiên cho mọi hành động)

```
Mảng [S, T], S = 1000 mẫu, T ≤ 72 giờ:
  Lmult[s]      ~ LogNormal(0, level_sigma_i)                 # bất định mức nền, chung cho mọi giờ của mẫu
  (κ_s, a_s)    ~ posterior lưới của iroas_estimates           # bất định hiệu quả ads
  G[s, t]       ~ Gamma(k_i, 1/k_i)                            # dao động NegBin
  demand[s, t]  = Poisson( λ_{i,t}(a; κ_s, a_s) · Lmult[s] · G[s, t] )
```

**Common random numbers:** `Lmult`, `(κ_s, a_s)`, `G` và chuỗi seed Poisson **giống hệt** cho mọi hành động. Chênh lệch giữa hai hành động khi đó chỉ do hành động gây ra, và `P(A tốt hơn B) = mean_s[J_s(A) > J_s(B)]` có ý nghĩa với S = 1000. Poisson: tạo `Generator(seed_i)` mới cho mỗi hành động với cùng `seed_i`.

### 5.4 Tồn kho và ràng buộc trong vòng lặp giờ (`simulator/constraints.py`)

Vòng lặp theo `t` (T ≤ 72 bước), mỗi bước vector hóa trên trục mẫu `S`:

```
Với mỗi t:
  1. Hàng về: stock_i += qty nếu t = giờ về dự kiến (+ trễ kịch bản)
  2. Tồn bán được ngoài flash:   avail_i = stock_i − reserve_left_i − safety_stock_i
     Trong khung flash sale:      avail_i = stock_i − safety_stock_i   (bán cả phần giữ)
  3. SKU thường trước:  sold_i = min(demand_i, max(avail_i, 0)); stock_i −= sold_i
     Combo sau:         avail_combo = min_j(avail_j / qty_j); sold_combo = min(demand_combo, avail_combo);
                        stock_j −= qty_j · sold_combo     (với mọi thành phần j)
  4. Ghi lost = demand − sold; reserve_left giảm theo phần bán trong flash
```

Ràng buộc kiểm **trước** khi mô phỏng. Hành động vi phạm thì `feasible=false`, có `violations`, không được đề xuất, nhưng vẫn hiển thị để người dùng thấy cái giá của ràng buộc.

| Ràng buộc | Nguồn | Vi phạm khi |
|---|---|---|
| Không tắt chiến dịch trong khung flash sale | `sale_commitments(kind='campaign_must_run')` | `pause`, `pause_until_restock`, `schedule_hours` không phủ khung flash, `reduce_budget` về 0 trong khung |
| Không tăng giá SKU đã cam kết | `committed_price` | (bản này không có hành động đổi giá; kiểm cho what-if) |
| Không tắt riêng SKU | `campaigns.sku_level_control=false` | What-if kiểu "tắt riêng SKU X" → trả gợi ý tách chiến dịch, không mô phỏng |
| Không bán phần tồn giữ cho flash ngoài khung | `flash_reserve` | Đã áp trong vòng lặp |

### 5.5 Hiệu năng

| Thao tác | Mục tiêu | Cách đạt |
|---|---|---|
| 1 chiến dịch, 5 hành động, 1000 mẫu, 24 giờ | < 300 ms | numpy `[S, T]`, vòng lặp 24 bước |
| 50 chiến dịch ưu tiên | < 2 giây | Chạy song song bằng `ThreadPoolExecutor` (numpy nhả GIL) |
| Kéo tham số trên UI | < 300 ms | Gọi lại với `samples=300`; đủ cho thanh vùng, bản đầy đủ chạy nền |
| Độ nhạy (§8) | < 1,5 giây / chiến dịch | Bisection 6 bước × `samples=300` |

Cache kết quả theo khóa `(campaign_id, forecast_ids, estimate_id, param_version, goal_id, as_of)`. Chi tiết ở [06](06-dieu-phoi-va-hieu-nang.md).

## 6. Bảng lưu kết quả

```sql
CREATE TABLE IF NOT EXISTS scenarios (
  scenario_id   VARCHAR PRIMARY KEY,
  campaign_id   VARCHAR NOT NULL,
  goal_id       VARCHAR NOT NULL,
  start_at      TIMESTAMPTZ NOT NULL,
  horizon_hours INTEGER NOT NULL,
  param_version JSON NOT NULL,             -- {key: version} các tham số đã dùng
  overrides     JSON,                      -- what-if của người dùng/AI
  inputs_ref    JSON NOT NULL,             -- forecast_id[], estimate_id, data_as_of, panel_version
  samples       INTEGER NOT NULL,
  seed          BIGINT NOT NULL,
  requested_by  VARCHAR NOT NULL,          -- orchestrator|user|ai
  created_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS scenario_results (
  scenario_id   VARCHAR NOT NULL REFERENCES scenarios(scenario_id),
  action        JSON NOT NULL,             -- ActionSpec
  feasible      BOOLEAN NOT NULL,
  violations    VARCHAR[],
  objective     JSON NOT NULL,             -- Dist
  contribution  JSON NOT NULL, spend JSON NOT NULL, units_sold JSON NOT NULL,
  leftover_units JSON NOT NULL, leftover_value JSON NOT NULL,
  stockout_hour_p50 TIMESTAMPTZ,
  per_sku       JSON NOT NULL,
  PRIMARY KEY (scenario_id, action)
);

CREATE TABLE IF NOT EXISTS recommendations (
  recommendation_id VARCHAR PRIMARY KEY,
  scenario_id   VARCHAR NOT NULL REFERENCES scenarios(scenario_id),
  campaign_id   VARCHAR NOT NULL,
  status        VARCHAR NOT NULL,          -- recommend|undetermined|none_meets_goal
  best_action   JSON, runner_up JSON,
  p_best        DOUBLE, expected_gain_vnd BIGINT,
  zone_bar      JSON,
  flip_conditions JSON,
  confidence    VARCHAR NOT NULL,
  reasons       VARCHAR[],
  superseded_by VARCHAR,                   -- đề xuất mới thay thế (không xóa bản cũ)
  created_at    TIMESTAMPTZ NOT NULL
);
```

## 7. Ngưỡng hòa vốn và thanh vùng (tầng 1 của UI)

### 7.1 Công thức đóng (trường hợp tốc độ bán đều)

Còn `n` sp, còn `H` giờ, đóng góp `m` đ/sp, giá trị hàng dư `V` đ/sp. Gọi `Π_c` là kết quả của `continue` (đã gồm chi ads và giá trị hàng dư của nó). Nếu pause bán được `q` sp thì:

```
Π_p(q) = m·q + V·(n − q) − spend_p              (spend_p = 0 với pause)
Hòa vốn khi Π_p(q*) = Π_c   ⇒   q* = (Π_c + spend_p − n·V) / (m − V)
Ngưỡng tốc độ: x* = q* / H     (sp/giờ)
Nếu m ≤ V: giữ hàng luôn lợi hơn bán, pause thắng mọi trường hợp → zone_bar = null, reason tương ứng
q* ≤ 0 → pause thắng chắc; q* > n → continue thắng chắc (pause không thể bán quá n)
```

**Ví dụ trong tài liệu gốc (trang 10), bắt buộc có unit test:** `n = 120`, `H = 4`, `m = 45.000`, tiếp tục bán đều 40 sp/giờ và chi 450.000đ/giờ tới khi hết hàng (3 giờ).

```
Π_c = 120 × 45.000 − 3 × 450.000 = 4.050.000đ
V = 0:       q* = 4.050.000 / 45.000 = 90 sp → x* = 22,5 sp/giờ
V = 20.000:  q* = (4.050.000 − 120 × 20.000) / 25.000 = 66 sp → x* = 16,5 sp/giờ
```

### 7.2 Thanh vùng trong trường hợp thật

Dùng kết quả mô phỏng thay cho giả định bán đều: `Π_c = E[J(continue)]`, còn phân phối tốc độ bán khi pause lấy từ `units_sold(pause) / H` của mô phỏng.

```python
class ZoneBar(BaseModel):
    axis_label: str          # "Lượng bán mỗi giờ nếu pause (sp)"
    threshold: float         # x*
    interval: tuple[float, float, float]   # p10, p50, p90 của tốc độ bán khi pause
    verdict: Literal["pause_better", "continue_better", "undetermined"]
    alt_thresholds: list[AltThreshold]     # vd x* khi V = 20.000đ (đường nét đứt trên UI)
```

`verdict`: p10 > x* → `pause_better`; p90 < x* → `continue_better`; còn lại `undetermined`.

Pause bị cấm (vd cam kết flash sale): thanh vẫn trả về, `verdict` vẫn là so sánh tiền, thêm `pause_feasible = false`, `pause_violations` và `verdict_note`. UI hiện `verdict_note` thay cho câu "pause tốt hơn": thanh lúc này cho thấy cái giá của ràng buộc, không phải đề xuất (nhật ký M3-mo-phong Q14).

Thanh vùng so `continue` với `pause` (câu hỏi trung tâm của sản phẩm). Hành động tốt nhất trong cả 5 hiện ở dòng đề xuất ngay dưới thanh. Hành động tốt nhất là `schedule_hours` hay `reduce_budget` thì UI ghi "Phương án tốt hơn cả hai: …".

## 8. Điều làm đổi kết luận (độ nhạy)

Với đề xuất `best` và `runner_up`, tìm giá trị của từng yếu tố làm hai hành động **đổi thứ tự**. Dùng bisection trên `E[J(best) − J(runner_up)] = 0`, `samples=300`, cùng seed:

| Yếu tố | Khoảng tìm | Câu hiển thị (mẫu) |
|---|---|---|
| `leftover_value_per_unit` (V) | [−giá vốn, giá bán] | "Nếu hàng dư còn giá trị từ {V*}đ/sp trở lên → {runner_up} tốt hơn" |
| Mức nhu cầu (nhân `Lmult` cố định) | [p10, p90] của forecast | "Nếu nhu cầu thấp hơn dự báo {x}% → …" |
| Hiệu quả ads (a) | [p10, p90] của posterior | "Nếu ads hiệu quả kém hơn ước lượng {x}% → …" |
| `restock_delay_days` | [0, 7] | "Nếu hàng về trễ từ {d} ngày → …" |

Chỉ hiện yếu tố có điểm đổi **nằm trong khoảng hợp lý** (khoảng tìm ở trên). Không có điểm đổi thì ghi "Kết luận giữ nguyên trong khoảng bất định hiện tại".

Unit test theo ví dụ trang 10: dự báo pause p10 = 18 sp/giờ → điểm đổi `V* = (4.050.000 − 72 × 45.000) / (120 − 72) = 16.875đ`.

## 9. Quy tắc ra đề xuất (`simulator/recommend.py`)

```
1. Loại hành động feasible = false.
2. Xếp theo hàm mục tiêu của goal (§4). Nếu không còn hành động nào đạt ràng buộc goal → none_meets_goal.
3. best, runner_up = hai hành động đứng đầu, bỏ hành động có kết quả TRÙNG HỆT một hành động
   xếp trên (vd pause ≡ pause_until_restock khi không có hàng về trong horizon).
   Mục tiêu có ràng buộc (sell_target, clear_stock): runner_up lấy trong các hành động đạt mục tiêu.
   Chỉ một hành động đạt → recommend; runner_up = hành động tốt nhất KHÔNG đạt (để tham chiếu),
   p = gain = null, goal_cost_vnd = E[J(runner_up)] − E[J(best)], lý do ghi rõ "Chỉ X đạt mục tiêu…".
   p = mean_s[ J_s(best) > J_s(runner_up) ];  gain = E[J(best)] − E[J(runner_up)]
4. Nếu p ≥ decision_min_prob và gain ≥ max(decision_min_gain_vnd, 5% · |E[J(best)] − E[V·tồn cuối(best)]|)
   → recommend, ngược lại → undetermined (vẫn hiện best/runner_up, thanh vùng, điều làm đổi).
   Mẫu số 5% bỏ phần giá trị hàng dư: phần đó tỷ lệ với lượng tồn, không với cái đang được quyết.
5. confidence = min theo thứ tự scenario < reference < verified của:
   nhãn forecast các SKU chiếm ≥ 80% doanh thu chiến dịch, nhãn iROAS, nhãn dữ liệu
   (dữ liệu cũ, thiếu giá vốn, ads unknown → scenario), có tham số kịch bản ≠ 0 → tối đa reference
6. reasons: 3 câu ngắn, mỗi câu kèm số (UI tầng 2), ví dụ
   "Nhu cầu 11.11 dự kiến 140 sp, tồn 83 sp: ads không bán thêm được khi hết hàng"
```

Đề xuất mới khác đề xuất cũ thì đặt `superseded_by` cho bản cũ và phát `RecommendationChanged`. Không xóa bản cũ (nhật ký, [10](10-quan-tri-va-bao-mat.md)).

## 10. Chạy tay: chiến dịch nồi chiên (CD002) ngày 11.11

Số lấy từ bộ `demo` seed `20261111`. Đây là ước tính tay để kiểm logic; simulator sẽ tính chính xác hơn.

| Dữ kiện | Giá trị | Nguồn |
|---|---|---|
| SKU trong chiến dịch | `NOICHIEN2L`, `GIAYLOT50`, `COMBO-NOICHIEN` | `cau_truc_chien_dich.json` |
| Tồn `NOICHIEN2L` đầu ngày | 83 sp (lúc 01:00 còn 76) | sheet tồn 10/11 và live |
| Hàng về | 400 sp, dự kiến 12/11 | "Đang về" |
| Giữ cho flash sale | 23 nồi chiên, 9 combo | `chuong_trinh_sale.json` |
| Ràng buộc | Không tắt CD002 trong 00:00–02:00 và 12:00–14:00 | `quy_dinh_chien_dich` |
| Đóng góp | nồi chiên 9.280đ/sp; combo ≈ 29.100đ/sp (574.000 − 473.000 − 68.880 − 3.000) | §2 |
| Ngân sách ngày | 5.592.000đ (ngày đôi gấp 3) | báo cáo ads |
| Nhu cầu (đáp án, chỉ để kiểm) | nồi chiên 145, combo 31 → **vượt xa tồn 83** | `ground_truth` |

Lập luận app phải tái tạo được:

1. Nhu cầu cả ngày vượt tồn nhiều lần, nên **mọi hành động đều bán hết khoảng 83 nồi** (cộng combo bị chặn bởi cùng tồn nồi chiên). Số bán gần như không phụ thuộc ads.
2. Đóng góp bán hàng gần như bằng nhau giữa các hành động: khoảng 68 × 9.280 + 15 × 29.100 ≈ 1,07 triệu đ.
3. Khác biệt nằm ở chi ads:
   - `continue`: tiêu gần hết 5,59 triệu đ → J ≈ −4,5 triệu đ.
   - `schedule_hours([(0,2),(12,14)])`: chỉ chi trong 4 giờ flash. Theo nhịp chi ngày đôi, khoảng 20–25% ngân sách, tức ≈ 1,1–1,4 triệu đ → J ≈ −0,1 đến −0,3 triệu đ.
   - `pause` và `pause_until_restock`: J cao nhất, nhưng **vi phạm cam kết flash sale**, nên `feasible=false`.
4. Đề xuất: **`schedule_hours` (chỉ chạy trong khung flash sale)**, lợi hơn `continue` khoảng 4 triệu đ, `p ≈ 1`. Lý do hiện cho người dùng:
   - "Hàng sẽ hết khoảng {giờ}: ads không bán thêm được khi hết hàng."
   - "Ở giá 11.11 mỗi nồi chỉ lãi 9.280đ."
   - "Không được tắt hẳn vì đã cam kết với sàn trong khung flash sale."
5. Điều làm đổi kết luận: "Nếu hàng về sớm trong hôm nay, chạy tiếp có lợi hơn" (yếu tố `restock_delay_days` âm, tức hàng về sớm). Hoặc không có điểm đổi trong khoảng hợp lý.

Ngược lại, ngày thường (m = 149.200đ, iROAS ≈ 5) thì `continue` thắng rõ. Đây là cặp ví dụ hay cho slide: **cùng chiến dịch, cùng ROAS sàn 8,1, nhưng kết luận ngược nhau** vì giá, tồn và cam kết.

Test e2e `tests/e2e/test_noichien_1111.py`: ingest `demo` tới 11/11 01:00 → simulator CD002 mục tiêu `profit` → kỳ vọng `best.kind == "schedule_hours"`, `pause` có violation `campaign_must_run`.

## 11. Nghiệm thu

| Kiểm tra | Ngưỡng |
|---|---|
| Unit test ví dụ trang 10: x* = 22,5 (V=0), 16,5 (V=20.000), V* = 16.875 | Đúng tuyệt đối |
| `m ≤ V`, `q* ≤ 0`, `q* > n` xử lý đúng | Có test |
| Common random numbers: chạy 2 lần cùng seed → kết quả giống hệt; `P(A>B)` ổn định ±0,02 giữa seed | Pass |
| Ràng buộc flash sale chặn `pause` CD002 ngày 11.11 | Pass |
| Combo trừ tồn thành phần; không bao giờ bán âm tồn (property test, 1000 trường hợp ngẫu nhiên) | Pass |
| e2e nồi chiên 11.11 (§10) | `schedule_hours` |
| e2e cốc meme ngày thường, mục tiêu `profit` | `pause` hoặc `reduce_budget` (iROAS < hòa vốn) |
| e2e đèn LED Tết, mục tiêu `clear_stock` hạn 31/01 | Không ra `pause`; có `flip_conditions` theo V |
| Hiệu năng (§5.5) | 1 chiến dịch < 300 ms; 50 chiến dịch < 2 giây |
| Mọi `Recommendation` có `inputs_ref` đầy đủ, tái lập lại ra cùng kết quả | Pass |

## Tiêu chí hoàn thành

- [ ] `contribution.py` + `parameters.py` (danh mục §3, có version) + `goals.py` (4 mục tiêu).
- [ ] `policies.py` 5 hành động; `engine.py` Monte Carlo vector hóa với common random numbers.
- [ ] `constraints.py`: tồn, combo, flash reserve, `campaign_must_run`, `sku_level_control`.
- [ ] `breakeven.py`: công thức đóng + thanh vùng + độ nhạy, có unit test trang 10.
- [ ] `recommend.py` theo §9, ghi `scenarios`, `scenario_results`, `recommendations`.
- [ ] Các test e2e ở §11 pass.

## Task gợi ý

| Mã | Việc | Người | nd |
|---|---|---|---|
| ECO-01 | `contribution.py` (gồm combo) + chặn khi thiếu giá vốn | D2 | 0,5 |
| ECO-02 | `parameters.py` danh mục + bảng `parameters` + API đọc/ghi có version | D3 | 1 |
| ECO-03 | `goals.py` + bảng `goals` + 4 hàm mục tiêu | D2 | 0,75 |
| ECO-04 | `breakeven.py` công thức đóng + `ZoneBar` + unit test trang 10 | D2 | 0,5 |
| SIM-01 | `policies.py`: lịch chi 5 hành động, `profile_spend` | D2 | 0,75 |
| SIM-02 | `engine.py`: lấy mẫu [S,T], common random numbers, posterior iROAS | D2 | 1,5 |
| SIM-03 | `constraints.py`: vòng lặp tồn, combo, flash reserve, kiểm ràng buộc | D2 | 1 |
| SIM-04 | Độ nhạy (bisection) + `FlipCondition` | D2 | 0,75 |
| SIM-05 | `recommend.py` + DDL `scenarios`, `scenario_results`, `recommendations` | D3 | 0,75 |
| SIM-06 | Test e2e §10, §11 + benchmark hiệu năng | D2 | 0,75 |
