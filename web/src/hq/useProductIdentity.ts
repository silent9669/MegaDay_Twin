import { useEffect, useState } from "react";
import { config } from "../lib/config";
import { requestJSON } from "../studio/account";
import type { TrackedProduct } from "./types";

type Identity = { code: string; sku: string | null; campaign_ids: string[] };
export function useProductIdentity(product: TrackedProduct | null) {
  const code = product?.sku, mode = product?.mode;
  const [reload, setReload] = useState(0);
  const [state, setState] = useState<{ code: string; data?: Identity; error?: string }>();
  useEffect(() => {
    if (!code || mode !== "workspace" || config.useFixtures) return;
    const controller = new AbortController();
    const refresh = (event: Event) => {
      // Applying a goal changes campaign state, not SKU identity. Keep the
      // mounted planner and its successful apply receipt visible.
      if ((event as CustomEvent<{ reason?: string }>).detail?.reason === "goal-applied") return;
      setState(undefined); setReload(n => n + 1);
    };
    void requestJSON<{ data: Identity }>(`/api/v1/sku-resolution?code=${encodeURIComponent(code)}`, { signal: controller.signal })
      .then(({ data }) => {
        if (data.code !== code) throw Error("Product mapping did not match the selected code.");
        if (!controller.signal.aborted) setState({ code, data });
      }).catch((e: unknown) => {
        if (!controller.signal.aborted) setState({ code, error: e instanceof Error ? e.message : "Could not resolve product." });
      });
    window.addEventListener("workspace:backend-refresh", refresh);
    return () => { controller.abort(); window.removeEventListener("workspace:backend-refresh", refresh); };
  }, [code, mode, reload]);
  const current = state?.code === code ? state : undefined;
  const direct = mode === "model";
  const loading = !!code && !direct && !config.useFixtures && !current;
  return {
    sku: direct ? code : current?.data?.sku ?? undefined,
    campaignIds: direct ? undefined : current?.data?.campaign_ids,
    loading,
    error: !code || direct ? "" : current?.error || (!current?.data?.sku && !loading
      ? "This shop code has no confirmed backend SKU. Send the sources and review SKU matching in Data Studio before using model goals or simulations." : ""),
    retry: () => { setState(undefined); setReload(n => n + 1); },
  };
}
