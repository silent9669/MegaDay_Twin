import { ArrowLeft, MessageSquare, Search } from "lucide-react";
import { useState } from "react";
import type { ChatSession } from "../studio/workspace-types";

export function ChatHistory({
  sessions,
  activeId,
  busy,
  onSelect,
  onBack,
}: {
  sessions: ChatSession[];
  activeId: string | null;
  busy: boolean;
  onSelect: (id: string) => void;
  onBack: () => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = [...sessions]
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .filter((s) => s.title.toLowerCase().includes(query.toLowerCase()));
  return (
    <section className="chat-history" aria-label="Chat history">
      <div className="chat-history-heading">
        <button
          type="button"
          className="city-icon"
          aria-label="Back to chat"
          onClick={onBack}
        >
          <ArrowLeft />
        </button>
        <h3>Chat history</h3>
        <span>{sessions.length}</span>
      </div>
      <label className="chat-search">
        <Search size={16} />
        <input
          aria-label="Search chats"
          placeholder="Search chats"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </label>
      <div className="chat-history-list">
        {filtered.length ? (
          filtered.map((session) => (
            <button
              type="button"
              key={session.id}
              disabled={busy}
              aria-current={activeId === session.id ? "true" : undefined}
              onClick={() => onSelect(session.id)}
            >
              <MessageSquare size={17} />
              <span>
                <strong>{session.title}</strong>
                <small>{session.model}</small>
              </span>
              <time dateTime={session.updatedAt}>
                {new Date(session.updatedAt).toLocaleDateString(undefined, {
                  month: "short",
                  day: "numeric",
                })}
              </time>
            </button>
          ))
        ) : (
          <p>
            {query
              ? "No matching chats."
              : "Your conversations will appear here."}
          </p>
        )}
      </div>
    </section>
  );
}
