// TanStack Query keys (09-giao-dien §3). SSE events invalidate by these prefixes.
export const queryKeys = {
  health: ['health'] as const,
  me: ['me'] as const,
  freshness: ['freshness'] as const,
  liveStatus: ['live-status'] as const,
  skus: (filters: Record<string, string> = {}) => ['skus', filters] as const,
  sku: (sku: string) => ['sku', sku] as const,
  campaign: (id: string) => ['campaign', id] as const,
  recommendations: (filters: Record<string, string> = {}) => ['recommendations', filters] as const,
  recommendation: (id: string) => ['recommendation', id] as const,
  scenario: (id: string) => ['scenario', id] as const,
  alerts: (status: string) => ['alerts', status] as const,
  decisions: (filter: string) => ['decisions', filter] as const,
};
