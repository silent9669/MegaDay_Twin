// Resolves which recommendation the Compare screen shows, from either route
// (09 §1): /sku/:sku/compare or /campaign/:id/compare, with an optional ?rec= override.
// Real API: GET /skus/{sku} → campaign, GET /campaigns/{id} (name, SKUs, price_max_vnd,
// goal), GET /recommendations?campaign_id= → the active one, then /recommendations/{id}
// and /scenarios/{scenario_id}.
import { useParams, useSearchParams } from 'react-router';
import { useCampaign, useRecommendation, useRecommendations, useScenario, useSku } from '../../api/queries';
import type { CampaignDetail, Recommendation, SkuDetail } from '../../api/types';

export type CompareTarget = {
  sku?: string;
  campaignId?: string;
  skuItem?: SkuDetail;
  campaign?: CampaignDetail;
  recommendationId?: string;
  /** Lookups still loading. */
  resolving: boolean;
  /** GET /campaigns/{id} still loading (needed before showing the setup panel). */
  campaignPending: boolean;
  resolveError: unknown;
  retryResolve: () => void;
  /** "Nồi chiên không dầu mini 2L, Giấy lót…" from the campaign's SKU list. */
  campaignSkuNames: string[];
};

/** Active recommendation of a campaign from a /recommendations page (fixtures ignore the filter). */
export function pickActive(list: readonly Recommendation[] | undefined, campaignId: string | undefined): Recommendation | undefined {
  if (!list || !campaignId) return undefined;
  const mine = list.filter((r) => r.campaign_id === campaignId && !r.superseded_by && !r.what_if);
  return mine.find((r) => r.active !== false) ?? mine[0];
}

export function useCompareTarget(selectedSku?: string): CompareTarget {
  const { sku: routeSku, id } = useParams();
  const sku = selectedSku ?? routeSku;
  const [search] = useSearchParams();
  const recParam = search.get('rec') ?? undefined;

  const skuQ = useSku(sku);
  const skuItem = skuQ.data?.data;
  const campaignId = id ?? (selectedSku ? undefined : search.get('campaign') ?? undefined) ?? skuItem?.campaign?.id;
  const campaignQ = useCampaign(campaignId);
  const needsList = Boolean(campaignId) && !recParam;
  const recsQ = useRecommendations({ campaign_id: campaignId ?? '', active: 'true' }, needsList);

  let recommendationId = recParam;
  if (!recommendationId) recommendationId = pickActive(recsQ.data?.data, campaignId)?.recommendation_id;
  if (!recommendationId && sku && skuItem?.recommendation) recommendationId = skuItem.recommendation.recommendation_id;

  const resolving = (Boolean(sku) && skuQ.isPending) || (needsList && recsQ.isPending);
  const resolveError = (sku && skuQ.error) || (!recommendationId && campaignQ.error) || (needsList && recsQ.error) || null;
  const campaign = campaignQ.data?.data;
  const campaignSkuNames = (campaign?.sku_detail ?? []).map((s) => (s.variant ? `${s.name} · ${s.variant}` : s.name));

  return {
    sku,
    campaignId,
    skuItem,
    campaign,
    recommendationId,
    resolving,
    campaignPending: Boolean(campaignId) && campaignQ.isPending,
    resolveError,
    retryResolve: () => {
      void skuQ.refetch();
      void recsQ.refetch();
      void campaignQ.refetch();
    },
    campaignSkuNames,
  };
}

export function useCompareData(selectedSku?: string) {
  const target = useCompareTarget(selectedSku);
  const rec = useRecommendation(target.recommendationId);
  const scenarioId = rec.data?.data.scenario_id;
  const scenario = useScenario(scenarioId);
  return { target, rec, scenario };
}
