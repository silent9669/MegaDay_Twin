import { Button } from "../components/ui/button";
import { loadModelComparison, type ModelComparison } from "./model-comparison";
import { Sparkles } from "lucide-react";
import { useState } from "react";
import { useProductEvidence } from "./product-evidence";
import type { SourcePack } from "../studio/sources";
import type { StockRow } from "../studio/source-view";
import { suggestedWorksheet, autofillWorksheet } from "./worksheet-suggestions";
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
  model: modelDraft,
  onModelChange,
}: {
  product: TrackedProduct;
  saved?: Worksheet;
  onChange: (value: Worksheet) => void;
  onSave: (value: Worksheet) => void;
  busy: boolean;
  pack: SourcePack | null;
  stock?: StockRow;
  revision: number;
  model?: ModelComparison | null;
  onModelChange?: (value: ModelComparison | null) => void;
}) {
  const { evidence, canonicalSku, campaignId, loading, error, retry } = useProductEvidence(product, pack, stock, revision);
  const [previous, setPrevious] = useState<Worksheet | null>(null);
  const [cleared, setCleared] = useState<Partial<Record<keyof Worksheet, boolean>>>({});
  const value = autofillWorksheet(saved ?? blankWorksheet, evidence, cleared);
  const [localModel, setLocalModel] = useState<ModelComparison | null>(null);
  const generated = modelDraft === undefined ? localModel : modelDraft;
  const setGenerated = onModelChange ?? setLocalModel;
  const [generating, setGenerating] = useState(false);
  const [generationError, setGenerationError] = useState("");
  const currentModel = generated && JSON.stringify(generated.value) === JSON.stringify(value) ? generated : null;
  const count = Object.keys(evidence).length;
  const results = currentModel?.results ?? worksheetResults(value);
  return (
    <section className="hq-worksheet" aria-label="Scenario worksheet">
      <header>
        <div>
          <h3>Continue or pause?</h3>
          <span className="hq-provenance">Product facts & scenario assumptions</span>
        </div>
        <Button variant="default" disabled={busy || loading || generating || (!count && !campaignId)} onClick={async () => {
          setPrevious({ ...value });
          setGenerationError("");
          if (!canonicalSku || !campaignId) {
            setCleared({});
            setValue(suggestedWorksheet(evidence));
            setGenerated(null);
            setGenerationError("Available facts loaded. A linked campaign is required for model estimates.");
            return;
          }
          setGenerating(true);
          try {
            const comparison = await loadModelComparison(canonicalSku, campaignId);
            setCleared({});
            setGenerated(comparison);
            setValue(comparison.value);
          } catch (error) {
            setGenerationError(error instanceof Error ? error.message : "Could not load model estimates.");
          } finally { setGenerating(false); }
        }}>
          <Sparkles size={16} /> {generating ? "Calculating comparison…" : "Load best suggestion"}
        </Button>
      </header>
      <div className="hq-suggestion-status" role="status">
        {!currentModel && evidence.stock && value.stock !== null && evidence.stock.value !== value.stock && <Button variant="outline" disabled={busy} onClick={() => {
          setPrevious({ ...value });
          setValue({ ...value, stock: evidence.stock!.value });
        }}>Reload stock & recalculate</Button>}
        <span>{loading ? "Loading product data…"
          : error ? "Product data unavailable. Enter inputs manually or retry."
          : count ? `${count} of 8 inputs loaded. Demand and ad spend need assumptions.`
          : "No unambiguous SKU-linked inputs. Add matching records in Data Studio or enter assumptions."}</span>
        {previous && <Button variant="ghost" onClick={() => { setValue(previous); setPrevious(null); }}>Undo load</Button>}
        {error && <Button variant="outline" onClick={retry}>Retry</Button>}
      </div>
      {generationError && <p role="alert">{generationError}</p>}
      {currentModel && <p role="status">{currentModel.trace.horizon_hours}-hour model preview · {new Date(currentModel.trace.start).toLocaleString("en-GB", { timeZone: "Asia/Ho_Chi_Minh", dateStyle: "medium", timeStyle: "short" })} (Vietnam) · Ad spend allocation: {(currentModel.share * 100).toFixed(1)}%</p>}
      {currentModel && evidence.stock && evidence.stock.value !== value.stock && <p role="status">Model starting stock: {value.stock}; current workspace stock: {evidence.stock.value}. These snapshots have different data clocks.</p>}
      {currentModel && currentModel.results.every(row => row.units === 0) && <p role="status">The model estimates no direct sales for this SKU in this period. Bundles may still consume its stock.</p>}
      {currentModel?.violations.map(message => <p role="alert" key={message}>{message}</p>)}
      <div className="hq-assumptions">
        {fields.map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              disabled={generating}
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
                setCleared(previous => ({ ...previous, [key]: !Number.isFinite(n) }));
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
            <caption>{currentModel ? "Backend model estimates · VND" : "Scenario estimate · demand is assumed · VND"}</caption>
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
                  [currentModel ? "Units sold directly" : "Units sold", "units"],
                  [currentModel ? "Stock remaining, including bundle use" : "Units remaining", "leftover"],
                  ["Revenue", "revenue"],
                  ["Cost of goods + fees + packaging", "costs"],
                  ["Contribution before advertising", "beforeAds"],
                  [currentModel ? "Allocated new advertising spend" : "New advertising spend", "spend"],
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
      {currentModel ? <details className="hq-calculation"><summary>How this is calculated</summary>
        <p>Sales, remaining stock, revenue and contribution use the backend’s simulated means over the stated period, including hourly prices and restocking.</p>
        <p>Selling costs = revenue − contribution before ads. Allocated advertising spend = campaign spend × {(currentModel.share * 100).toFixed(1)}% forecast revenue share. Result after these costs = contribution − allocated spend. This allocation is an accounting assumption, not measured SKU advertising impact.</p>
        <p>The per-unit fields summarize starting economics. Editing an input switches to a manual comparison.</p>
        <p>{currentModel.source}</p>
        <p>Product stock can also be consumed by bundles. Stock changes are not always equal to direct sales. These estimates are not guarantees or full accounting profit.</p>
      </details> : <WorksheetCalculation value={value} evidence={evidence} fields={fields} />}
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
                source: currentModel ? "model-api" : "user-assumptions",
                sku: product.sku,
                data: JSON.stringify({
                  product,
                  assumptions: value,
                  results,
                  evidence: currentModel?.evidence ?? evidence,
                  model: currentModel ? { source: currentModel.source, adSpendShare: currentModel.share, allocation: "forecast revenue share", violations: currentModel.violations } : undefined,
                  revision: product.mode === "workspace" ? revision : undefined,
                  formula:
                    currentModel ? "Simulated SKU means; costs=revenue-contribution; allocatedSpend=campaignSpend*forecastRevenueShare; afterAds=contribution-allocatedSpend" : "units=min(stock,demand); costs=units*(cost+packaging+price*feePercent/100); afterAds=units*price-costs-spend",
                  excludes: currentModel ? ["causal SKU ad attribution", "full accounting profit"] : [
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
