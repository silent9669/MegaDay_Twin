// Text and scale helpers for ZoneBar (kept out of the component file for fast refresh).
import type { ZoneBar as ZoneBarData } from '../api/types';
import { formatNumber } from './format';
import { viText } from './i18n';

const t = viText.zoneBar;

/** Same text as backend `recommend.mark_pause_blocked`, for bars redrawn in the browser. */
export function pauseBlockedNote(verdict: ZoneBarData['verdict'], violations: readonly string[]): string {
  const why = violations.includes('campaign_must_run') ? t.blockedWhyCommitment : t.blockedWhyOther;
  return verdict === 'pause_better' ? t.blockedPauseBetter(why) : t.blockedCompareOnly(why);
}

function fmt(v: number): string {
  return formatNumber(v, 1);
}

/** Verdict sentence; when pause is not allowed the API's `verdict_note` replaces it (05 §7.2). */
export function verdictSentence(bar: Pick<ZoneBarData, 'verdict' | 'threshold'> & Partial<Pick<ZoneBarData, 'pause_feasible' | 'verdict_note'>>): string {
  if (bar.pause_feasible === false && bar.verdict_note) return bar.verdict_note.endsWith('.') ? bar.verdict_note : `${bar.verdict_note}.`;
  switch (bar.verdict) {
    case 'undetermined':
      return t.verdict.undetermined(fmt(bar.threshold));
    case 'pause_better':
      return t.verdict.pause_better;
    case 'continue_better':
      return t.verdict.continue_better;
  }
}

/** Alternative thresholds that can be drawn (the API sends threshold = null when the line does not exist). */
export function drawableAlts(bar: Pick<ZoneBarData, 'alt_thresholds'>): { label: string; threshold: number }[] {
  return bar.alt_thresholds.flatMap((a) => (a.threshold == null ? [] : [{ label: a.leftover_value_per_unit!=null?t.altLabel(a.leftover_value_per_unit):a.label, threshold: a.threshold }]));
}

export function zoneBarAriaLabel(bar: ZoneBarData): string {
  const [p10, p50, p90] = bar.interval;
  const parts = [verdictSentence(bar), t.summary(fmt(p10), fmt(p50), fmt(p90), fmt(bar.threshold))];
  for (const alt of drawableAlts(bar)) parts.push(t.altSummary(alt.label, fmt(alt.threshold)));
  return parts.join(' ');
}

export function niceStep(span: number): number {
  const raw = span / 5;
  const mag = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 2.5, 5, 10]) {
    if (m * mag >= raw) return m * mag;
  }
  return 10 * mag;
}

export function defaultDomain(bar: ZoneBarData): [number, number] {
  const max = Math.max(bar.interval[2], bar.threshold, ...drawableAlts(bar).map((a) => a.threshold));
  return [0, max > 0 ? max * 1.4 : 1];
}

