import { z } from "zod";
import type { GoalInterpretResponse } from "./goal-contract";

// Canonical backend identifiers may contain Vietnamese letters and spaces, but no URL separators.
export const assistantSkuSchema = z.string().trim().min(1).max(150).refine((value) => !/[/\\?#]/.test(value) && [...value].every((char) => char.charCodeAt(0) >= 32), "Mã SKU không hợp lệ.");
export const assistantTurnSchema = z.strictObject({
  sku: assistantSkuSchema,
  messages: z.array(z.strictObject({ role: z.enum(["user", "assistant"]), content: z.string().trim().min(1).max(4000) })).min(1).max(16),
});
export type AssistantMessage = z.infer<typeof assistantTurnSchema>["messages"][number];
export type AssistantSkuList = {
  items: { sku: string; name: string; variant: string; stockAvailable: number | null }[];
  nextCursor: string | null;
  dataAsOf: string | null;
};
export type AssistantContext = {
  sku: {
    sku: string; name: string; variant: string; stockAvailable: number | null;
    soldTodayUnits: number | null; priceEffectiveVnd: number | null;
    contributionPerUnitVnd: number | null; demandGroup: string | null;
  };
  campaigns: { id: string; name: string; skus: string[] }[];
  dataAsOf: string | null;
  source: { id: string; kind: string; label: string; data_as_of?: string | null; tool?: string | null; href?: string | null } | null;
  canPlan: boolean;
  notices: string[];
  opening: string;
};
export type AssistantTurnResponse = {
  reply: string;
  interpretation: GoalInterpretResponse;
  context: AssistantContext;
};
