import {
  formatDate,
  formatDateTime,
  formatHourWindows,
  formatNumber,
  formatPct,
  formatTime,
  formatUnits,
  formatVnd,
  formatVndMillions,
} from './format';

describe('formatVnd', () => {
  it('uses dot thousands and đ suffix', () => {
    expect(formatVnd(531000)).toBe('531.000đ');
    expect(formatVnd(9280)).toBe('9.280đ');
    expect(formatVnd(0)).toBe('0đ');
  });
  it('rounds and uses a real minus sign', () => {
    expect(formatVnd(-4480000.4)).toBe('−4.480.000đ');
  });
  it('handles non-finite input', () => {
    expect(formatVnd(Number.NaN)).toBe('—');
  });
});

describe('formatVndMillions', () => {
  it('formats millions with comma decimals', () => {
    expect(formatVndMillions(4300000)).toBe('4,3 tr');
    expect(formatVndMillions(10100000, 'long')).toBe('10,1 triệu');
    expect(formatVndMillions(1000000)).toBe('1 tr');
  });
  it('keeps two decimals below one million', () => {
    expect(formatVndMillions(-200000)).toBe('−0,2 tr');
    expect(formatVndMillions(-50000)).toBe('−0,05 tr');
  });
});

describe('formatNumber / formatUnits / formatPct', () => {
  it('formats Vietnamese decimals', () => {
    expect(formatNumber(22.5)).toBe('22,5');
    expect(formatNumber(35880)).toBe('35.880');
    expect(formatNumber(6.54)).toBe('6,5');
  });
  it('appends units', () => {
    expect(formatUnits(41)).toBe('41 sp');
  });
  it('formats ratios as percent', () => {
    expect(formatPct(0.24)).toBe('24%');
    expect(formatPct(0.815, 1)).toBe('81,5%');
  });
});

describe('time in Asia/Ho_Chi_Minh', () => {
  it('keeps +07:00 input as is', () => {
    expect(formatTime('2026-11-11T14:59:00+07:00')).toBe('14:59');
  });
  it('converts UTC input to Vietnam time', () => {
    expect(formatTime('2026-11-11T07:59:00Z')).toBe('14:59');
    expect(formatTime('2026-11-11T17:30:00Z')).toBe('00:30');
    expect(formatDate('2026-11-11T17:30:00Z')).toBe('12/11');
  });
  it('formats date and date-time', () => {
    expect(formatDate('2026-11-12T10:00:00+07:00')).toBe('12/11');
    expect(formatDateTime('2026-11-12T10:00:00+07:00')).toBe('12/11 10:00');
  });
  it('returns a dash for invalid input', () => {
    expect(formatTime('not a date')).toBe('—');
  });
});

describe('formatHourWindows', () => {
  it('joins local hour windows', () => {
    expect(formatHourWindows([[0, 2], [12, 14]])).toBe('0–2h, 12–14h');
  });
});
