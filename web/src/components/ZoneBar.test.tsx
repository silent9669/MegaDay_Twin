import { render, screen } from '@testing-library/react';
import type { ZoneBar as ZoneBarData } from '../api/types';
import { computeZoneBar } from '../lib/breakeven';
import { ZoneBar } from './ZoneBar';

// 09 §4 "Ví dụ kiểm tra": same forecast 18–31, two thresholds.
const undetermined: ZoneBarData = {
  axis_label: 'Sales per hour if ads pause (units)',
  threshold: 22.5,
  interval: [18, 24, 31],
  verdict: 'undetermined',
  alt_thresholds: [{ label: 'hàng dư 20.000đ', threshold: 16.5, leftover_value_per_unit: 20_000, status: 'ok' }],
};

describe('ZoneBar (09 §4 examples)', () => {
  it('threshold 22,5 with forecast 18–31 → "Undetermined"', () => {
    render(<ZoneBar bar={undetermined} />);
    const img = screen.getByRole('img');
    const label = img.getAttribute('aria-label') ?? '';
    expect(label).toContain('Undetermined: the forecast spans both sides of 22.5 units/hour.');
    expect(label).toContain('18–31 units/hour (median 24)');
    expect(label).toContain('With leftover value 20,000 VND: 16.5 units/hour.');
    expect(screen.getByText('threshold 22.5')).toBeInTheDocument();
    expect(screen.getByText('leftover value 20,000 VND: 16.5')).toBeInTheDocument();
  });

  it('threshold 16,5 (hàng dư 20.000đ) with the same forecast → "Pause is better"', () => {
    const bar = computeZoneBar(
      {
        units_left: 120,
        hours_left: 4,
        contribution_per_unit_vnd: 45_000,
        spend_per_hour_vnd: 450_000,
        rate_continue_per_hour: 40,
        pause_rate_interval: [18, 24, 31],
        leftover_value_per_unit_vnd: 20_000,
      },
      [0],
    ).zone_bar;
    expect(bar).not.toBeNull();
    render(<ZoneBar bar={bar as ZoneBarData} estimated />);
    expect(screen.getByRole('img').getAttribute('aria-label')).toContain('Pause is better: the forecast is above the threshold.');
    expect(screen.getByText('threshold 16.5')).toBeInTheDocument();
    expect(screen.getByText(/Browser break-even estimate/)).toBeInTheDocument();
  });

  it('continue_better reads the third sentence', () => {
    render(<ZoneBar bar={{ ...undetermined, threshold: 3.49, interval: [1.8, 2.4, 3.1], verdict: 'continue_better', alt_thresholds: [] }} />);
    expect(screen.getByText('Continue is better: the forecast is below the threshold.')).toBeInTheDocument();
  });

  it('pause not allowed: verdict_note replaces "Pause is better"', () => {
    render(
      <ZoneBar
        bar={{
          ...undetermined,
          threshold: 0,
          verdict: 'pause_better',
          pause_feasible: false,
          pause_violations: ['campaign_must_run'],
          verdict_note: 'Tạm dừng bị cấm vì đã cam kết với sàn trong khung flash sale. Về tiền, tạm dừng lợi hơn chạy tiếp',
        }}
      />,
    );
    const label = screen.getByRole('img').getAttribute('aria-label') ?? '';
    expect(label).toContain('Pausing is blocked');
    expect(label).not.toContain('Pause tốt hơn: cả khoảng');
    expect(screen.getByRole('note')).toHaveTextContent('Pausing is blocked');
    expect(screen.getByText('Pause (blocked) ▶')).toBeInTheDocument();
    expect(screen.queryByText('Pause tốt hơn ▶')).not.toBeInTheDocument();
  });
});
