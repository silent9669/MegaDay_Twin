// Confidence, compute-state and freshness badges (09 §4). CitationChip, MoneyVND,
// Range and ZoneBar live in their own files.
import type { ComputeState, Confidence, FreshnessSource } from '../api/types';
import { formatDateTime, formatTime } from '../lib/format';
import { viText } from '../lib/i18n';

const confidenceStyle: Record<Confidence, { box: string; dot: string }> = {
  verified: { box: 'border-ok/50 bg-ok-soft text-ok', dot: 'bg-ok' },
  reference: { box: 'border-warn/60 text-warn', dot: 'bg-[linear-gradient(90deg,currentColor_50%,transparent_50%)] border border-current' },
  scenario: { box: 'border-dashed border-neutral/60 text-neutral', dot: 'border border-current' },
};

/** Confidence label: always text + dot shape (solid / half / hollow), never color alone. */
export function ConfidenceBadge({ value }: { value: Confidence }) {
  const s = confidenceStyle[value];
  const { label, hint } = viText.confidence[value];
  return (
    <span title={hint} className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${s.box}`}>
      <span aria-hidden className={`size-2 rounded-full ${s.dot}`} />
      {label}
    </span>
  );
}

export function ComputeStateTag({ state, computedAt }: { state: ComputeState; computedAt?: string }) {
  if (state === 'fresh') {
    return <span className="text-muted tabular-nums">{computedAt ? formatTime(computedAt) : viText.computeState.fresh}</span>;
  }
  const tone = state === 'failed' ? 'text-danger' : state === 'stale' ? 'text-warn' : 'text-accent';
  return (
    <span className={`text-xs font-medium ${tone}`}>
      {viText.computeState[state]}
      {computedAt ? ` · ${formatTime(computedAt)}` : ''}
    </span>
  );
}

const freshnessDot: Record<FreshnessSource['status'], string> = {
  fresh: 'bg-ok',
  partial: 'bg-[linear-gradient(90deg,var(--warn)_50%,transparent_50%)] border border-warn',
  stale: 'border border-danger',
  missing: 'border border-danger',
};

export function FreshnessBadge({ source }: { source: FreshnessSource }) {
  const label = viText.freshnessSource[source.source] ?? source.label;
  const status = viText.freshnessStatus[source.status] ?? source.status;
  const extra = [
    source.lag_minutes != null ? viText.freshnessLag(source.lag_minutes, source.threshold_minutes) : null,
    source.missing_skus ? viText.freshnessMissingSkus(source.missing_skus) : null,
  ].filter(Boolean);
  const tip = [viText.freshnessTooltip(label, formatDateTime(source.as_of), status), ...extra].join(', ');
  return (
    <span title={tip} aria-label={tip} className="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-xs">
      {label}
      <span aria-hidden className={`size-2 rounded-full ${freshnessDot[source.status]}`} />
    </span>
  );
}
