import type { Envelope, ScenarioRun } from "../api/types";
import type { GoalDraft } from "./goal-contract";

export type PlannerContext = {
  backendConfigured: boolean;
  canCreateGoals: boolean;
  canPreview?: boolean;
  canApply?: boolean;
  referenceTime: string;
  dataAsOf: string | null;
  campaigns: { id: string; name: string }[];
  notice: string | null;
};
export type GoalRevision = { goal_id: string; version: number };
export type PlannerGoal = {
  goal_id: string; scope: string; scope_id: string; kind: string; label?: string;
  deadline: string; target_units: number | null; params: Record<string, unknown>;
  active: boolean; version: number;
};
export type PlannerActiveContext = {
  campaignId: string; activeGoal: PlannerGoal | null; baseGoal: GoalRevision | null;
};
export type PlannerRunRequest = {
  campaignId: string;
  draft: GoalDraft;
  confirmed: true;
  acknowledgedUnresolved: boolean;
  expectedGoal: GoalRevision | null;
  focusSku?: string;
};
export type PlannerRunResponse = {
  previewId: string; goalId: string; effectiveGoal: PlannerGoal; baseGoal: PlannerGoal | null;
  changes: { field: string; before: unknown; after: unknown }[];
  notices: string[]; canApply: boolean; appliedGoalId: string | null;
  scenario: Envelope<ScenarioRun>;
};
export type PlannerApplyResponse = { previewId: string; goal: PlannerGoal; alreadyApplied: boolean };
export type PlannerRunError = {
  error: { message: string; code: string; details?: Record<string, unknown> };
  goalId: string | null;
  previewId?: string | null;
  stage: "preview" | "validation" | "apply";
};
