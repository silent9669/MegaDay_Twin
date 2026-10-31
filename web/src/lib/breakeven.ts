// Closed-form break-even between `continue` and `pause` (05 §7.1, ECO-04).
// Same formula as backend `economics/breakeven.py` and POST /scenarios/breakeven:
//
//   Π_p(q) = m·q + V·(n − q) − spend_p
//   Π_p(q*) = Π_c  ⇒  q* = (Π_c + spend_p − n·V) / (m − V),   x* = q* / H
//
// m ≤ V → holding stock beats selling, pause always wins, no bar (null).
// q* ≤ 0 → pause wins for sure; q* > n → continue wins for sure.
// Used by the Compare screen to redraw the ZoneBar instantly while a slider moves
// (09 §2.3, < 100 ms); the server result replaces it when it arrives.
import type { AltThreshold, BreakevenRequest, BreakevenResponse, ZoneBar, ZoneVerdict } from '../api/types';

/** Part of the POST /scenarios/breakeven response the browser reproduces. */
export type LocalBreakeven = Pick<BreakevenResponse, 'zone_bar' | 'flip_leftover_value_vnd'>;

const ALT_STATUS = { threshold: 'ok', pause_always: 'pause_sure', continue_always: 'continue_sure' } as const;
import { viText } from './i18n';

export type ContinueOutcome = {
  /** Units sold by `continue` within the horizon (capped by stock). */
  unitsSold: number;
  /** Hours `continue` keeps selling (and spending) before stock runs out. */
  hoursSelling: number;
  spendVnd: number;
  /** Units left at the end of the horizon under `continue`. */
  leftoverUnits: number;
  /** Π_c without leftover value: m·sold − spend. */
  profitExLeftoverVnd: number;
};

/** `continue` sells evenly at `rate_continue_per_hour` and spends `spend_per_hour_vnd` until stock or time runs out. */
export function continueOutcome(req: BreakevenRequest): ContinueOutcome {
  const n = req.units_left;
  const rate = req.rate_continue_per_hour;
  const unitsSold = Math.min(n, rate * req.hours_left);
  const hoursSelling = rate > 0 ? Math.min(req.hours_left, n / rate) : req.hours_left;
  const spendVnd = req.spend_per_hour_vnd * hoursSelling;
  return {
    unitsSold,
    hoursSelling,
    spendVnd,
    leftoverUnits: n - unitsSold,
    profitExLeftoverVnd: unitsSold * req.contribution_per_unit_vnd - spendVnd,
  };
}

/** Π_c at leftover value V (đ/sp). */
export function continueProfit(req: BreakevenRequest, leftoverValue = req.leftover_value_per_unit_vnd): number {
  const c = continueOutcome(req);
  return c.profitExLeftoverVnd + leftoverValue * c.leftoverUnits;
}

/** q* = (Π_c + spend_p − n·V) / (m − V); null when m ≤ V (pause always better). */
export function breakevenUnits(
  continueProfitVnd: number,
  unitsLeft: number,
  contributionPerUnit: number,
  leftoverValue: number,
  pauseSpendVnd = 0,
): number | null {
  const margin = contributionPerUnit - leftoverValue;
  if (margin <= 0) return null;
  return (continueProfitVnd + pauseSpendVnd - unitsLeft * leftoverValue) / margin;
}

export type BreakevenThreshold =
  | { kind: 'threshold'; units: number; ratePerHour: number }
  | { kind: 'pause_always'; units: number; ratePerHour: 0 }
  | { kind: 'continue_always'; units: number; ratePerHour: number }
  | { kind: 'no_bar' };

/** x* for one leftover value V, with the edge cases of 05 §7.1 spelled out. */
export function breakevenThreshold(req: BreakevenRequest, leftoverValue = req.leftover_value_per_unit_vnd): BreakevenThreshold {
  const n = req.units_left;
  const H = req.hours_left;
  const q = breakevenUnits(continueProfit(req, leftoverValue), n, req.contribution_per_unit_vnd, leftoverValue);
  if (q === null) return { kind: 'no_bar' };
  if (q <= 0) return { kind: 'pause_always', units: q, ratePerHour: 0 };
  // Pause cannot sell more than n, so a threshold above n/H is unreachable.
  if (q > n) return { kind: 'continue_always', units: q, ratePerHour: n / H };
  return { kind: 'threshold', units: q, ratePerHour: q / H };
}

/** 05 §7.2: p10 > x* → pause_better; p90 < x* → continue_better; otherwise undetermined. */
export function zoneVerdict(interval: readonly [number, number, number], threshold: number): ZoneVerdict {
  const [p10, , p90] = interval;
  if (p10 > threshold) return 'pause_better';
  if (p90 < threshold) return 'continue_better';
  return 'undetermined';
}

/**
 * Leftover value V* at which x*(V) equals a pause sales rate (sp/giờ), i.e. where
 * the verdict flips (05 §8). Raising V lowers x*, so with rate = p10 this is the V
 * from which pause is better across the whole forecast range.
 * Π_c(V) = P0 + V·L_c  ⇒  V* = (P0 + spend_p − m·q) / (n − q − L_c),  q = rate·H.
 * Returns null when no V in [0, m) flips it.
 */
export function flipLeftoverValue(req: BreakevenRequest, pauseRatePerHour: number, pauseSpendVnd = 0): number | null {
  const c = continueOutcome(req);
  const q = pauseRatePerHour * req.hours_left;
  const denom = req.units_left - q - c.leftoverUnits;
  if (denom <= 0) return null;
  const v = (c.profitExLeftoverVnd + pauseSpendVnd - req.contribution_per_unit_vnd * q) / denom;
  if (!Number.isFinite(v) || v < 0 || v >= req.contribution_per_unit_vnd) return null;
  return v;
}

/**
 * Same output as POST /scenarios/breakeven: a ZoneBar (null when m ≤ V) plus V*
 * computed at p10. `altLeftoverValues` adds dashed thresholds (default 20.000đ).
 */
export function computeZoneBar(req: BreakevenRequest, altLeftoverValues: readonly number[] = [20_000]): LocalBreakeven {
  const V = req.leftover_value_per_unit_vnd;
  const main = breakevenThreshold(req, V);
  const interval = req.pause_rate_interval;
  const flip = flipLeftoverValue(req, interval[0]);
  if (main.kind === 'no_bar') return { zone_bar: null, flip_leftover_value_vnd: flip };

  const threshold = main.ratePerHour;
  let verdict: ZoneVerdict;
  if (main.kind === 'pause_always') verdict = 'pause_better';
  else if (main.kind === 'continue_always') verdict = 'continue_better';
  else verdict = zoneVerdict(interval, threshold);

  const alt_thresholds: AltThreshold[] = [];
  for (const altV of altLeftoverValues) {
    if (altV === V) continue;
    const alt = breakevenThreshold(req, altV);
    if (alt.kind === 'no_bar') continue;
    alt_thresholds.push({
      label: viText.zoneBar.altLabel(altV),
      threshold: alt.ratePerHour,
      leftover_value_per_unit: altV,
      status: ALT_STATUS[alt.kind],
    });
  }

  const zone_bar: ZoneBar = {
    axis_label: viText.zoneBar.axisLabel,
    threshold,
    interval: [interval[0], interval[1], interval[2]],
    verdict,
    alt_thresholds,
    inputs: req,
  };
  return { zone_bar, flip_leftover_value_vnd: flip };
}
