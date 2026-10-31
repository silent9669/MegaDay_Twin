import { z } from "zod";

export const goalKindSchema = z.enum([
  "profit", "sell_target", "clear_stock", "efficiency",
]);
const instant = z.iso.datetime({ offset: true });
const amount = z.number().min(0);

/** An interpretation is an editable proposal, never permission to run a sale. */
export const goalDraftSchema = z.strictObject({
  text: z.string().min(1).max(4000),
  kind: goalKindSchema.nullable(),
  targetUnits: z.number().int().positive().max(Number.MAX_SAFE_INTEGER).nullable(),
  start: instant.nullable(),
  deadline: instant.nullable(),
  maxSpendVnd: amount.nullable(),
  maxLossVnd: amount.nullable(),
  sellTargetProbability: z.number().gt(0).max(1).nullable(),
  minMarginalReturn: amount.nullable(),
  unresolved: z.array(z.string().min(1).max(4000)).max(30),
});

export const goalInterpretRequestSchema = z.strictObject({
  text: z.string().trim().min(1).max(4000),
  referenceTime: instant.optional(),
});
export const goalInterpretResponseSchema = z.strictObject({
  draft: goalDraftSchema,
  method: z.enum(["ai", "rules"]),
  notices: z.array(z.string()),
});
export type GoalKind = z.infer<typeof goalKindSchema>;
export type GoalDraft = z.infer<typeof goalDraftSchema>;
export type GoalInterpretResponse = z.infer<typeof goalInterpretResponseSchema>;

export function draftIssues(draft: GoalDraft): string[] {
  const issues: string[] = [];
  if (!draft.text.trim()) issues.push("Describe your goal before confirming.");
  if (!draft.kind) issues.push("Choose the campaign's primary goal.");
  if (draft.kind === "sell_target" && (!draft.targetUnits || !Number.isSafeInteger(draft.targetUnits) || draft.targetUnits < 1))
    issues.push("Target units must be a positive integer.");
  if (draft.kind !== "sell_target" && (draft.targetUnits !== null || draft.sellTargetProbability !== null))
    issues.push("Units and target probability only apply to Reach sales target. Change the goal or clear these fields.");
  if (draft.kind !== "efficiency" && draft.minMarginalReturn !== null)
    issues.push("Marginal return only applies to Improve ad efficiency. Change the goal or clear this field.");
  const start = Date.parse(draft.start ?? ""), end = Date.parse(draft.deadline ?? "");
  if (!Number.isFinite(start) || !Number.isFinite(end)) issues.push("Choose a start and end time.");
  else if (start % 3_600_000 || end % 3_600_000 || end <= start || end - start > 72 * 3_600_000)
    issues.push("Use whole hours and a duration of 1 to 72 hours.");
  if ([draft.maxSpendVnd, draft.maxLossVnd, draft.minMarginalReturn].some((v) => v !== null && (!Number.isFinite(v) || v < 0)))
    issues.push("Budget, loss limit and marginal return must be zero or greater.");
  if (draft.sellTargetProbability !== null && (!Number.isFinite(draft.sellTargetProbability) || draft.sellTargetProbability <= 0 || draft.sellTargetProbability > 1))
    issues.push("Target probability must be above 0% and at most 100%.");
  return issues;
}
