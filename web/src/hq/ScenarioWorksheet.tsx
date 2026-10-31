import { Button } from "../components/ui/button";
import { Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { useSku } from "../api/queries";
import { config } from "../lib/config";
import type { SourcePack } from "../studio/sources";
import type { StockRow } from "../studio/source-view";
import { modelSuggestion, workspaceSuggestion, suggestedWorksheet } from "./worksheet-suggestions";
import { WorksheetCalculation } from "./WorksheetCalculation";
import { analyzeWithAI } from "../city/analysis-context";
import {
  blankWorksheet,
  worksheetResults,
  type Worksheet,
  type TrackedProduct,
} from "./types";
const fields: [keyof Worksheet, string][] = [
  ["stock", "Available units"],
  ["price", "Selling price (VND)"],
  ["cost", "Unit cost (VND)"],
  ["packaging", "Packaging / unit (VND)"],
  ["feePercent", "Platform fee (%)"],
  ["continueUnits", "Demand with ads (units)"],
  ["pauseUnits", "Demand without ads (units)"],
  ["spend", "New ad spend (VND)"],
];
const format = (value: number) =>
  new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
export function ScenarioWorksheet({
  product,
  saved,
  onSave,
  onChange: setValue,
  busy,
  pack,
  stock,
  revision,
}: {
  product: TrackedProduct;
  saved?: Worksheet;
  onChange: (value: Worksheet) => void;
  onSave: (value: Worksheet) => void;
  busy: boolean;
  pack: SourcePack | null;
  stock?: StockRow;
  revision: number;
}) {
  const model = useSku(product.mode === "model" ? product.sku : undefined);
  const evidence = useMemo(() => product.mode === "model"
    ? modelSuggestion(model.data?.data, config.useFixtures)
    : workspaceSuggestion(pack, product.sku, stock), [product, model.data, pack, stock]);
  const [previous, setPrevious] = useState<Worksheet | null>(null);
  const value = saved ?? blankWorksheet;
  const count = Object.keys(evidence).length;
  const results = worksheetResults(value);
  return (
    <section className="hq-worksheet" aria-label="Scenario worksheet">
      <header>
        <div>
          <h3>Continue or pause?</h3>
          <span className="hq-provenance">Product facts & scenario assumptions</span>
        </div>
        <Button variant="default" disabled={busy || !count || (product.mode === "model" && model.isFetching)} onClick={() => {
          setPrevious({ ...value });
          setValue(suggestedWorksheet(evidence));
        }}>
          <Sparkles size={16} /> Load best suggestion
        </Button>
      </header>
      <div className="hq-suggestion-status" role="status">
        {evidence.stock && value.stock !== null && evidence.stock.value !== value.stock && <Button variant="outline" disabled={busy} onClick={() => {
          setPrevious({ ...value });
          setValue({ ...value, stock: evidence.stock!.value });
        }}>Reload stock & recalculate</Button>}
        <span>{product.mode === "model" && model.isPending ? "Loading product data…"
          : model.isError && product.mode === "model" ? "Product data unavailable. Enter inputs manually or retry."
          : count ? `${count} of 8 inputs available from ${product.mode === "workspace" ? `workspace revision ${revision}` : config.useFixtures ? "the model snapshot" : "the decision backend"}. Loading replaces the form; demand and new ad spend need assumptions for the same period.`
          : "No unambiguous SKU-linked inputs. Add matching records in Data Studio or enter assumptions."}</span>
        {previous && <Button variant="ghost" onClick={() => { setValue(previous); setPrevious(null); }}>Undo load</Button>}
        {product.mode === "model" && model.isError && <Button variant="outline" onClick={() => void model.refetch()}>Retry</Button>}
      </div>
      <div className="hq-assumptions">
        {fields.map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              type="number"
              min="0"
              max={
                key === "feePercent"
                  ? 100
                  : key.includes("Units") || key === "stock"
                    ? 1e9
                    : 1e12
              }
              step="any"
              value={value[key] ?? ""}
              onChange={(e) => {
                const n = e.target.valueAsNumber;
                setValue({
                  ...value,
                  [key]: Number.isFinite(n)
                    ? Math.min(
                        key === "feePercent"
                          ? 100
                          : key.includes("Units") || key === "stock"
                            ? 1e9
                            : 1e12,
                        Math.max(0, n),
                      )
                    : null,
                });
              }}
            />
          </label>
        ))}
      </div>
      {results ? (
        <div
          className="hq-table-scroll"
          tabIndex={0}
          role="region"
          aria-label="Continue versus pause results"
        >
          <table className="hq-financial-table">
            <caption>Scenario estimate · demand is assumed · VND</caption>
            <thead>
              <tr>
                <th>Metric</th>
                <th>Continue ads</th>
                <th>Pause ads</th>
              </tr>
            </thead>
            <tbody>
              {(
                [
                  ["Units sold", "units"],
                  ["Units remaining", "leftover"],
                  ["Revenue", "revenue"],
                  ["Cost of goods + fees + packaging", "costs"],
                  ["Contribution before advertising", "beforeAds"],
                  ["New advertising spend", "spend"],
                  ["Result after these costs", "afterAds"],
                ] as const
              ).map(([label, key]) => (
                <tr key={key}>
                  <th scope="row">{label}</th>
                  {results.map((result, i) => (
                    <td key={i}>
                      {format(result[key])}
                      {key !== "units" && key !== "leftover" ? " VND" : ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="hq-empty">
          Enter the assumptions to compare the two options.
        </p>
      )}
      <WorksheetCalculation value={value} evidence={evidence} fields={fields} />
      <footer>
        <Button variant="outline"
          disabled={busy || !results}
          onClick={() => onSave(value)}
        >
          Save assumptions
        </Button>
        <Button variant="default"
          disabled={!results}
          onClick={() =>
            analyzeWithAI(
              {
                title: "Continue versus pause",
                source: "user-assumptions",
                sku: product.sku,
                data: JSON.stringify({
                  product,
                  assumptions: value,
                  results,
                  evidence,
                  revision: product.mode === "workspace" ? revision : undefined,
                  formula:
                    "units=min(stock,demand); costs=units*(cost+packaging+price*feePercent/100); afterAds=units*price-costs-spend",
                  excludes: [
                    "taxes",
                    "returns",
                    "fixed costs",
                    "restocking",
                    "leftover valuation",
                    "campaign constraints",
                  ],
                }),
              },
              "Explain this product comparison in English. Cite matching source values and distinguish them from edited inputs. Trace each result through its formula and explain the difference. Treat demand and ad spend as assumptions, not a forecast or an optimized recommendation.",
            )
          }
        >
          <Sparkles size={16} />
          Analyze with AI
        </Button>
      </footer>
    </section>
  );
}
