import type { Source } from "./sources";
export type ImportTable = {
  file: string;
  source: Source;
  rowCount: number;
  review?: string[];
  repairs?: string[];
  remainingIssues?: string[];
  method: "recognized" | "model" | "general";
  columns: { source: string; target: string }[];
};
export type ImportAnalysis = {
  tables: ImportTable[];
  skipped: { file: string; reason: string }[];
  model: string | null;
};
