import type { BreakevenRequest } from '../api/types';
import {
  breakevenThreshold,
  breakevenUnits,
  computeZoneBar,
  continueProfit,
  flipLeftoverValue,
  zoneVerdict,
} from './breakeven';

// 05 §7.1 "ví dụ trang 10", identical to the POST /scenarios/breakeven example in 08 §3.8.
const page10: BreakevenRequest = {
  units_left: 120,
  hours_left: 4,
  contribution_per_unit_vnd: 45_000,
  spend_per_hour_vnd: 450_000,
  rate_continue_per_hour: 40,
  pause_rate_interval: [18, 24, 31],
  leftover_value_per_unit_vnd: 0,
};

describe('breakeven (05 §7, page-10 example)', () => {
  it('Π_c = 120 × 45.000 − 3 × 450.000 = 4.050.000đ', () => {
    expect(continueProfit(page10)).toBe(4_050_000);
  });

  it('V = 0 → q* = 90 sp, x* = 22,5 sp/giờ', () => {
    expect(breakevenUnits(4_050_000, 120, 45_000, 0)).toBe(90);
    expect(breakevenThreshold(page10)).toEqual({ kind: 'threshold', units: 90, ratePerHour: 22.5 });
  });

  it('V = 20.000 → q* = 66 sp, x* = 16,5 sp/giờ', () => {
    expect(breakevenThreshold(page10, 20_000)).toEqual({ kind: 'threshold', units: 66, ratePerHour: 16.5 });
  });

  it('p10 = 18 sp/giờ → V* = 16.875đ', () => {
    expect(flipLeftoverValue(page10, 18)).toBe(16_875);
  });

  it('builds the same ZoneBar as the 08 §3.8 response', () => {
    const res = computeZoneBar(page10);
    expect(res.flip_leftover_value_vnd).toBe(16_875);
    expect(res.zone_bar).toMatchObject({
      axis_label: 'Sales per hour if ads pause (units)',
      threshold: 22.5,
      interval: [18, 24, 31],
      verdict: 'undetermined',
      alt_thresholds: [{ label: 'leftover value 20,000 VND', threshold: 16.5, leftover_value_per_unit: 20_000, status: 'ok' }],
    });
  });

  it('V = 20.000 flips the bar to pause_better (09 §4 second example)', () => {
    const res = computeZoneBar({ ...page10, leftover_value_per_unit_vnd: 20_000 }, [0]);
    expect(res.zone_bar?.threshold).toBe(16.5);
    expect(res.zone_bar?.verdict).toBe('pause_better');
    expect(res.zone_bar?.alt_thresholds).toEqual([{ label: 'leftover value 0 VND', threshold: 22.5, leftover_value_per_unit: 0, status: 'ok' }]);
  });
});

describe('breakeven edge cases (05 §7.1, §11)', () => {
  it('m ≤ V → no bar (keeping stock beats selling)', () => {
    expect(breakevenUnits(4_050_000, 120, 45_000, 45_000)).toBeNull();
    expect(breakevenThreshold(page10, 50_000)).toEqual({ kind: 'no_bar' });
    expect(computeZoneBar({ ...page10, leftover_value_per_unit_vnd: 45_000 }).zone_bar).toBeNull();
  });

  it('q* ≤ 0 → pause better for sure', () => {
    // Ads spend eats all contribution: Π_c < 0.
    const losing = { ...page10, spend_per_hour_vnd: 2_000_000 };
    expect(breakevenThreshold(losing)).toMatchObject({ kind: 'pause_always', ratePerHour: 0 });
    expect(computeZoneBar(losing).zone_bar?.verdict).toBe('pause_better');
  });

  it('q* > n → continue better for sure, threshold capped at n/H', () => {
    // With spend ≥ 0 and m > V, q* ≤ n always; q* > n needs Π_c > n·m, e.g. a
    // platform ads subsidy (negative net spend). The guard must still hold.
    const cheapAds = { ...page10, spend_per_hour_vnd: 0, rate_continue_per_hour: 40, units_left: 120 };
    // Π_c = 120·45.000 = 5.400.000 → q* = 120 = n exactly: still a threshold.
    expect(breakevenThreshold(cheapAds)).toEqual({ kind: 'threshold', units: 120, ratePerHour: 30 });
    const negativeSpend = { ...cheapAds, spend_per_hour_vnd: -10_000 };
    expect(breakevenThreshold(negativeSpend)).toMatchObject({ kind: 'continue_always', ratePerHour: 30 });
    expect(computeZoneBar(negativeSpend).zone_bar?.verdict).toBe('continue_better');
  });

  it('verdict follows p10 / p90 against x*', () => {
    expect(zoneVerdict([18, 24, 31], 22.5)).toBe('undetermined');
    expect(zoneVerdict([18, 24, 31], 16.5)).toBe('pause_better');
    expect(zoneVerdict([1.8, 2.4, 3.1], 3.49)).toBe('continue_better');
  });

  it('flip value is null when no V in [0, m) flips the verdict', () => {
    expect(flipLeftoverValue(page10, 40)).toBeNull();
  });

  it('accounts for continue leftover in Π_c(V) (normal-day fixture)', () => {
    const normalDay: BreakevenRequest = {
      units_left: 60,
      hours_left: 12,
      contribution_per_unit_vnd: 149_200,
      spend_per_hour_vnd: 150_000,
      rate_continue_per_hour: 4.5,
      pause_rate_interval: [1.8, 2.4, 3.1],
      leftover_value_per_unit_vnd: 0,
    };
    expect(continueProfit(normalDay)).toBe(6_256_800);
    expect(continueProfit(normalDay, 20_000)).toBe(6_376_800);
    const bar = computeZoneBar(normalDay).zone_bar;
    expect(bar?.verdict).toBe('continue_better');
    expect(bar?.threshold).toBeCloseTo(3.4946, 3);
    expect(bar?.alt_thresholds[0]?.threshold).toBeCloseTo(3.339, 3);
  });
});
