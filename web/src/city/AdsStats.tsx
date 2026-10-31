import { useMemo } from "react";
import { Link } from "react-router";
import { inspect, normalize, type SourcePack } from "../studio/sources";
import { analyzeWithAI } from "./analysis-context";
export function AdsStats({
  pack,
  revision,
}: {
  pack: SourcePack | null;
  revision: number;
}) {
  const tables = useMemo(
    () =>
      pack?.files
        .filter((f) => f.kind === "ads")
        .map((file) => ({ file, parsed: inspect(file.name, file.text) })) ?? [],
    [pack],
  );
  const field = (row: Record<string, unknown>, names: string[]) =>
    Object.entries(row).find(([key]) => names.includes(normalize(key)))?.[1];
  const records = tables.flatMap((t) => t.parsed.rows);
  const ids = new Set(
    records
      .map((row) =>
        String(
          field(row, ["ma chien dich", "campaign_id", "campaign id"]) ?? "",
        ),
      )
      .filter(Boolean),
  );
  return (
    <section className="ads-source-stats">
      <header>
        <h3>Ad observations</h3>
        <Link to="/city-demo?district=hq&hq=campaigns">Campaigns in HQ</Link>
      </header>
      <dl className="source-metrics">
        <div>
          <dt>Saved records</dt>
          <dd>{records.length.toLocaleString("en-US")}</dd>
        </div>
        <div>
          <dt>Campaign IDs</dt>
          <dd>{ids.size}</dd>
        </div>
        <div>
          <dt>Source tables</dt>
          <dd>{tables.length}</dd>
        </div>
      </dl>
      {!tables.length ? (
        <p>No ad performance table imported.</p>
      ) : (
        <>
          <button
            className="btn-secondary"
            onClick={() =>
              analyzeWithAI(
                {
                  title: "Ad observations",
                  source: "workspace",
                  revision,
                  data: JSON.stringify({
                    files: tables.map((t) => ({
                      name: t.file.name,
                      records: t.parsed.rows.length,
                    })),
                    records: records.length,
                    campaigns: ids.size,
                  }),
                },
                "Inspect the uploaded ad observations. Identify the reporting period, platforms, spend and clicks from cited source records. Check whether values are cumulative before aggregating. Do not infer causal ROAS or allocate campaign results to individual products.",
              )
            }
          >
            Analyze with AI
          </button>
          {tables.map(({ file, parsed }) => (
            <details key={file.name}>
              <summary>
                {file.name} · {parsed.rows.length} records
              </summary>
              <div className="hq-table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Campaign</th>
                      <th>Reported time</th>
                      <th>Cost (VND)</th>
                      <th>Clicks</th>
                      <th>Impressions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {parsed.rows.slice(-20).map((row, i) => (
                      <tr key={i}>
                        <td>
                          {String(
                            field(row, ["ma chien dich", "campaign_id"]) ?? "—",
                          )}
                        </td>
                        <td>
                          {String(
                            field(row, ["thoi gian", "timestamp"]) ?? "—",
                          )}
                        </td>
                        <td>
                          {String(field(row, ["chi phi", "spend"]) ?? "—")}
                        </td>
                        <td>
                          {String(field(row, ["luot click", "clicks"]) ?? "—")}
                        </td>
                        <td>
                          {String(
                            field(row, ["luot hien thi", "impressions"]) ?? "—",
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>Last 20 source records · reported observations</p>
            </details>
          ))}
        </>
      )}
    </section>
  );
}
