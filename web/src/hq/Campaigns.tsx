import { Button } from "../components/ui/button";
import { useMemo, useState } from "react";
import { inspect, type SourcePack } from "../studio/sources";
import { analyzeWithAI } from "../city/analysis-context";
import { useHQSelection } from "./selection";
export function Campaigns({
  pack,
  revision,
}: {
  pack: SourcePack | null;
  revision: number;
}) {
  const selection = useHQSelection(),
    sku = selection?.product?.sku;
  const [search, setSearch] = useState("");
  const [scope, setScope] = useState("product");
  const campaigns = useMemo(
    () =>
      pack?.files
        .filter((file) => file.kind === "campaigns")
        .flatMap((file) =>
          inspect(file.name, file.text).rows.map((row, index) => ({
            row,
            file: file.name,
            record: index + 1,
          })),
        ) ?? [],
    [pack],
  );
  const rows = campaigns.filter(
    ({ row }) =>
      (!sku || scope === "all" || (Array.isArray(row.sku) && row.sku.includes(sku))) &&
      JSON.stringify(row).toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <section className="hq-campaigns">
      <header className="hq-product-toolbar">
        <h2>Campaigns</h2>
        {sku && <label>Scope<select aria-label="Campaign scope" value={scope} onChange={e => setScope(e.target.value)}><option value="product">{sku}</option><option value="all">All campaigns</option></select></label>}
        <Button variant="outline" onClick={() => selection?.tab("goals")}>Plan a goal</Button>
        <input
          aria-label="Search campaigns"
          placeholder="Search campaigns"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </header>
      {!rows.length ? (
        <p className="hq-empty">
          {search ? "No campaigns match your search." : sku && scope === "product"
            ? "No campaign source explicitly links this product."
            : "Import campaign structure in Data Studio to view campaigns."}
        </p>
      ) : (
        rows.map(({ row, file, record }) => (
          <article className="hq-campaign-card" key={`${file}:${record}`}>
            <header>
              <h3>
                {String(
                  row.ten_chien_dich ??
                    row.name ??
                    row.ma_chien_dich ??
                    "Campaign",
                )}
              </h3>
              <span>{String(row.ma_chien_dich ?? row.id ?? "—")}</span>
            </header>
            <dl className="source-metrics">
              <div>
                <dt>Daily budget (VND)</dt>
                <dd>
                  {typeof row.ngan_sach_ngay === "number"
                    ? row.ngan_sach_ngay.toLocaleString("en-US")
                    : "—"}
                </dd>
              </div>
              <div>
                <dt>Linked products</dt>
                <dd>{Array.isArray(row.sku) ? row.sku.length : "—"}</dd>
              </div>
              <div>
                <dt>Start</dt>
                <dd>{String(row.bat_dau ?? "—")}</dd>
              </div>
            </dl>
            <div className="hq-campaign-products">
              {Array.isArray(row.sku) &&
                row.sku.map((code: unknown) => (
                  <Button variant="outline"
                    key={String(code)}
                    onClick={() =>
                      selection?.open(
                        {
                          sku: String(code),
                          name: String(code),
                          mode: "workspace",
                        },
                        "details",
                      )
                    }
                  >
                    {String(code)}
                  </Button>
                ))}
            </div>
            <footer>
              <span>
                {file} · record {record}
              </span>
              <Button variant="outline"
                onClick={() =>
                  analyzeWithAI(
                    {
                      title: "Campaign review",
                      source: "workspace",
                      revision,
                      sku,
                      data: JSON.stringify({ file, record, row }).slice(
                        0,
                        24000,
                      ),
                    },
                    "Review this campaign and its linked products in English. Inspect workspace records with tools. Explain the budget scope, available evidence and missing inputs without allocating campaign totals to individual SKUs unless the source provides that allocation.",
                  )
                }
              >
                Analyze with AI
              </Button>
            </footer>
          </article>
        ))
      )}
    </section>
  );
}
