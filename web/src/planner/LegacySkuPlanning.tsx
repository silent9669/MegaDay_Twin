import { Navigate, useSearchParams } from "react-router";

export function LegacySkuPlanning() {
  const [params] = useSearchParams();
  const next = new URLSearchParams(params);
  next.set("scope", "sku");
  return <Navigate to={`/sale-planner?${next}`} replace />;
}
