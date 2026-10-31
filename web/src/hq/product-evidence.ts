import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useSku } from "../api/queries";
import { config } from "../lib/config";
import type { StockRow } from "../studio/source-view";
import type { SourcePack } from "../studio/sources";
import type { TrackedProduct } from "./types";
import { modelSuggestion, workspaceSuggestion } from "./worksheet-suggestions";

type InventoryAlias = { raw_code: string; alias: string; status: string; selected: string | null };
type AliasPage = { data: { items: InventoryAlias[] }; next_cursor?: string | null };
export function linkedSku(aliases: InventoryAlias[], sku: string, name: string) {
  const matches = aliases.filter(row => row.raw_code === sku && row.alias === name);
  if (!matches.length || matches.some(row => !row.selected || !["auto_accepted", "confirmed"].includes(row.status))) return undefined;
  const codes = new Set(matches.map(row => row.selected!));
  return codes.size === 1 ? [...codes][0] : undefined;
}
export function useInventorySku(sku: string, stock: StockRow | undefined, revision: number, enabled: boolean) {
  return useQuery({
    queryKey: ["hq-inventory-links", revision],
    enabled: enabled && !!stock && !config.useFixtures,
    staleTime: 60_000,
    retry: false,
    queryFn: async ({ signal }) => {
      const rows: InventoryAlias[] = [];
      let cursor: string | null | undefined;
      do {
        const params = new URLSearchParams({ status: "all", source_type: "inventory", limit: "500" });
        if (cursor) params.set("cursor", cursor);
        const page = await api.get<AliasPage>(`/ingest/sku-queue?${params}`, signal);
        rows.push(...page.data.items);
        cursor = page.next_cursor;
      } while (cursor);
      return rows;
    },
    select: rows => linkedSku(rows, sku, stock?.name ?? ""),
  });
}
export function useProductEvidence(product: TrackedProduct, pack: SourcePack | null, stock?: StockRow, revision = 0) {
  const link = useInventorySku(product.sku, stock, revision, product.mode === "workspace");
  const canonicalSku = product.mode === "model" ? product.sku : link.data;
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
  return { evidence, canonicalSku, campaignId, loading: link.isFetching || model.isFetching || fee.isFetching,
    error: link.isError || (!!canonicalSku && model.isError) || fee.isError,
    retry: () => { void link.refetch(); if (canonicalSku) { void model.refetch(); void fee.refetch(); } } };
}
