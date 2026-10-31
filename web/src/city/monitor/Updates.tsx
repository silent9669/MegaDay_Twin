import { ArrowUpRight, X } from "lucide-react";
import { Link } from "react-router";
import type { ChangeNotice } from "./changes";
import type { WorkspaceUpdates } from "./useWorkspaceMonitor";
export function Updates({
  updates,
  onExplain,
  onNavigate,
}: {
  updates: WorkspaceUpdates;
  onExplain: (notice: ChangeNotice) => void;
  onNavigate: () => void;
}) {
  return (
    <section className="milo-updates" aria-label="Workspace updates">
      {!!updates.notices.length && <div className="updates-actions"><button onClick={updates.readAll}>Read all</button></div>}
      {!updates.notices.length && (
        <div className="updates-empty">
          <h3>No changes yet</h3>
          <p>Updates appear here while this workspace is open.</p>
        </div>
      )}
      {updates.notices.map((notice) => (
        <article key={notice.id}>
          <header>
            <h3>{notice.title}</h3>
            <button
              aria-label={`Dismiss ${notice.title}`}
              onClick={() => updates.dismiss(notice.id)}
            >
              <X size={16} />
            </button>
          </header>
          <p>{notice.detail}</p>
          <small>
            {notice.simulation ? "Simulation · " : ""}
            {notice.reconnected ? "Since reconnect · " : ""}
            <time dateTime={notice.observedAt}>
              {new Date(notice.observedAt).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })}
            </time>
          </small>
          <footer>
            <Link to={notice.href} onClick={() => { if (notice.kind === "stock-restocked") window.dispatchEvent(new CustomEvent("workspace:changed", { detail: "sources" })); onNavigate(); }}>
              {notice.backendCampaign ? "Mở campaign để cập nhật & đề xuất lại" : notice.kind === "stock-restocked" ? "Reload inventory" : "View source"} <ArrowUpRight size={14} />
            </Link>
            {!notice.backendCampaign && <button onClick={() => onExplain(notice)}>{notice.kind === "stock-restocked" ? "Analyze again" : "Explain"}</button>}
          </footer>
        </article>
      ))}
    </section>
  );
}
