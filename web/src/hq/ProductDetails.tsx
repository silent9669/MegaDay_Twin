import { readSource } from "../studio/read-source";
import { config } from "../lib/config";
import { useMemo } from "react";
import { useSku } from "../api/queries";
import { normalize, type SourcePack } from "../studio/sources";
import type { StockRow } from "../studio/source-view";
import { analyzeWithAI } from "../city/analysis-context";
import { ErrorState, SkeletonRows } from "../components/states";
import type { TrackedProduct } from "./types";
const codeColumns = new Set([
  "ma noi bo",
  "sku",
  "sku_id",
  "sku id",
  "ma sku",
  "sku phan loai hang",
  "seller sku",
  "merchant sku",
]);
function matchingRecords(pack: SourcePack | null, sku: string) {
  return (pack?.files ?? [])
    .map((file) => {
      const parsed = readSource(file),
        columns = parsed.columns.filter((c) => codeColumns.has(normalize(c)));
      const rows = parsed.rows.filter((row) =>
        columns.some((c) =>
          Array.isArray(row[c])
            ? row[c].some((value) => String(value).trim() === sku)
            : String(row[c] ?? "").trim() === sku,
        ),
      );
      return {
        name: file.name,
        kind: file.kind,
        columns: parsed.columns,
        rows,
      };
    })
    .filter((file) => file.rows.length);
}
export function ProductDetails({
  product,
  pack,
  stock,
  revision,
}: {
  product: TrackedProduct;
  pack: SourcePack | null;
  stock?: StockRow;
  revision: number;
}) {
  const records = useMemo(
    () => matchingRecords(pack, product.sku),
    [pack, product.sku],
  );
  if (product.mode === "model") return <ModelDetails sku={product.sku} />;
  return (
    <section className="hq-details">
      <header>
        <h3>{product.name}</h3>
        <button
          className="btn-secondary"
          onClick={() =>
            analyzeWithAI(
              {
                title: "Product source review",
                source: "workspace",
                sku: product.sku,
                revision,
                data: JSON.stringify({
                  stock,
                  sourceTables: records.map((f) => ({
                    name: f.name,
                    matchingRows: f.rows.length,
                  })),
                  join: "Exact SKU column matches only; unlinked product names are not joined.",
                }),
              },
              "Review this product in English. Use the workspace tools to inspect its source records, summarize inventory and costs with citations, identify missing inputs and explain what can and cannot be inferred. No invented SKU joins.",
            )
          }
        >
          Analyze with AI
        </button>
      </header>
      <dl className="source-metrics">
        <div>
          <dt>SKU</dt>
          <dd>{product.sku}</dd>
        </div>
        <div>
          <dt>Available units</dt>
          <dd>{stock?.stock ?? "—"}</dd>
        </div>
        <div>
          <dt>Updated</dt>
          <dd>{stock?.asOf || "—"}</dd>
        </div>
        <div>
          <dt>Demand label</dt>
          <dd>{stock?.demand || "Unclassified"}</dd>
        </div>
      </dl>
      {!stock && (
        <p role="status">
          This tracked product is no longer in the current inventory.
        </p>
      )}
      {records.length ? (
        records.map((file) => (
          <details key={file.name}>
            <summary>
              {file.name} · {file.rows.length} matching records
            </summary>
            <div className="hq-table-scroll" tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    {file.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {file.rows.slice(0, 30).map((r, i) => (
                    <tr key={i}>
                      {file.columns.map((c) => (
                        <td key={c}>
                          {typeof r[c] === "object"
                            ? JSON.stringify(r[c])
                            : String(r[c] ?? "—")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {file.rows.length > 30 && (
              <p>First 30 records. Open Data Studio for the full table.</p>
            )}
          </details>
        ))
      ) : (
        <p>No source rows have an exact SKU match.</p>
      )}
    </section>
  );
}
function ModelDetails({ sku }: { sku: string }) {
  const query = useSku(sku),
    item = query.data?.data;
  if (query.isPending) return <SkeletonRows rows={4} />;
  if (query.isError)
    return (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    );
  if (!item) return null;
  const number = (value: number | null | undefined) =>
    value == null ? "—" : new Intl.NumberFormat("en-US").format(value);
  return (
    <section className="hq-details">
      <header>
        <h3>{item.name}</h3>
        <button
          className="btn-secondary"
          onClick={() =>
            analyzeWithAI(
              {
                title: "Model product details",
                source: config.useFixtures ? "model-snapshot" : "model-api",
                sku,
                data:
                  JSON.stringify(query.data).slice(0, 23900) +
                  (JSON.stringify(query.data).length > 23900
                    ? " [Context truncated; inspect source tools for more records.]"
                    : ""),
              },
              "Explain these saved model product metrics in English, including stock cover, effective price, costs and missing data. Distinguish the model snapshot from current uploaded sources.",
            )
          }
        >
          Analyze with AI
        </button>
      </header>
      <dl className="source-metrics">
        {[
          ["Available units", item.stock_available],
          ["Sold today", item.sold_today_units],
          ["Cover (hours, median)", item.hours_of_cover?.p50],
          ["Effective price (VND)", item.price_effective_vnd],
          ["Unit cost (VND)", item.cost?.unit_cost_vnd],
          ["Contribution / unit (VND)", item.contribution_per_unit_vnd],
        ].map(([label, value]) => (
          <div key={String(label)}>
            <dt>{String(label)}</dt>
            <dd>{number(value as number | null | undefined)}</dd>
          </div>
        ))}
      </dl>
      <h4>Incoming inventory</h4>
      {item.incoming?.length ? (
        <ul>
          {item.incoming.map((r, i) => (
            <li key={i}>
              {String(r.qty ?? "—")} units · {String(r.planned_date ?? "—")}
            </li>
          ))}
        </ul>
      ) : (
        <p>No incoming inventory in this snapshot.</p>
      )}
      <h4>Campaign</h4>
      <p>
        {item.campaign?.id ?? "No linked campaign"}
        {Array.isArray(item.campaign_detail?.shared_with) &&
        item.campaign_detail.shared_with.length
          ? ` · Shared with ${item.campaign_detail.shared_with.join(", ")}`
          : ""}
      </p>
      <details>
        <summary>Source evidence</summary>
        <pre>{JSON.stringify(query.data?.meta, null, 2)}</pre>
      </details>
    </section>
  );
}
