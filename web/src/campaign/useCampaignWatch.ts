import { useEffect, useState } from "react";
import { requestJSON } from "../studio/account";
import type { CampaignContext } from "./types";

/** Uses the backend data revision, not the decorative City clock. One watcher per City. */
export function useCampaignWatch() {
  const [context, setContext] = useState<CampaignContext | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    let last: string | null = null;
    let polling = false, refreshRequested = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (polling) { refreshRequested = true; return; }
      polling = true;
      try {
        const next = await requestJSON<CampaignContext>("/api/v1/campaign-analysis/CD002");
        if (disposed) return;
        setContext(next); setError("");
        if (last && last !== next.revision) window.dispatchEvent(new CustomEvent("workspace:campaign-update", {
          detail: { revision: next.revision, dataAsOf: next.dataAsOf },
        }));
        last = next.revision;
      } catch (e) {
        if (!disposed) setError(e instanceof Error ? e.message : "Chưa đọc được backend.");
      } finally {
        polling = false;
        if (!disposed) timer = setTimeout(() => void poll(), refreshRequested ? 0 : 15000);
        refreshRequested = false;
      }
    };
    void poll();
    const refresh = () => { clearTimeout(timer); void poll(); };
    window.addEventListener("workspace:backend-refresh", refresh);
    return () => { disposed = true; clearTimeout(timer); window.removeEventListener("workspace:backend-refresh", refresh); };
  }, []);
  return { context, error, acceptContext: setContext };
}
