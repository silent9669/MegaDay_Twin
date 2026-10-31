import { evidenceText } from '../../lib/evidence-text';
// Pure helpers for the Compare screen (09 §2.3). No React here so they are easy to test.
import { ApiError } from '../../api/client';
import type {
  ActionSpec,
  CampaignDetail,
  GoalBrief,
  FlipCondition,
  Recommendation,
  ScenarioResult,
  ScenarioRun,
  SimRequest,
  Source,
  ZoneBar,
} from '../../api/types';
import { computeZoneBar } from '../../lib/breakeven';
import { pauseBlockedNote } from '../../lib/zoneBarText';
import { formatPct, formatVndMillions } from '../../lib/format';
import { pctOf, viText } from '../../lib/i18n';
import type { Citation } from '../../components/CitationChip';

const t = viText.compare;

/** Wireframe order: Tiếp tục, Pause, Giảm %, Chỉ khung giờ, Pause tới khi hàng về. */
const KIND_ORDER: ActionSpec['kind'][] = ['continue', 'pause', 'reduce_budget', 'schedule_hours', 'pause_until_restock'];

export function actionKey(a: Pick<ActionSpec, 'kind' | 'pct' | 'windows_local'>): string {
  const pct = a.pct != null ? pctOf(a.pct) : '';
  const win = a.windows_local?.map(([s, e]) => `${s}-${e}`).join(',') ?? '';
  return `${a.kind}|${pct}|${win}`;
}

export function sameAction(a: ActionSpec | null | undefined, b: ActionSpec | null | undefined): boolean {
  if (!a || !b) return false;
  return actionKey(a) === actionKey(b);
}

export function orderResults(results: ScenarioResult[]): ScenarioResult[] {
  return results
    .map((r, i) => ({ r, i }))
    .sort((x, y) => KIND_ORDER.indexOf(x.r.action.kind) - KIND_ORDER.indexOf(y.r.action.kind) || x.i - y.i)
    .map(({ r }) => r);
}

export function violationLabel(code: string): string {
  return t.violation[code] ?? t.violationFallback(code);
}

/** Expected objective: mean when the API sends it, otherwise p50. */
export function expectedObjective(r: ScenarioResult): number {
  return r.objective.mean ?? r.objective.p50;
}

function lower(a: ActionSpec): string {
  return t.lowerCase(viText.action(a));
}

/** Tier-1 sentence readable in 3 seconds. null for none_meets_goal (tier 1 is replaced). */
export function headline(rec: Recommendation): string | null {
  if (rec.status === 'none_meets_goal' || !rec.best) return null;
  const best = viText.action(rec.best);
  if (rec.runner_up_meets_goal === false && rec.runner_up && rec.goal_cost_vnd != null) {
    return t.headline.onlyMeetsGoal(best, lower(rec.runner_up), formatVndMillions(rec.goal_cost_vnd, 'long'));
  }
  if (rec.status === 'undetermined' && rec.runner_up) {
    const prob = rec.p_best_beats_runner_up != null ? formatPct(rec.p_best_beats_runner_up) : '—';
    return t.headline.undetermined(lower(rec.best), lower(rec.runner_up), prob);
  }
  if (rec.runner_up && rec.expected_gain_vnd != null) {
    return t.headline.recommend(best, lower(rec.runner_up), formatVndMillions(rec.expected_gain_vnd, 'long'));
  }
  return t.headline.recommendNoRunner(best);
}

/** 05 §7: best is schedule_hours / reduce_budget → "Phương án tốt hơn cả hai: …". */
export function betterThanBothLine(rec: Recommendation): string | null {
  if (!rec.best || rec.status === 'none_meets_goal') return null;
  if (rec.best.kind !== 'schedule_hours' && rec.best.kind !== 'reduce_budget') return null;
  return t.betterThanBoth(viText.action(rec.best));
}

/** Pause wins on the bar but is blocked by the flash-sale commitment. */
export function pauseBlockedLine(bar: ZoneBar | null, results: ScenarioResult[], holdBetter = false): string | null {
  // The API's verdict_note already says it under the bar.
  if (bar?.verdict_note) return null;
  const pauseWins = bar ? bar.verdict === 'pause_better' : holdBetter;
  if (!pauseWins) return null;
  const pause = results.find((r) => r.action.kind === 'pause');
  return pause && !pause.feasible && pause.violations.includes('campaign_must_run') ? t.pauseBlocked : null;
}

// ---------------------------------------------------------------------------
// What-if sliders

export type WhatIf = {
  /** Leftover value V (đ/sp); null = untouched (server default). */
  leftoverValue: number | null;
  /** restock_delay_days; 0 = on schedule, negative = earlier. */
  restockDelayDays: number;
};

export const BASE_WHAT_IF: WhatIf = { leftoverValue: null, restockDelayDays: 0 };

export function isWhatIfActive(w: WhatIf): boolean {
  return w.leftoverValue !== null || w.restockDelayDays !== 0;
}

export function whatIfKey(w: WhatIf): string {
  return `${w.leftoverValue ?? ''}|${w.restockDelayDays}`;
}

/** Parameter keys from the 05 §3 catalogue. */
export function paramOverrides(w: WhatIf): Record<string, number> {
  const out: Record<string, number> = {};
  if (w.leftoverValue !== null) out.leftover_value_per_unit = w.leftoverValue;
  if (w.restockDelayDays !== 0) out.restock_delay_days = w.restockDelayDays;
  return out;
}

export const DEFAULT_SEED = 20261111;

/**
 * SimRequest for a slider re-run. Reuses the run's own `request` (M3-api §2: re-sending
 * it unchanged hits the cache); without it, lets the backend fill `start`/`horizon_hours`.
 */
export function buildSimRequest(
  run: Pick<ScenarioRun, 'request'> | undefined,
  rec: Recommendation,
  w: WhatIf,
  samples: number,
): SimRequest {
  const base: SimRequest = run?.request
    ? { ...run.request }
    : { campaign_id: rec.campaign_id, goal_id: rec.inputs_ref.goal_id, samples, seed: DEFAULT_SEED };
  // The echo is exactly the request that ran (M3-api "Sửa lỗi nhỏ M3"); the sliders win.
  return {
    ...base,
    actions: base.actions ?? null,
    param_overrides: { ...base.param_overrides, ...paramOverrides(w) },
    samples,
    seed: base.seed ?? DEFAULT_SEED,
  };
}

/** Restock slider range: the API accepts −7..7 days (negative = earlier, M3-api §2). */
export const RESTOCK_MIN = -7;
export const RESTOCK_MAX = 7;

/** Max of the leftover-value slider: V ≤ selling price (05 §3) = `price_max_vnd` of the campaign. */
export function leftoverMax(campaign: Pick<CampaignDetail, 'price_max_vnd'> | undefined, rec: Recommendation, fixtures: boolean): number {
  const inputs = rec.zone_bar?.inputs;
  if (fixtures && inputs) return inputs.contribution_per_unit_vnd;
  return campaign?.price_max_vnd ?? inputs?.contribution_per_unit_vnd ?? 1_000_000;
}

/**
 * Min of the slider: −(unit cost) since M3 (hàng dư mất trắng, parameter catalogue min_rule);
 * 0 when costs are unknown or in fixture mode.
 */
export function leftoverMin(campaign: Pick<CampaignDetail, 'sku_detail'> | undefined, fixtures: boolean): number {
  if (fixtures || !campaign) return 0;
  const costs = campaign.sku_detail.filter((s) => !s.is_bundle).map((s) => s.unit_cost_vnd ?? 0);
  return costs.length ? -Math.max(...costs) : 0;
}

export function clampLeftover(v: number, max: number, min = 0): number {
  return Math.min(Math.max(min, v), max);
}

export type LocalBar = { bar: ZoneBar | null; estimated: boolean };

/**
 * Instant browser estimate of the bar for the current leftover value (09 §2.3:
 * < 100 ms), using the closed form when the API supplies `zone_bar.inputs`.
 */
export function localZoneBar(rec: Recommendation, w: WhatIf): LocalBar {
  const base = rec.zone_bar;
  const inputs = base?.inputs;
  if (w.leftoverValue === null || !inputs) return { bar: base, estimated: false };
  const baselineV = inputs.leftover_value_per_unit_vnd;
  if (w.leftoverValue === baselineV) return { bar: base, estimated: false };
  const alts = [...new Set([baselineV, 20_000])];
  const res = computeZoneBar({ ...inputs, leftover_value_per_unit_vnd: w.leftoverValue }, alts);
  let bar = res.zone_bar;
  if (bar && base.pause_feasible === false) {
    const violations = base.pause_violations ?? [];
    bar = { ...bar, pause_feasible: false, pause_violations: violations, verdict_note: pauseBlockedNote(bar.verdict, violations) };
  }
  return { bar, estimated: true };
}

function crossed(value: number, baseline: number, flipAt: number): boolean {
  return flipAt >= baseline ? value >= flipAt : value <= flipAt;
}

/** Flip conditions (05 §8) that the current what-if values have reached. */
export function flipHits(rec: Recommendation, w: WhatIf): FlipCondition[] {
  const baselineV = rec.zone_bar?.inputs?.leftover_value_per_unit_vnd ?? 0;
  return rec.flip_conditions.filter((fc) => {
    if (fc.factor === 'leftover_value_per_unit') return w.leftoverValue !== null && crossed(w.leftoverValue, baselineV, fc.flip_at);
    if (fc.factor === 'restock_delay_days') return w.restockDelayDays !== 0 && crossed(w.restockDelayDays, 0, fc.flip_at);
    return false;
  });
}

// ---------------------------------------------------------------------------
// Citations

/**
 * Source.href is an API path (`/api/v1/skus/X/forecast`); map it to where the source is
 * shown in the app: SKU detail, or tier 3 of this screen for scenarios, iROAS, commitments.
 */
export function webHref(href: string | null | undefined): string | null {
  if (!href) return null;
  if (href.startsWith('#')) return href;
  const m = /^\/api\/v1(\/.*)$/.exec(href);
  if (!m) return href.startsWith('/') ? href : null;
  const path = m[1] ?? '';
  const sku = /^\/skus\/([^/?#]+)/.exec(path);
  if (sku) return `/sku/${sku[1]}`;
  if (/^\/(scenarios|campaigns|recommendations)\//.test(path)) return '#tier-3';
  return null;
}

export function citationFromSource(src: Source, fallbackComputedAt?: string | null): Citation {
  return {
    label: src.label,
    kind: src.kind,
    id: src.id,
    tool: src.tool ?? null,
    dataAsOf: src.data_as_of ?? null,
    computedAt: fallbackComputedAt ?? null,
    href: webHref(src.href),
  };
}

/** Source for reason i, falling back to the scenario run behind the recommendation. */
export function reasonCitation(rec: Recommendation, i: number, computedAt?: string | null): Citation {
  const src = rec.reason_sources?.[i];
  if (src) return citationFromSource(src, computedAt);
  const id = rec.scenario_id ?? rec.recommendation_id;
  return {
    label: `${viText.citation.kind.scenario} ${id}`,
    kind: 'scenario',
    id,
    dataAsOf: rec.inputs_ref.data_as_of,
    computedAt: computedAt ?? null,
    href: '#tier-3',
  };
}

/**
 * Splits a reason around its key number so the number itself opens the source.
 * Prefers a clock time or a number with a unit ("13:00", "9.280đ", "18–31 sp/giờ")
 * over a bare one, so "giá 11.11" is not mistaken for the figure.
 */
export function splitFirstNumber(text: string): { before: string; number: string; after: string } | null {
  const m =
    /\d{1,2}:\d{2}(?:–\d{1,2}:\d{2})?|\d[\d.,]*(?:–\d[\d.,]*)?(?: VND\/unit| VND| units\/hour| units|đ\/sp|đ| ?sp\/giờ| ?sp|%| lần)/.exec(text) ?? /\d[\d.,]*/.exec(text);
  if (!m) return null;
  return { before: text.slice(0, m.index), number: m[0], after: text.slice(m.index + m[0].length) };
}

// ---------------------------------------------------------------------------
// No zone bar, goals, blocked runs

const HOLD_PREFIX = 'Giữ hàng lợi hơn bán';

/** The "keep stock beats selling" reason (05 §7.1, m ≤ V), if the simulator gave it. */
export function holdBetterReason(rec: Pick<Recommendation, 'reasons'>): string | null {
  return rec.reasons.find((r) => r.startsWith(HOLD_PREFIX)) ?? null;
}

/** Sentence shown instead of the ZoneBar when `zone_bar` is null, by cause. */
export function noZoneBarText(rec: Recommendation): string {
  const hold = holdBetterReason(rec);
  if (hold) return t.noZoneBarHold(hold.slice(HOLD_PREFIX.length).replace(/^[:\s]+/, ''));
  if (!rec.best) return t.noZoneBarBlocked;
  return t.noZoneBar;
}

/** "Lợi nhuận tới 00:00 12/11" (label from the API, deadline included); falls back to the id. */
export function goalText(goal: GoalBrief | null | undefined, goalId: string): string {
  if (!goal || goal.goal_id !== goalId) return goalId;
  return evidenceText(goal.label);
}

/** Default deadline for a new goal: next local midnight after the data clock. */
export function defaultDeadline(dataAsOf: string | null | undefined): string {
  const base = dataAsOf ? new Date(dataAsOf) : new Date();
  const t0 = Number.isNaN(base.getTime()) ? Date.now() : base.getTime();
  // Asia/Ho_Chi_Minh has no DST: shift to local, cut to the day, add one day, shift back.
  const local = new Date(t0 + 7 * 3_600_000);
  local.setUTCHours(0, 0, 0, 0);
  const next = local.getTime() + 24 * 3_600_000 - 7 * 3_600_000;
  return new Date(next).toISOString().replace('.000Z', 'Z');
}

/** `datetime-local` value (VN time) ↔ ISO with +07:00. */
export function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const local = new Date(d.getTime() + 7 * 3_600_000).toISOString();
  return local.slice(0, 16);
}
export function fromLocalInput(value: string): string {
  return `${value}:00+07:00`;
}

export type Blocking = { kind: 'goal'; message: string } | { kind: 'data'; message: string; reasons: string[] };

/** 422 BLOCKING_DATA_ISSUE from POST /scenarios/run (M3-api Q7): missing goal or missing data. */
export function blockingInfo(err: unknown): Blocking | null {
  if (!(err instanceof ApiError) || err.code !== 'BLOCKING_DATA_ISSUE') return null;
  const d = err.details ?? {};
  if (d.missing === 'goal') return { kind: 'goal', message: err.message };
  const reasons = Array.isArray(d.reasons) ? d.reasons.filter((r): r is string => typeof r === 'string') : [];
  return { kind: 'data', message: err.message, reasons };
}

/** 400 PARAM_OUT_OF_RANGE: the API's max (selling price) so the slider can clamp. */
export function outOfRangeMax(err: unknown): number | null {
  if (!(err instanceof ApiError) || err.code !== 'PARAM_OUT_OF_RANGE') return null;
  const max = err.details?.max;
  return typeof max === 'number' ? max : null;
}

/** param_version {key: version} → "leftover_value_per_unit v0 · …"; non-zero first. */
export function paramVersionText(pv: Record<string, number> | null | undefined): string {
  const entries = Object.entries(pv ?? {});
  if (!entries.length) return '—';
  const changed = entries.filter(([, v]) => v !== 0);
  if (!changed.length) return t.lineage.allDefault(entries.length);
  return changed.map(([k, v]) => (v === -1 ? `${k}: ${t.lineage.override}` : `${k} v${v}`)).join(' · ');
}
