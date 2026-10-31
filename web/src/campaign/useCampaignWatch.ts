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
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
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
        if (!disposed) timer = setTimeout(() => void poll(), 15000);
      }
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, []);
  return { context, error, acceptContext: setContext };
}
