import type { CampaignDetail, ScenarioRun } from "../api/types";
export type CampaignContext = {
  campaign: CampaignDetail;
  goal: { goal_id: string; version: number; kind: string; deadline: string; target_units: number | null;
    params: Record<string, unknown>; confirmed_by: string } | null;
  revision: string; dataAsOf: string | null; sources: Record<string, string | null>; canRun: boolean;
  history: { scenario_id: string; created_at: string }[];
};
export type CampaignRun = { revision: string; scenario: ScenarioRun; end: string;
  goal: NonNullable<CampaignContext["goal"]>; progress: { units: number; spend: number; ads_complete: boolean } | null };
export type Explanation = { scenarioId: string; method: "ai" | "rules"; reasons: string[]; cautions: string[]; notice?: string };
