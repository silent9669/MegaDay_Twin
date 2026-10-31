import { Inbox, Info, RefreshCw, TriangleAlert, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { ApiError } from '../api/client';
import { formatTime } from '../lib/format';
import { viText } from '../lib/i18n';

export function PageHeader({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return (
    <header className="mb-5 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1 max-w-3xl text-muted">{description}</p>
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function EmptyState({
  title,
  body,
  icon: Icon = Inbox,
  action,
}: {
  title: string;
  body: string;
  icon?: LucideIcon;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center rounded-lg border border-dashed border-border bg-surface px-6 py-12 text-center">
      <Icon aria-hidden className="mb-3 size-8 text-muted" strokeWidth={1.5} />
      <h2 className="font-medium">{title}</h2>
      <p className="mt-1 max-w-md text-muted">{body}</p>
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : String(error);
  const code = error instanceof ApiError ? error.code : null;
  return (
    <div role="alert" className="flex items-start gap-3 rounded-lg border border-danger/40 bg-danger-soft px-4 py-3">
      <TriangleAlert aria-hidden className="mt-0.5 size-5 shrink-0 text-danger" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{viText.state.errorTitle}</p>
        <p className="mt-0.5 text-muted">{message}</p>
        {code ? <p className="mt-0.5 text-xs text-muted">{viText.state.errorCode(code)}</p> : null}
      </div>
      {onRetry ? (
        <button type="button" onClick={onRetry} className="btn-secondary">
          <RefreshCw aria-hidden className="size-4" />
          {viText.state.retry}
        </button>
      ) : null}
    </div>
  );
}

/** Layout-shaped placeholder rows (09 §4: skeleton, not a centered spinner). */
export function SkeletonRows({ rows = 3, label = viText.state.loading }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-2">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="h-14 animate-pulse rounded-lg bg-surface-2" />
      ))}
    </div>
  );
}

/**
 * Page-top band (09 §4): yellow "data older than threshold" or grey "scenario /
 * assumption". Text always states the consequence, not just the colour.
 */
export function Banner({ tone, children }: { tone: 'warn' | 'neutral'; children: ReactNode }) {
  const style =
    tone === 'warn'
      ? 'border-warn/50 bg-warn-soft text-fg'
      : 'border-dashed border-neutral/60 bg-neutral-soft text-fg';
  const Icon = tone === 'warn' ? TriangleAlert : Info;
  return (
    <div role="status" className={`mb-4 flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${style}`}>
      <Icon aria-hidden className={`mt-0.5 size-4 shrink-0 ${tone === 'warn' ? 'text-warn' : 'text-neutral'}`} />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/**
 * Old data stays visible at 60% while a recompute runs, with a tag
 * "Đang cập nhật · dữ liệu lúc 13:05" (09 §4).
 */
export function UpdatingRegion({
  updating,
  dataAsOf,
  children,
  className = '',
}: {
  updating: boolean;
  dataAsOf?: string | null;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div aria-busy={updating} className={`relative ${className}`}>
      {updating ? (
        <span className="absolute right-2 top-2 z-10 inline-flex items-center gap-1 rounded-full border border-accent/40 bg-surface px-2 py-0.5 text-xs font-medium text-accent">
          <RefreshCw aria-hidden className="size-3 animate-spin" />
          {viText.banner.updating(dataAsOf ? formatTime(dataAsOf) : '—')}
        </span>
      ) : null}
      <div className={updating ? 'opacity-60 transition-opacity' : 'transition-opacity'}>{children}</div>
    </div>
  );
}
