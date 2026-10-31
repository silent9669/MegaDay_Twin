-- =============================================================================
-- MegaDay Twin · DuckDB schema (CONTRACT for every layer)
--
-- Source of truth for column names: docs/dev-plan 02 §4/§6, 03 §8, 04 §7,
-- 05 §3/§4/§6, 06 §7, 07 §9, 10 §3/§9. Every deviation from the plan is marked
-- "-- [DEV]" and explained in docs/quyet-dinh/INF-backend.md.
--
-- Rules
--   * Idempotent: only CREATE ... IF NOT EXISTS. migrate() runs this file on
--     every start. Changing an existing table = add a numbered migration in
--     app/warehouse/db.py (MIGRATIONS), never edit a CREATE in place after M0.
--   * Time: TIMESTAMPTZ holds UTC instants (the write connection sets
--     TimeZone='UTC'). DATE columns named *_local / day_local / *_date are VN
--     calendar dates.
--   * Money: INTEGER/BIGINT VND. Ratios: DOUBLE 0..1.
--   * Ids: ULID strings (app.core.ids.new_id) except natural keys.
-- =============================================================================

CREATE TABLE IF NOT EXISTS schema_version (               -- [DEV] migration bookkeeping
  version     INTEGER PRIMARY KEY,
  applied_at  TIMESTAMPTZ NOT NULL,
  note        VARCHAR
);

-- Primary demand labels are independent of the historical forecasting profile.
CREATE TABLE IF NOT EXISTS sku_demand_labels (
  entry_id VARCHAR PRIMARY KEY,
  sku_id VARCHAR NOT NULL,
  raw_code VARCHAR NOT NULL,
  inventory_sha256 VARCHAR NOT NULL,
  assessed_at TIMESTAMPTZ NOT NULL,
  synced_at TIMESTAMPTZ NOT NULL,
  payload JSON NOT NULL
);

-- ============================================================ 02 · data layer
CREATE TABLE IF NOT EXISTS ingest_batches (
  batch_id        VARCHAR PRIMARY KEY,           -- ULID
  source_type     VARCHAR NOT NULL,              -- orders|ads|inventory|cost|campaigns|sale_program
  file_name       VARCHAR NOT NULL,
  file_sha256     VARCHAR NOT NULL UNIQUE,
  received_at     TIMESTAMPTZ NOT NULL,
  as_of           TIMESTAMPTZ,                   -- data valid up to (last hour with data)
  row_count       INTEGER,
  dropped_columns VARCHAR[],                     -- PII columns dropped
  tz_offset_hours INTEGER DEFAULT 0,
  mode            VARCHAR NOT NULL,              -- history|live  (06 calls this source_kind)
  status          VARCHAR NOT NULL,              -- ok|ok_with_issues|failed
  duration_ms     INTEGER                        -- [DEV] 06 §6 measures import time here
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
  issue_id      VARCHAR PRIMARY KEY,
  batch_id      VARCHAR NOT NULL REFERENCES ingest_batches(batch_id),
  code          VARCHAR NOT NULL,                -- DUP_ORDER, NEG_STOCK, ... (02 §3.1)
  severity      VARCHAR NOT NULL,                -- info|warn|error  (error = "blocking" in 10)
  entity_type   VARCHAR,                         -- order|sku|campaign|snapshot|file
  entity_id     VARCHAR,
  row_ref       VARCHAR,                         -- "line 1043" or key
  detail        JSON,                            -- {"value": -7, "expected": 112}
  status        VARCHAR NOT NULL DEFAULT 'open', -- open|auto_fixed|confirmed|dismissed
  resolved_by   VARCHAR,
  created_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
  product_id    VARCHAR PRIMARY KEY,             -- platform SKU prefix before '-' (e.g. BINHNUOC500)
  name          VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS skus (
  sku_id        VARCHAR PRIMARY KEY,             -- platform SKU (canonical key), e.g. BINHNUOC500-XANH
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
  raw_code      VARCHAR NOT NULL DEFAULT '',     -- [DEV] '' instead of NULL when the source has no code
                                                 --       (NULLs are distinct in UNIQUE, so upsert would duplicate)
  raw_name      VARCHAR NOT NULL,
  norm_name     VARCHAR NOT NULL,
  sku_id        VARCHAR REFERENCES skus(sku_id), -- NULL = not matched yet
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
  buyer_note_masked VARCHAR,                     -- masked, UNTRUSTED text
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
  snapshot_at  TIMESTAMPTZ NOT NULL,             -- instant the stock is valid (end of day / end of hour)
  on_hand      INTEGER,                          -- NULL if the raw value was invalid
  quality_flag VARCHAR,                          -- negative|interpolated|NULL
  note         VARCHAR,
  granularity  VARCHAR NOT NULL,                 -- day|hour
  batch_id     VARCHAR NOT NULL,
  PRIMARY KEY (sku_id, snapshot_at)
);

CREATE TABLE IF NOT EXISTS restock_plans (           -- "Đang về" as declared by the seller
  sku_id        VARCHAR NOT NULL REFERENCES skus(sku_id),
  seen_at       TIMESTAMPTZ NOT NULL,            -- snapshot that showed the plan
  qty_incoming  INTEGER NOT NULL,
  planned_date  DATE NOT NULL,                   -- planned date (NOT the actual arrival)
  PRIMARY KEY (sku_id, seen_at)
);

CREATE TABLE IF NOT EXISTS restock_receipts (        -- actual receipts parsed from "Nhập {qty}"
  sku_id       VARCHAR NOT NULL REFERENCES skus(sku_id),
  received_on  DATE NOT NULL,
  qty          INTEGER NOT NULL,
  planned_date DATE,                             -- nearest restock_plans → delay measurement
  PRIMARY KEY (sku_id, received_on)
);

CREATE TABLE IF NOT EXISTS campaigns (
  campaign_id       VARCHAR PRIMARY KEY,
  name              VARCHAR NOT NULL,
  type              VARCHAR,                     -- Tìm kiếm|Khám phá|Tự động
  daily_budget      INTEGER NOT NULL,
  started_at        TIMESTAMPTZ,
  sku_level_control BOOLEAN NOT NULL             -- false if > 1 SKU
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
  spend              INTEGER,                    -- NULL when unknown
  impressions        INTEGER,
  clicks             INTEGER,
  attributed_orders  INTEGER,                    -- PLATFORM-attributed numbers
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

CREATE TABLE IF NOT EXISTS price_events (            -- derived from orders: effective price per day
  sku_id         VARCHAR NOT NULL REFERENCES skus(sku_id),
  day_local      DATE NOT NULL,
  list_price     INTEGER NOT NULL,
  eff_price      INTEGER NOT NULL,               -- median unit_price of the day
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
  value_int     INTEGER,                         -- price or quantity
  window_start  TIMESTAMPTZ,
  window_end    TIMESTAMPTZ,
  raw_text      VARCHAR
);

-- 02 §6 · base table SKU × hour. Write ordered by (sku_id, hour_utc) for zone maps.
CREATE TABLE IF NOT EXISTS hourly_panel (
  sku_id            VARCHAR NOT NULL,
  hour_utc          TIMESTAMPTZ NOT NULL,
  hour_local        TINYINT NOT NULL,           -- 0..23 VN hour
  dow_local         TINYINT NOT NULL,           -- 0=Sun .. 6=Sat
  day_local         DATE NOT NULL,
  units_gross       INTEGER NOT NULL,           -- every order except cancelled
  units_provisional INTEGER NOT NULL,           -- part from non-final orders
  units_cancelled   INTEGER NOT NULL,
  orders_count      INTEGER NOT NULL,
  revenue_net       BIGINT NOT NULL,            -- Σ unit_price·qty minus cancelled, minus returned
  list_price        INTEGER,
  eff_price         INTEGER,
  discount_pct      DOUBLE,
  stock_begin       INTEGER,                    -- estimated stock at start of hour
  stock_end         INTEGER,
  stock_source      VARCHAR,                    -- snapshot_hour|reconstructed|interpolated
  stockout          BOOLEAN NOT NULL,           -- demand may be censored this hour
  stockout_conf     DOUBLE,                     -- 0..1
  low_stock         BOOLEAN NOT NULL,           -- stock < 2 × recent daily demand
  restock_arrived   INTEGER DEFAULT 0,
  campaign_id       VARCHAR,
  ad_status         VARCHAR,                    -- running|paused|budget_exhausted|unknown|none
  ad_spend_alloc    DOUBLE,                     -- NULL when unknown
  ad_alloc_share    DOUBLE,
  attributed_orders_alloc DOUBLE,
  is_double_day     BOOLEAN NOT NULL,
  is_pre_double_day BOOLEAN NOT NULL,
  is_post_double_day BOOLEAN NOT NULL,
  is_payday         BOOLEAN NOT NULL,           -- 25th
  in_flash_window   BOOLEAN NOT NULL,
  days_to_tet       INTEGER,                    -- Tết 2027-02-06
  days_to_gift_peak INTEGER,                    -- to nearest 20/10 or 20/11
  data_flags        VARCHAR[],                  -- {'ADS_GAP_DAY','PROVISIONAL'}
  panel_version     VARCHAR NOT NULL,           -- latest batch_id affecting the row
  PRIMARY KEY (sku_id, hour_utc)
);

-- ============================================================ 03 · forecasting
CREATE TABLE IF NOT EXISTS model_registry (
  model_id     VARCHAR PRIMARY KEY,      -- "profile_ewma@1.2.0"
  name         VARCHAR NOT NULL,
  version      VARCHAR NOT NULL,
  kind         VARCHAR NOT NULL,         -- forecast|ads_effect
  params       JSON,                     -- default hyper-parameters
  code_hash    VARCHAR NOT NULL,         -- hash of the model file
  created_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS model_scores (
  score_id     VARCHAR PRIMARY KEY,
  model_id     VARCHAR NOT NULL REFERENCES model_registry(model_id),
  backtest     VARCHAR NOT NULL,         -- dd_1010, normal_roll, ...
  demand_group VARCHAR NOT NULL,
  context      VARCHAR NOT NULL,
  n_skus       INTEGER,
  n_sku_days   INTEGER,
  wape         DOUBLE,
  bias         DOUBLE,
  pinball_10   DOUBLE,
  pinball_50   DOUBLE,
  pinball_90   DOUBLE,
  coverage     DOUBLE,                   -- randomised-PIT coverage of the 80 % interval (M2)
  is_champion  BOOLEAN NOT NULL DEFAULT FALSE,
  panel_version VARCHAR NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL,
  wape_floor   DOUBLE                    -- [DEV] M2: expected WAPE under the model's own
                                         --       predictive distribution (03 §7.2, §7.3)
);

CREATE TABLE IF NOT EXISTS forecasts (
  forecast_id  VARCHAR PRIMARY KEY,
  sku_id       VARCHAR NOT NULL,
  model_id     VARCHAR NOT NULL,
  origin       TIMESTAMPTZ NOT NULL,
  horizon_hours INTEGER NOT NULL,
  payload      JSON NOT NULL,            -- ForecastResult (mean/p10/p50/p90/cum_*)
  confidence   VARCHAR NOT NULL,         -- verified|reference|scenario
  reasons      VARCHAR[],
  data_as_of   TIMESTAMPTZ NOT NULL,
  panel_version VARCHAR NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL,
  input_hash   VARCHAR                   -- [DEV] 06 §3: final results stored with input_hash
);

-- ============================================================ 04 · ads effect
CREATE TABLE IF NOT EXISTS iroas_estimates (
  estimate_id        VARCHAR PRIMARY KEY,
  campaign_id        VARCHAR NOT NULL,
  period_start       TIMESTAMPTZ NOT NULL,
  period_end         TIMESTAMPTZ NOT NULL,
  model_id           VARCHAR NOT NULL,          -- "adstock_grid@1.0.0"
  delta              DOUBLE NOT NULL,
  kappa_mean         DOUBLE NOT NULL,
  a_mean             DOUBLE NOT NULL,
  posterior          JSON NOT NULL,             -- grid cells with weight > 1e-4: [{kappa,a,w}]
  spend_total        BIGINT NOT NULL,
  inc_revenue_mean   BIGINT NOT NULL,
  iroas_mean         DOUBLE NOT NULL,
  iroas_p10          DOUBLE NOT NULL,
  iroas_p90          DOUBLE NOT NULL,
  marginal_iroas     DOUBLE,
  platform_roas      DOUBLE,
  n_hours_used       INTEGER NOT NULL,
  n_clean_off_hours  INTEGER NOT NULL,
  excluded_hours     JSON NOT NULL,             -- {"unknown":24,"low_stock":130,"stock_pause":48,"launch":0}
  check_ratio        DOUBLE,
  confidence         VARCHAR NOT NULL,
  panel_version      VARCHAR NOT NULL,
  created_at         TIMESTAMPTZ NOT NULL
);

-- ============================================================ 05 · economics & simulation
CREATE TABLE IF NOT EXISTS parameters (
  key          VARCHAR NOT NULL,
  scope        VARCHAR NOT NULL,          -- shop|sku|campaign|session
  scope_id     VARCHAR NOT NULL DEFAULT '*',
  value        DOUBLE,
  value_json   JSON,                      -- list/table-valued parameters
  version      INTEGER NOT NULL,
  set_by       VARCHAR NOT NULL,          -- system|operator|lead|ai_suggested|ai_suggested_confirmed
  set_at       TIMESTAMPTZ NOT NULL,
  reason       VARCHAR,
  PRIMARY KEY (key, scope, scope_id, version)
);

CREATE TABLE IF NOT EXISTS goals (
  goal_id      VARCHAR PRIMARY KEY,
  scope        VARCHAR NOT NULL,           -- campaign|sku
  scope_id     VARCHAR NOT NULL,
  kind         VARCHAR NOT NULL,           -- profit|sell_target|clear_stock|efficiency
  deadline     TIMESTAMPTZ NOT NULL,
  target_units INTEGER,                    -- sell_target
  params       JSON,                       -- goal-specific overrides (V, max_loss, ...)
  confirmed_by VARCHAR NOT NULL,           -- a real user; AI never creates goals
  created_at   TIMESTAMPTZ NOT NULL,
  active       BOOLEAN NOT NULL DEFAULT TRUE,
  version      INTEGER NOT NULL DEFAULT 1, -- [DEV] 10 §4: goals.version, keep old rows
  valid_from   TIMESTAMPTZ,                -- [DEV] 10 §4
  valid_to     TIMESTAMPTZ                 -- [DEV] 10 §4, NULL = current
);

CREATE TABLE IF NOT EXISTS scenarios (
  scenario_id   VARCHAR PRIMARY KEY,
  campaign_id   VARCHAR NOT NULL,
  goal_id       VARCHAR NOT NULL,
  start_at      TIMESTAMPTZ NOT NULL,
  horizon_hours INTEGER NOT NULL,
  param_version JSON NOT NULL,             -- {key: version}
  overrides     JSON,                      -- what-if from user/AI
  inputs_ref    JSON NOT NULL,             -- forecast_id[], estimate_id, data_as_of, panel_version
  samples       INTEGER NOT NULL,
  seed          BIGINT NOT NULL,
  requested_by  VARCHAR NOT NULL,          -- orchestrator|user|ai
  created_at    TIMESTAMPTZ NOT NULL,
  input_hash    VARCHAR,                   -- [DEV] 06 §3 cache key hash, compared by decisions (10 §2)
  request       JSON                       -- [DEV] SimRequest as run (start/horizon resolved), echoed by GET /scenarios/{id}
);

CREATE TABLE IF NOT EXISTS scenario_results (
  scenario_id   VARCHAR NOT NULL REFERENCES scenarios(scenario_id),
  action        JSON NOT NULL,             -- ActionSpec
  feasible      BOOLEAN NOT NULL,
  violations    VARCHAR[],
  objective     JSON NOT NULL,             -- Dist {mean,p10,p50,p90}
  contribution  JSON NOT NULL,
  spend         JSON NOT NULL,
  units_sold    JSON NOT NULL,
  leftover_units JSON NOT NULL,
  leftover_value JSON NOT NULL,
  stockout_hour_p50 TIMESTAMPTZ,
  per_sku       JSON NOT NULL,
  PRIMARY KEY (scenario_id, action)
);

CREATE TABLE IF NOT EXISTS scenario_calculation_traces (
  scenario_id VARCHAR PRIMARY KEY,
  trace       JSON NOT NULL,
  results     JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS sale_plan_previews (
  preview_id VARCHAR PRIMARY KEY,
  campaign_id VARCHAR NOT NULL,
  goal_id VARCHAR NOT NULL,
  scenario_id VARCHAR NOT NULL,
  snapshot JSON NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  applied_goal_id VARCHAR,
  applied_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS recommendations (
  recommendation_id VARCHAR PRIMARY KEY,
  scenario_id   VARCHAR NOT NULL REFERENCES scenarios(scenario_id),
  campaign_id   VARCHAR NOT NULL,
  status        VARCHAR NOT NULL,          -- recommend|undetermined|none_meets_goal
  best_action   JSON,
  runner_up     JSON,
  p_best        DOUBLE,
  expected_gain_vnd BIGINT,
  zone_bar      JSON,
  flip_conditions JSON,
  confidence    VARCHAR NOT NULL,
  reasons       VARCHAR[],
  superseded_by VARCHAR,                   -- newer recommendation (old rows are never deleted)
  created_at    TIMESTAMPTZ NOT NULL,
  input_hash    VARCHAR,                   -- [DEV] 06 §3
  what_if       BOOLEAN DEFAULT FALSE      -- [DEV] run with param_overrides / session_id / own actions: never active, never supersedes
);

-- ============================================================ 06 · orchestration
CREATE TABLE IF NOT EXISTS event_log (
  id VARCHAR PRIMARY KEY, type VARCHAR, payload JSON, "at" TIMESTAMPTZ  -- [DEV] quoted: AT is reserved in DuckDB 2.x, always write "at"
);

CREATE TABLE IF NOT EXISTS sku_compute_state (
  sku_id VARCHAR PRIMARY KEY,
  state VARCHAR,                           -- fresh|queued|computing|failed (stale is derived)
  last_input_hash VARCHAR,
  computed_at TIMESTAMPTZ,
  data_as_of TIMESTAMPTZ,
  error VARCHAR,
  retries INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS live_cursor (
  file VARCHAR PRIMARY KEY, last_row_count BIGINT, last_tick VARCHAR, updated_at TIMESTAMPTZ
);

-- ============================================================ 07 · AI
CREATE TABLE IF NOT EXISTS ai_sessions (
  id VARCHAR PRIMARY KEY, user_id VARCHAR, role VARCHAR, flow VARCHAR,
  started_at TIMESTAMPTZ, model VARCHAR,
  input_tokens BIGINT, output_tokens BIGINT, cost_estimate_usd DOUBLE,
  grounding_ok BOOLEAN, rewrite_count INTEGER,
  outcome VARCHAR                          -- answered|asked_back|refused|gave_up|offline
);

CREATE TABLE IF NOT EXISTS ai_tool_calls (
  id VARCHAR PRIMARY KEY, session_id VARCHAR, turn INTEGER, tool VARCHAR,
  args JSON, result_summary JSON, result_source_id VARCHAR,
  duration_ms INTEGER, ok BOOLEAN, error VARCHAR, "at" TIMESTAMPTZ  -- quoted, see event_log
);

-- ============================================================ 10 · governance
CREATE TABLE IF NOT EXISTS decisions (
  id VARCHAR PRIMARY KEY,                  -- dec-<ulid>
  kind VARCHAR,                            -- ad_action | goal_change | handoff_note
  target_kind VARCHAR, target_id VARCHAR,  -- campaign / sku
  action JSON,                             -- ActionSpec
  state VARCHAR,                           -- draft|pending_approval|approved|rejected|cancelled|expired|sent|verified|failed
  rationale VARCHAR,
  override_of_recommendation BOOLEAN,
  recommendation_id VARCHAR, recommended_action JSON,
  scenario_id VARCHAR,
  goal_snapshot JSON,
  parameters_snapshot JSON,
  model_versions JSON,
  data_as_of JSON,                         -- {"orders":"...","ads":"...","inventory":"..."}
  input_hash VARCHAR,
  expected_outcome JSON,
  open_issues JSON,
  created_by VARCHAR, created_via VARCHAR, -- ui | ai
  created_at TIMESTAMPTZ, submitted_at TIMESTAMPTZ,
  approved_by VARCHAR, approved_at TIMESTAMPTZ, approval_note VARCHAR,
  reject_reason VARCHAR, valid_until TIMESTAMPTZ,
  observed_outcome JSON,
  evaluation JSON
);

CREATE TABLE IF NOT EXISTS actions (
  id VARCHAR PRIMARY KEY, decision_id VARCHAR,
  mode VARCHAR,                            -- simulated (hackathon) | live (later)
  payload JSON, sent_by VARCHAR, sent_at TIMESTAMPTZ,
  state VARCHAR,                           -- sent | verified | failed
  verified_at TIMESTAMPTZ, verified_by_human BOOLEAN, observed JSON, failure_reason VARCHAR
);

CREATE TABLE IF NOT EXISTS alerts (
  id VARCHAR PRIMARY KEY,
  shop_id VARCHAR, kind VARCHAR,           -- stockout_risk|sales_below_forecast|sales_above_forecast|drift|budget_exhausted|data_stale|data_blocking|recommendation_changed|compute_failed|ai_policy
  severity VARCHAR,                        -- high | medium | low
  target_kind VARCHAR, target_id VARCHAR, group_key VARCHAR,
  title VARCHAR, detail JSON, impact_vnd BIGINT,
  status VARCHAR,                          -- open|acknowledged|snoozed|resolved|hidden_by_budget
  raised_at TIMESTAMPTZ, resolved_at TIMESTAMPTZ, snoozed_until TIMESTAMPTZ,
  acknowledged_by VARCHAR, source_event_id VARCHAR,
  investigation JSON
);

CREATE TABLE IF NOT EXISTS audit_log (
  id VARCHAR PRIMARY KEY, "at" TIMESTAMPTZ, shop_id VARCHAR,  -- "at" quoted, see event_log
  actor VARCHAR, actor_role VARCHAR, via VARCHAR,   -- ui | ai | system
  action VARCHAR,          -- decision.submit, decision.approve, goal.update, parameter.update, ...
  target_kind VARCHAR, target_id VARCHAR,
  before JSON, after JSON, request_id VARCHAR
);
