// Building blocks of the 3-tier Compare screen (09 §2.3).
import { ChevronDown, ChevronRight, CircleSlash, RotateCcw, Star } from 'lucide-react';
import type { ReactNode } from 'react';
import type { CampaignDetail, Commitment, Meta, Recommendation, ScenarioResult, ScenarioRun, ZoneBar as ZoneBarData } from '../../api/types';
import { CitationChip } from '../../components/CitationChip';
import { MoneyVND, Range } from '../../components/numbers';
import { ZoneBar } from '../../components/ZoneBar';
import { formatDateTime, formatNumber, formatTime, formatUnits, formatVnd, formatVndMillions } from '../../lib/format';
import { viText } from '../../lib/i18n';
import {
  actionKey,
  citationFromSource,
  expectedObjective,
  orderResults,
  paramVersionText,
  reasonCitation,
  RESTOCK_MAX,
  RESTOCK_MIN,
  sameAction,
  splitFirstNumber,
  violationLabel,
  type WhatIf,
} from './compareLogic';

const t = viText.compare;
const millions = (v: number) => formatVndMillions(v);

// ---------------------------------------------------------------------------

export function TierSection({
  id,
  title,
  open,
  onToggle,
  children,
  collapsible = true,
}: {
  id: string;
  title: string;
  open: boolean;
  onToggle?: (next: boolean) => void;
  children: ReactNode;
  collapsible?: boolean;
}) {
  const headingId = `${id}-heading`;
  return (
    <section id={id} aria-labelledby={headingId} className="card scroll-mt-4 p-4">
      <h2 id={headingId} className="font-semibold">
        {collapsible ? (
          <button
            type="button"
            aria-expanded={open}
            aria-controls={`${id}-body`}
            onClick={() => onToggle?.(!open)}
            className="-m-1 inline-flex items-center gap-1.5 rounded p-1 hover:bg-surface-2"
          >
            {open ? <ChevronDown aria-hidden className="size-4" /> : <ChevronRight aria-hidden className="size-4" />}
            {title}
          </button>
        ) : (
          title
        )}
      </h2>
      {open ? (
        <div id={`${id}-body`} className="mt-3">
          {children}
        </div>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Tier 1

function ActionCard({ result, isBest }: { result: ScenarioResult; isBest: boolean }) {
  const name = viText.action(result.action);
  if (!result.feasible) {
    return (
      <li className="rounded-lg border border-dashed border-border bg-surface-2 p-3 text-muted">
        <p className="font-medium">{name}</p>
        <p className="mt-1 inline-flex items-center gap-1 text-sm font-medium">
          <CircleSlash aria-hidden className="size-3.5" />
          {t.infeasible}
        </p>
        <ul className="mt-0.5 text-sm">
          {result.violations.map((v) => (
            <li key={v}>{violationLabel(v)}</li>
          ))}
        </ul>
        {result.violation_details?.length ? (
          <ul className="mt-0.5 text-xs">
            {result.violation_details.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        ) : null}
        {result.objective ? (
          <p className="mt-1 text-xs tabular-nums">{t.infeasibleIfAllowed(formatVndMillions(expectedObjective(result)))}</p>
        ) : null}
      </li>
    );
  }
  return (
    <li className={`rounded-lg border p-3 ${isBest ? 'border-accent bg-accent-soft' : 'border-border bg-surface'}`}>
      <p className="flex items-start justify-between gap-2 font-medium">
        <span>{name}</span>
        {isBest ? (
          <span className="inline-flex shrink-0 items-center gap-0.5 text-xs font-semibold text-accent">
            <Star aria-hidden className="size-3.5 fill-current" />
            {t.bestMark}
          </span>
        ) : null}
      </p>
      <p className="mt-1 text-lg font-semibold">
        <MoneyVND value={expectedObjective(result)} style="short" />
      </p>
      <Range q={result.objective} format={millions} className="text-xs" />
      <p className="mt-1 text-xs text-muted tabular-nums">
        {result.stockout_hour_p50 ? t.stockoutAt(formatTime(result.stockout_hour_p50)) : t.noStockout}
      </p>
      {result.duplicate_of ? <p className="mt-0.5 text-xs text-muted">{t.duplicateOf(result.duplicate_of)}</p> : null}
    </li>
  );
}

export function ActionCards({ results, rec }: { results: ScenarioResult[]; rec: Recommendation }) {
  return (
    <ul aria-label={t.actionCards} className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
      {orderResults(results).map((r) => (
        <ActionCard key={actionKey(r.action)} result={r} isBest={sameAction(r.action, rec.best)} />
      ))}
    </ul>
  );
}

export function ZoneBarBlock({
  bar,
  estimated,
  noBarText,
  extraLines,
}: {
  bar: ZoneBarData | null;
  estimated: boolean;
  noBarText: string;
  extraLines: (string | null)[];
}) {
  return (
    <div className="rounded-lg border border-border p-3">
      {bar ? (
        <ZoneBar bar={bar} estimated={estimated} />
      ) : (
        <p className="text-muted">{estimated ? viText.zoneBar.noBar : noBarText}</p>
      )}
      {extraLines.filter(Boolean).map((line) => (
        <p key={line} className="mt-1 font-medium">
          {line}
        </p>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tier 2

function ReasonItem({ text, rec, index, computedAt }: { text: string; rec: Recommendation; index: number; computedAt?: string }) {
  const citation = reasonCitation(rec, index, computedAt);
  const parts = splitFirstNumber(text);
  if (!parts) {
    return (
      <li>
        {text} <CitationChip citation={citation} />
      </li>
    );
  }
  return (
    <li>
      {parts.before}
      <CitationChip citation={citation}>{parts.number}</CitationChip>
      {parts.after}
    </li>
  );
}

export function ReasonList({ rec, computedAt }: { rec: Recommendation; computedAt?: string }) {
  return (
    <div>
      <h3 className="mb-1 text-sm font-medium text-muted">{t.reasonsTitle}</h3>
      <ul className="list-disc space-y-1.5 pl-5">
        {rec.reasons.map((r, i) => (
          <ReasonItem key={r} text={r} rec={rec} index={i} computedAt={computedAt} />
        ))}
      </ul>
    </div>
  );
}

export function FlipList({ rec }: { rec: Recommendation }) {
  return (
    <div>
      <h3 className="mb-1 text-sm font-medium text-muted">{t.flipTitle}</h3>
      {rec.flip_conditions.length ? (
        <ul className="list-disc space-y-1 pl-5">
          {rec.flip_conditions.map((fc) => (
            <li key={`${fc.factor}:${fc.flip_at}`}>{fc.text}</li>
          ))}
        </ul>
      ) : (
        <p>{t.noFlip}</p>
      )}
    </div>
  );
}

export function ParamSliders({
  whatIf,
  showLeftover,
  leftoverMax,
  leftoverMin = 0,
  leftoverBase,
  leftoverIsDefault = false,
  leftoverHint = null,
  showRestock,
  onChange,
  onRelease,
  onReset,
  status,
}: {
  whatIf: WhatIf;
  showLeftover: boolean;
  leftoverMax: number;
  leftoverMin?: number;
  leftoverBase: number;
  leftoverIsDefault?: boolean;
  leftoverHint?: string | null;
  showRestock: boolean;
  onChange: (next: WhatIf) => void;
  onRelease: () => void;
  onReset: () => void;
  status: ReactNode;
}) {
  const v = whatIf.leftoverValue ?? leftoverBase;
  const releaseProps = { onPointerUp: onRelease, onKeyUp: onRelease, onBlur: onRelease };
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium text-muted">{t.tryTitle}</h3>
      {showLeftover ? (
        <label className="block">
          <span className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
            <span>{t.sliderLeftover}</span>
            <span className="font-medium tabular-nums">{leftoverIsDefault ? t.sliderLeftoverDefault : `${formatVnd(v)}/sp`}</span>
          </span>
          <input
            type="range"
            min={leftoverMin}
            max={leftoverMax}
            step={leftoverMax > 200_000 ? 1000 : 500}
            value={v}
            onChange={(e) => onChange({ ...whatIf, leftoverValue: Number(e.target.value) })}
            {...releaseProps}
            className="mt-1 w-full accent-[var(--accent)]"
          />
          {leftoverHint ? <span className="text-xs text-muted">{leftoverHint}</span> : null}
        </label>
      ) : null}
      {showRestock ? (
        <label className="block">
          <span className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
            <span>{t.sliderRestock}</span>
            <span className="font-medium tabular-nums">{t.sliderRestockValue(whatIf.restockDelayDays)}</span>
          </span>
          <input
            type="range"
            min={RESTOCK_MIN}
            max={RESTOCK_MAX}
            step={1}
            value={whatIf.restockDelayDays}
            onChange={(e) => onChange({ ...whatIf, restockDelayDays: Number(e.target.value) })}
            {...releaseProps}
            className="mt-1 w-full accent-[var(--accent)]"
          />
        </label>
      ) : null}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <button type="button" className="btn-secondary" onClick={onReset}>
          <RotateCcw aria-hidden className="size-4" />
          {t.resetParams}
        </button>
        <div aria-live="polite" className="min-w-0 flex-1 text-muted">
          {status}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tier 3

function q50(q: ScenarioResult['spend']): number | undefined {
  return q?.p50;
}

export function ResultsTable({ results, rec }: { results: ScenarioResult[]; rec: Recommendation }) {
  const c = t.table;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-sm">
        <caption className="mb-1 text-left text-xs text-muted">{c.caption}</caption>
        <thead className="border-b border-border text-xs text-muted">
          <tr>
            <th scope="col" className="px-2 py-1.5 font-medium">{c.action}</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">{c.expected}</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">{c.range}</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">{c.spend}</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">{c.sold}</th>
            <th scope="col" className="px-2 py-1.5 text-right font-medium">{c.leftover}</th>
            <th scope="col" className="px-2 py-1.5 font-medium">{c.stockout}</th>
            <th scope="col" className="px-2 py-1.5 font-medium">{c.feasible}</th>
          </tr>
        </thead>
        <tbody>
          {orderResults(results).map((r) => {
            const spend = q50(r.spend);
            const sold = q50(r.units_sold);
            const left = q50(r.leftover_units);
            const best = sameAction(r.action, rec.best);
            return (
              <tr key={actionKey(r.action)} className={`border-b border-border last:border-0 ${r.feasible ? '' : 'text-muted'}`}>
                <th scope="row" className="px-2 py-1.5 font-medium">
                  {viText.action(r.action)}
                  {best ? <span className="ml-1 text-xs text-accent">({t.bestMark})</span> : null}
                </th>
                <td className="px-2 py-1.5 text-right">{r.feasible ? formatVnd(expectedObjective(r)) : '—'}</td>
                <td className="px-2 py-1.5 text-right">
                  {r.feasible ? <Range q={r.objective} format={millions} brackets={false} /> : '—'}
                </td>
                <td className="px-2 py-1.5 text-right">{spend != null ? formatVnd(spend) : '—'}</td>
                <td className="px-2 py-1.5 text-right">{sold != null ? formatUnits(sold, 0) : '—'}</td>
                <td className="px-2 py-1.5 text-right">{left != null ? formatUnits(left, 0) : '—'}</td>
                <td className="px-2 py-1.5">{r.stockout_hour_p50 ? formatTime(r.stockout_hour_p50) : '—'}</td>
                <td className="px-2 py-1.5">
                  {r.feasible ? c.yes : r.violations.map(violationLabel).join('; ') || t.infeasible}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function Lineage({
  rec,
  run,
  meta,
  campaign,
}: {
  rec: Recommendation;
  run: ScenarioRun | undefined;
  meta: Meta | undefined;
  campaign?: CampaignDetail;
}) {
  const l = t.lineage;
  const ref = rec.inputs_ref;
  const req = run?.request;
  const rows: [string, ReactNode][] = [
    [l.dataAsOf, formatDateTime(ref.data_as_of)],
    [l.computedAt, meta?.computed_at ? formatDateTime(meta.computed_at) : '—'],
    [l.forecasts, ref.forecast_ids.join(', ')],
    [l.estimate, ref.estimate_id ?? '—'],
    [l.paramVersion, paramVersionText(ref.param_version)],
    [l.goal, ref.goal_id],
    [l.scenario, run?.scenario_id ?? rec.scenario_id ?? '—'],
    [l.samples, req ? `${formatNumber(req.samples, 0)} · ${req.seed}` : '—'],
    [l.horizon, req?.start ? `${formatDateTime(req.start)} · ${formatNumber(req.horizon_hours ?? null, 0)} ${l.hours}` : '—'],
    [l.cache, (meta?.cache_hit ?? run?.cache_hit) == null ? '—' : (meta?.cache_hit ?? run?.cache_hit) ? t.table.yes : t.table.no],
    [l.recommendation, rec.recommendation_id],
  ];
  return (
    <div className="mt-4 grid gap-4 md:grid-cols-2">
      <div>
        <h3 className="mb-1 text-sm font-medium text-muted">{l.title}</h3>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted">{k}</dt>
              <dd className="tabular-nums">{v}</dd>
            </div>
          ))}
        </dl>
      </div>
      {campaign?.commitments.length ? (
        <div>
          <h3 className="mb-1 text-sm font-medium text-muted">{l.commitments}</h3>
          <ul className="list-disc space-y-0.5 pl-5 text-sm">
            {(campaign.commitments as Commitment[]).map((c, i) => (
              <li key={c.id ?? i}>
                {c.text ?? c.kind}
                {c.windows_local?.length ? ` · ${c.windows_local.map(([a, b]) => `${a}–${b}h`).join(', ')}` : ''}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {meta?.sources?.length ? (
        <div>
          <h3 className="mb-1 text-sm font-medium text-muted">{l.sources}</h3>
          <ul className="space-y-1 text-sm">
            {meta.sources.map((s) => (
              <li key={s.id}>
                <CitationChip citation={citationFromSource({ ...s, data_as_of: s.data_as_of ?? meta.data_as_of }, meta.computed_at)} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
