import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '../lib/queryKeys';
import { api } from './client';
import type {
  Alert,
  CampaignDetail,
  DecisionCreate,
  DecisionCreated,
  Envelope,
  Freshness,
  GoalIn,
  GoalOut,
  HealthStatus,
  Me,
  Page,
  Recommendation,
  ScenarioRun,
  SimRequest,
  SkuDetail,
  SkuListItem,
} from './types';

export function qs(params: Record<string, string | undefined>): string {
  const clean: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') clean[k] = v;
  const s = new URLSearchParams(clean).toString();
  return s ? `?${s}` : '';
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => api.get<Envelope<HealthStatus>>('/health', signal),
  });
}

/** GET /me: role and what the caller may do (M3-api §2). */
export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => api.get<Envelope<Me>>('/me', signal),
    staleTime: 5 * 60_000,
  });
}

export function useFreshness() {
  return useQuery({
    queryKey: queryKeys.freshness,
    queryFn: ({ signal }) => api.get<Envelope<Freshness>>('/freshness', signal),
    refetchInterval: 60_000,
  });
}

/** One page of GET /skus (used for the "Cần xem ngay" block). */
export function useSkus(filters: Record<string, string> = { sort: 'priority' }) {
  return useQuery({
    queryKey: queryKeys.skus(filters),
    queryFn: ({ signal }) => api.get<Page<SkuListItem>>(`/skus${qs(filters)}`, signal),
  });
}

/** GET /skus with cursor pagination ("Tải thêm" in the full table). */
export function useSkuPages(filters: Record<string, string>) {
  return useInfiniteQuery({
    queryKey: [...queryKeys.skus(filters), 'pages'] as const,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ signal, pageParam }) => api.get<Page<SkuListItem>>(`/skus${qs({ ...filters, cursor: pageParam })}`, signal),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

export function useSku(sku: string | undefined) {
  return useQuery({
    queryKey: queryKeys.sku(sku ?? ''),
    queryFn: ({ signal }) => api.get<Envelope<SkuDetail>>(`/skus/${encodeURIComponent(sku ?? '')}`, signal),
    enabled: Boolean(sku),
  });
}

export function useCampaign(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.campaign(id ?? ''),
    queryFn: ({ signal }) => api.get<Envelope<CampaignDetail>>(`/campaigns/${encodeURIComponent(id ?? '')}`, signal),
    enabled: Boolean(id),
    retry: false,
  });
}

export function useRecommendations(filters: Record<string, string> = { active: 'true' }, enabled = true) {
  return useQuery({
    queryKey: queryKeys.recommendations(filters),
    queryFn: ({ signal }) => api.get<Page<Recommendation>>(`/recommendations${qs(filters)}`, signal),
    enabled,
  });
}

export function useRecommendation(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.recommendation(id ?? ''),
    queryFn: ({ signal }) => api.get<Envelope<Recommendation>>(`/recommendations/${encodeURIComponent(id ?? '')}`, signal),
    enabled: Boolean(id),
  });
}

export function useScenario(id: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.scenario(id ?? ''),
    queryFn: ({ signal }) => api.get<Envelope<ScenarioRun>>(`/scenarios/${encodeURIComponent(id ?? '')}`, signal),
    enabled: Boolean(id),
  });
}

export function useAlerts(status = 'open') {
  return useQuery({
    queryKey: queryKeys.alerts(status),
    queryFn: ({ signal }) => api.get<Page<Alert>>(`/alerts${qs({ status })}`, signal),
    retry: false,
  });
}

/** POST /scenarios/run (08 §3.8). Sliders send samples=300 while dragging, 1000 on release. */
export function useRunScenario() {
  return useMutation({
    mutationFn: ({ body, signal }: { body: SimRequest; signal?: AbortSignal }) =>
      api.post<Envelope<ScenarioRun>>('/scenarios/run', body, { signal }),
  });
}

/** POST /goals (lead only). Refreshes the campaign so the new goal shows. */
export function useCreateGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GoalIn) => api.post<Envelope<GoalOut>>('/goals', body),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: queryKeys.campaign(res.data.scope_id) });
    },
  });
}

/** POST /decisions (08 §3.10): creates a draft. */
export function useCreateDecision() {
  return useMutation({
    mutationFn: ({ body, idempotencyKey }: { body: DecisionCreate; idempotencyKey: string }) =>
      api.post<Envelope<DecisionCreated>>('/decisions', body, { idempotencyKey }),
  });
}

/** POST /decisions/{id}/submit: draft → pending_approval. */
export function useSubmitDecision() {
  return useMutation({
    mutationFn: (id: string) => api.post<Envelope<DecisionCreated>>(`/decisions/${encodeURIComponent(id)}/submit`),
  });
}
