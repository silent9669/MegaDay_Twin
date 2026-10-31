import { z } from "zod";
import { validateLabelResponse, type LabelInput, type ProductLabel } from "../src/studio/product-labels";
import { modelJSON, type StudioModelEnv } from "./model-json";
import { DEMAND_PROMPT, DEMAND_PROMPT_VERSION } from "./demand-prompt";

export interface ProductLabelProvider {
  id: string;
  classify(input: LabelInput, signal: AbortSignal): Promise<unknown>;
}
const outputSchema = z.toJSONSchema(z.object({ labels: z.array(z.object({
  productId: z.string(), group: z.enum(["Trendy", "Seasonal", "Normal"]).nullable(),
  reason: z.string(), confidence: z.number(),
}).strict()) }).strict());

export function getProductLabelProvider(env: StudioModelEnv): ProductLabelProvider | null {
  const model = env.OPENAI_MODEL_DECISION;
  if (!env.OPENAI_API_KEY || !model) return null;
  return {
    id: `openai:${model}:${DEMAND_PROMPT_VERSION}`,
    async classify(input, signal) {
      if (!input.context) throw Error("Assessment context is required.");
      const context = input.context;
      const labels: ProductLabel[] = [];
      let next = 0;
      // Small batches bound structured output; three requests at a time bound provider load.
      await Promise.all(Array.from({ length: Math.min(3, Math.ceil(input.products.length / 25)) }, async () => {
        while (next < input.products.length) {
          signal.throwIfAborted();
          const products = input.products.slice(next, next += 25);
          const codes = new Set(products.map(p => p.reference));
          const batch = { ...input, products, context: { ...context, evidence: context.evidence.filter(e => codes.has(e.productCode)) } };
          const raw = await modelJSON(env, model, DEMAND_PROMPT, batch, outputSchema, signal);
          const result = validateLabelResponse(raw, batch);
          if (result.length !== products.length) throw Error("Incomplete product labels.");
          labels.push(...result);
        }
      }));
      return { labels };
    },
  };
}
export const LABEL_TIMEOUT_MS = 120_000;
export const LABEL_LEASE_MS = LABEL_TIMEOUT_MS + 5_000;
