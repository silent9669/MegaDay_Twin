import { z } from "zod";
import { assistantSkuSchema, type AssistantContext } from "../planner/assistant-contract";
import type { GoalInterpretResponse } from "../planner/goal-contract";

// Only identifiers cross the client/server boundary. Facts are resolved by the server.
export const miloContextSchema = z.strictObject({
  screen: z.string().max(100).optional(),
  sku: assistantSkuSchema.optional(),
  campaignId: z.string().regex(/^[\w.-]{1,150}$/).optional(),
  scenarioId: z.string().regex(/^[\w.-]{1,150}$/).optional(),
});
export type MiloContext = z.infer<typeof miloContextSchema>;
export type MiloReplyPlan = {
  interpretation: GoalInterpretResponse;
  context: AssistantContext;
};
export type MiloCampaignPlan = { campaignId: string; name: string; interpretation: GoalInterpretResponse };
export type MiloReply = { reply: string; model: string; plan?: MiloReplyPlan | null; campaignPlan?: MiloCampaignPlan | null };
export function openMilo(context: MiloContext) {
  window.dispatchEvent(new CustomEvent("workspace:milo-sku", { detail: context }));
}
export function openMiloForSku(sku: string) {
  openMilo({ sku, screen: "hq" });
}
