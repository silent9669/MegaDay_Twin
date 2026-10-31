import { evidenceText } from '../lib/evidence-text';
// Source chip next to a number (09 §4 `CitationChip`). Clicking opens a small
// popover with the source kind, id, tool and data time; Esc or a click outside closes it.
import { ExternalLink, X } from 'lucide-react';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router';
import { formatDateTime, formatTime } from '../lib/format';
import { viText } from '../lib/i18n';

export type Citation = {
  /** Short chip text: "Mô phỏng scn-2291", "Tồn kho 01:00". */
  label: string;
  kind: string;
  id?: string | null;
  tool?: string | null;
  dataAsOf?: string | null;
  computedAt?: string | null;
  /** In-app link to where the source lives (tier 3 anchor or SKU detail). */
  href?: string | null;
};

const t = viText.citation;

export function CitationChip({ citation, children }: { citation: Citation; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popId = useId();

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    function onDown(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDown);
    };
  }, [open]);

  const kindLabel = t.kind[citation.kind] ?? citation.kind;
  const rows: [string, string][] = [[t.sourceKind, kindLabel]];
  if (citation.id) rows.push([t.id, citation.id]);
  if (citation.tool) rows.push([t.tool, citation.tool]);
  if (citation.dataAsOf) rows.push([t.dataAsOf, formatDateTime(citation.dataAsOf)]);
  if (citation.computedAt) rows.push([t.computedAt, formatTime(citation.computedAt)]);

  return (
    <span ref={wrapRef} className="relative inline-flex items-baseline gap-1">
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls={open ? popId : undefined}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-baseline gap-1 rounded text-left"
      >
        {children != null ? (
          <span className="font-medium tabular-nums underline decoration-dotted underline-offset-4">{children}</span>
        ) : null}
        <span className="rounded border border-border bg-surface-2 px-1.5 text-xs font-normal text-muted">{evidenceText(citation.label)}</span>
      </button>
      {open ? (
        <span
          id={popId}
          role="dialog"
          aria-label={t.dialog(evidenceText(citation.label))}
          className="absolute left-0 top-full z-30 mt-1 block w-72 rounded-lg border border-border bg-surface p-3 text-left text-sm shadow-lg"
        >
          <span className="mb-2 flex items-start justify-between gap-2">
            <span className="font-medium">{evidenceText(citation.label)}</span>
            <button type="button" className="icon-btn -m-1 size-6" aria-label={t.close} onClick={() => setOpen(false)}>
              <X aria-hidden className="size-3.5" />
            </button>
          </span>
          <span className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
            {rows.map(([k, v]) => (
              <span key={k} className="contents">
                <span className="text-muted">{k}</span>
                <span className="tabular-nums">{v}</span>
              </span>
            ))}
          </span>
          {citation.href ? (
            <Link
              to={citation.href}
              onClick={() => setOpen(false)}
              className="mt-2 inline-flex items-center gap-1 text-accent hover:underline"
            >
              {t.open}
              <ExternalLink aria-hidden className="size-3.5" />
            </Link>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}
