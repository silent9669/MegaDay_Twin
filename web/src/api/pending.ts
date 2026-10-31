// Hand-written types for endpoints the backend does not serve yet (M4: decisions,
// alerts, live), following docs/dev-plan/08-api.md §3.10, §3.11, §3.13. Replace
// with aliases into schema.d.ts once `npm run gen:api` picks them up.
import type { components } from './schema';

type ActionSpec = components['schemas']['ActionSpec'];

export type DecisionCreate = {
  target: { campaign_id?: string; sku?: string };
  action: ActionSpec;
  scenario_id: string;
  recommendation_id: string;
  rationale: string;
  override_of_recommendation: boolean;
};

export type DecisionCreated = { id: string; state: string; created_by: string; input_hash: string };

export type Alert = {
  id: string;
  kind: string;
  severity: 'high' | 'medium' | 'low';
  target: { sku?: string; product?: string; campaign_id?: string };
  title: string;
  impact_vnd: number;
  raised_at: string;
  status: 'open' | 'acked' | 'snoozed' | 'resolved';
  group_key: string;
  investigation: unknown | null;
};

export type LiveStatus = {
  enabled: boolean;
  day: string;
  orders_until: string;
  ads_until: string;
  last_tick_at: string;
  replay_running: boolean;
};
