import { z } from "zod";

export const demandLabelSchema = z.enum(["Trendy", "Seasonal", "Normal"]);
export const demandEvidenceSchema = z.object({
  label: demandLabelSchema, reason: z.string().max(1000), provider: z.string().max(150),
  assessed_on: z.string().max(30), run_id: z.string().max(150),
  source_revision: z.number().int(), source_fingerprint: z.string().max(64),
  raw_code: z.string().max(300), sku_id: z.string().max(300),
  confidence: z.number().min(0).max(1).nullable().optional(),
  manual: z.boolean().optional(), evidence_through: z.string().max(60).nullable().optional(),
});
export const modelSelectionSchema = z.object({
  policy_version: z.string().max(100).optional(), primary_label: demandLabelSchema.nullable().optional(),
  forecast_origin: z.string().max(60).optional(),
  model_id: z.string().max(150).optional(), history_profile: z.string().max(100).nullable().optional(),
  label_source: z.string().max(100).nullable().optional(), label_evidence: demandEvidenceSchema.nullable().optional(),
  from_backtest: z.boolean().optional(), reasons: z.array(z.string().max(1000)).max(30).optional(),
  data_status: z.object({ new: z.boolean(), sparse: z.boolean(), insufficient: z.boolean(), history_days: z.number() }).optional(),
  thresholds: z.object({ min_history_days: z.number(), sparse_adi: z.number() }).optional(),
});
export const demandSummarySchema = z.object({
  primary_label: demandLabelSchema.nullable(), source: z.string().max(100).nullable(),
  label_evidence: demandEvidenceSchema.nullable(), history_profile: z.string().nullable(),
  model_selection: modelSelectionSchema.nullable(),
});
export type ModelSelection = z.infer<typeof modelSelectionSchema>;
export type DemandSummary = z.infer<typeof demandSummarySchema>;
