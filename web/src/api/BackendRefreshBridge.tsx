import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';

const liveKeys = new Set(['health', 'freshness', 'live-status', 'skus', 'sku', 'campaign', 'recommendations', 'recommendation', 'alerts']);

/** Workspace mutations also change Python-backed views outside Data Studio. */
export function BackendRefreshBridge() {
  const client = useQueryClient();
  useEffect(() => {
    const refresh = () => {
      void client.invalidateQueries({ predicate: query => liveKeys.has(String(query.queryKey[0])) });
    };
    window.addEventListener('workspace:backend-refresh', refresh);
    window.addEventListener('workspace:campaign-update', refresh);
    return () => {
      window.removeEventListener('workspace:backend-refresh', refresh);
      window.removeEventListener('workspace:campaign-update', refresh);
    };
  }, [client]);
  return null;
}
