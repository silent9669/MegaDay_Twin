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
