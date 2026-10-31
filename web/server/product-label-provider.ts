import type { LabelInput } from "../src/studio/product-labels";

/** Server-only integration seam. The adapter must respect signal, keep credentials
 * server-side, and return the shared { labels: ProductLabel[] } contract. Product names are
 * untrusted data; do not turn them into provider instructions.
 */
export interface ProductLabelProvider {
  id: string;
  classify(input: LabelInput, signal: AbortSignal): Promise<unknown>;
}

/** Deliberately disabled until a real provider and its API contract are supplied.
 * Implement provider selection here; do not install a fake heuristic fallback.
 */
export function getProductLabelProvider(env: unknown): ProductLabelProvider | null {
  void env;
  return null;
}

export const LABEL_TIMEOUT_MS = 30_000;
export const LABEL_LEASE_MS = LABEL_TIMEOUT_MS + 5_000;
