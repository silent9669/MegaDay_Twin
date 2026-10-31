import { z } from 'zod';
export const analysisContextSchema=z.object({
  title:z.string().min(1).max(150),
  source:z.enum(['workspace','model-snapshot','model-api','user-assumptions']),
  sku:z.string().max(150).optional(),
  revision:z.number().int().nonnegative().optional(),
  data:z.string().max(24000),
});
export type AnalysisContext=z.infer<typeof analysisContextSchema>;
export function analyzeWithAI(context:AnalysisContext,prompt:string) {
  window.dispatchEvent(new CustomEvent('workspace:analyze',{detail:{context,prompt}}));
}
