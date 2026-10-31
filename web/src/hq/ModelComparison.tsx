import { Link } from "react-router";
import { ComparePage } from "../pages/compare/ComparePage";
import { useProductIdentity } from "./useProductIdentity";
import type { TrackedProduct } from "./types";

export function ModelComparison({ product }: { product: TrackedProduct }) {
  const identity = useProductIdentity(product);
  if (identity.loading) return <p role="status">Checking the product's backend SKU…</p>;
  if (!identity.sku) return <div role="alert"><p>{identity.error}</p><Link to="/mock-data">Review source matching</Link> <button onClick={identity.retry}>Retry mapping</button></div>;
  return <ComparePage key={identity.sku} sku={identity.sku} />;
}
