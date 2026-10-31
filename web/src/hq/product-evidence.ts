import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useSku } from "../api/queries";
import { config } from "../lib/config";
import type { StockRow } from "../studio/source-view";
import type { SourcePack } from "../studio/sources";
import type { TrackedProduct } from "./types";
import { useProductIdentity } from "./useProductIdentity";
import { modelSuggestion, workspaceSuggestion } from "./worksheet-suggestions";

export function useProductEvidence(product: TrackedProduct, pack: SourcePack | null, stock?: StockRow, revision = 0) {
  const identity = useProductIdentity(product);
  const canonicalSku = identity.sku;
  const model = useSku(canonicalSku);
  const campaignId = model.data?.data.campaign?.id;
  const fee = useQuery({
    queryKey: ["hq-platform-fee", canonicalSku, campaignId, revision],
    enabled: !!canonicalSku && !!model.data && !config.useFixtures,
    retry: false,
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({ sku: canonicalSku!, key: "platform_fee_rate" });
      if (campaignId) params.set("campaign_id", campaignId);
      return api.get<{ data: { key: string; value: unknown; scope: string; version: number; is_default: boolean }[] }>(`/parameters?${params}`, signal);
    },
  });
  const evidence = useMemo(() => {
    const item = model.data?.data;
    const backend = modelSuggestion(item?.sku === canonicalSku ? item : undefined, config.useFixtures);
    const rate = fee.data?.data.find(row => row.key === "platform_fee_rate");
    if (typeof rate?.value === "number" && Number.isFinite(rate.value) && rate.value >= 0 && rate.value <= 1)
      backend.feePercent = { value: rate.value * 100, source: `Decision backend · platform_fee_rate · ${rate.scope} · version ${rate.version}${rate.is_default ? " · model default" : ""}` };
    if (product.mode === "model") return backend;
    // Workspace stock follows its visible replay hour; never replace it with the backend clock.
    delete backend.stock;
    return { ...backend, ...workspaceSuggestion(pack, product.sku, stock) };
  }, [product, pack, stock, model.data, canonicalSku, fee.data]);
  return { evidence, canonicalSku, campaignId, loading: identity.loading || model.isFetching || fee.isFetching,
    error: !!identity.error || (!!canonicalSku && model.isError) || fee.isError,
    retry: () => { identity.retry(); if (canonicalSku) { void model.refetch(); void fee.refetch(); } } };
}
