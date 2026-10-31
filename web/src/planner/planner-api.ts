import type { Envelope, ScenarioRun } from "../api/types";
import type { GoalDraft } from "./goal-contract";

export type PlannerContext = {
  backendConfigured: boolean;
  canCreateGoals: boolean;
  referenceTime: string;
  dataAsOf: string | null;
  campaigns: { id: string; name: string }[];
  notice: string | null;
};
export type PlannerRunRequest = {
  campaignId: string;
  draft: GoalDraft;
  confirmed: true;
  acknowledgedUnresolved: boolean;
  goalId?: string;
};
export type PlannerRunResponse = { goalId: string; scenario: Envelope<ScenarioRun> };
export type PlannerRunError = {
  error: { message: string; code: string; details?: Record<string, unknown> };
  goalId: string | null;
  stage: "goal" | "simulation" | "validation";
};
