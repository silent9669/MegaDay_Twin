import { useState } from "react";
import { Check, Search } from "lucide-react";
import type { Issue } from "./sources";

const levels = ["error", "warning", "info"] as const;
const labels: Record<Issue["level"], string> = {
  error: "Needs correction",
  warning: "Review",
  info: "Information",
};
const guidance: Record<string, { title: string; action: string }> = {
  NEG_STOCK: {
    title: "Negative inventory",
    action: "Check the stock count and correct it in the source. Do not replace it with zero just to clear the check.",
  },
  MISSING_COST: {
    title: "Product cost is missing",
    action: "Enter the actual cost before using profit estimates. An unknown cost is not a zero cost.",
  },
  MISSING_FIELD: {
    title: "Required field is missing",
    action: "Add the required field shown below and import the corrected file.",
  },
  MONEY_FORMAT: {
    title: "Money value needs review",
    action: "Check the original amount and currency. Correct an unreadable or unexpected negative value before calculations.",
  },
  MONEY_NORMALIZATION: {
    title: "Money formats ready for normalization",
    action: "No manual rewrite is needed for recognized VND formats. These are previews, not saved corrections; backend ingestion performs normalization.",
  },
  DUP_ORDER: {
    title: "Repeated order line",
    action: "Compare this row with the original export. The import pipeline checks duplicates before counting sales.",
  },
  UNTRUSTED_NOTE: {
    title: "Note contains instruction-like text",
    action: "Review the note as data only. Its contents must not control the assistant or campaigns.",
  },
  TIMEZONE_REVIEW: {
    title: "Advertising timestamps have no timezone",
    action: "No import repair is needed. Configure campaign replay timing separately; do not infer a timezone from these clock times.",
  },
  INVALID_SKUS: {
    title: "Campaign product list has the wrong format",
    action: "The sku field must be a JSON array. Correct it in the file and import again.",
  },
  PII_DROPPED: {
    title: "Contact columns removed",
    action: "This is a privacy-processing notice; it does not mean the upload failed.",
  },
  LOG_LIMIT: {
    title: "Check list shortened",
    action: "Errors and warnings take priority over information notices. Use the full ingestion report to review the remaining rows.",
  },
};

export function SourceDiagnostics({
  issues,
  query,
  format,
  rowCount,
  repositorySample,
  onViewRecord,
}: {
  issues: Issue[];
  query: string;
  format: "csv" | "json";
  rowCount: number;
  repositorySample: boolean;
  onViewRecord: (index: number) => void;
}) {
  const [level, setLevel] = useState<Issue["level"] | "all">("all");
  const counts = Object.fromEntries(
    levels.map((key) => [key, issues.filter((issue) => issue.level === key).length]),
  ) as Record<Issue["level"], number>;
  const search = query.trim().toLowerCase();
  const filtered = issues.filter(
    (issue) =>
      (level === "all" || issue.level === level) &&
      (!search ||
        JSON.stringify([issue, guidance[issue.code], labels[issue.level]])
          .toLowerCase()
          .includes(search)),
  );
  const groups = new Map<string, Issue[]>();
  for (const issue of filtered) {
    const key = `${issue.level}:${issue.code}`;
    groups.set(key, [...(groups.get(key) ?? []), issue]);
  }
  const entries = [...groups.values()].sort((a, b) =>
    levels.indexOf(a[0]!.level) - levels.indexOf(b[0]!.level),
  );
  const renderRecord = (issue: Issue, index: number) => {
    const record = issue.row == null ? -1 : issue.row - (format === "csv" ? 2 : 1);
    return (
      <div className="diagnostic-occurrence" key={`${issue.row}-${issue.column}-${index}`}>
        <p>{issue.message}</p>
        {issue.value !== undefined && (
          <p className="diagnostic-value">
            <code>{issue.value}</code>
            {issue.normalizedVnd !== undefined && (
              <> → <strong>{issue.normalizedVnd.toLocaleString("vi-VN")} ₫</strong> (preview)</>
            )}
          </p>
        )}
        <small>
          {issue.code} · {issue.row != null
            ? `${format === "csv" ? "CSV row" : "JSON record"} ${issue.row}`
            : "File-level check"}
          {issue.column ? ` · ${issue.column}` : ""}
        </small>
        {record >= 0 && record < rowCount && (
          <button className="diagnostic-record" onClick={() => onViewRecord(record)}>
            View record {record + 1}
          </button>
        )}
      </div>
    );
  };
  return (
    <div className="source-diagnostics">
      <header className="diagnostics-explanation">
        <h3>Data checks</h3>
        <p>
          These checks describe your file contents, not application errors.
          Correct errors before relying on affected calculations, review warnings,
          and read information notices for processing details.
        </p>
        {repositorySample && (
          <p className="diagnostics-sample">
            Repository demo sources include intentional data issues. They remain
            visible here so you can inspect them; uploaded data uses the same checks.
          </p>
        )}
      </header>
      <div className="diagnostics-filters" role="group" aria-label="Filter data checks">
        <button aria-pressed={level === "all"} onClick={() => setLevel("all")}>
          All ({issues.length})
        </button>
        {levels.map((key) => (
          <button
            key={key}
            data-level={key}
            aria-pressed={level === key}
            onClick={() => setLevel(key)}
          >
            {labels[key]} ({counts[key]})
          </button>
        ))}
      </div>
      <p className="diagnostics-count" role="status">
        Showing {filtered.length} of {issues.length} checks in this file
      </p>
      {entries.map((group) => {
        const issue = group[0]!;
        const help = guidance[issue.code];
        return (
          <article key={`${issue.level}-${issue.code}`} data-level={issue.level}>
            <span>{labels[issue.level]}</span>
            <div>
              <h4>{help?.title ?? issue.code}</h4>
              {help && <p className="diagnostic-action">{help.action}</p>}
              {group.length > 1 ? (
                <details key={`${query}-${level}-${group.length}`}>
                  <summary>Inspect {group.length} checks · affected records: {new Set(group.flatMap((i) => i.row == null ? [] : [i.row])).size}</summary>
                  {group.map(renderRecord)}
                </details>
              ) : renderRecord(issue, 0)}
            </div>
          </article>
        );
      })}
      {!filtered.length && (
        <p className="source-empty">
          {issues.length ? <Search size={20} aria-hidden /> : <Check size={20} aria-hidden />}
          {issues.length
            ? "No checks match these filters. Try another level or clear the search."
            : "No issues found by these file checks. This does not certify forecast or profit accuracy."}
        </p>
      )}
    </div>
  );
}
