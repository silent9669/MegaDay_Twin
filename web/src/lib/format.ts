// English display formatting with VND and Vietnam local time.
// All times are shown in Asia/Ho_Chi_Minh regardless of the browser time zone.

export const VN_TZ = 'Asia/Ho_Chi_Minh';
const LOCALE = 'en-US';
const MINUS = '−';

const numberFormats = new Map<string, Intl.NumberFormat>();
function nf(minFrac: number, maxFrac: number): Intl.NumberFormat {
  const key = `${minFrac}:${maxFrac}`;
  let f = numberFormats.get(key);
  if (!f) {
    f = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: minFrac, maximumFractionDigits: maxFrac });
    numberFormats.set(key, f);
  }
  return f;
}

function withSign(value: number, body: string): string {
  return value < 0 ? `${MINUS}${body}` : body;
}

/** 35880 → "35,880"; 22.5 → "22.5". Uses a true minus sign for negatives. */
export function formatNumber(value: number | null | undefined, maxFractionDigits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return withSign(value, nf(0, maxFractionDigits).format(Math.abs(value)));
}

/** Full VND: 531000 → "531,000 VND". */
export function formatVnd(value: number): string {
  if (!Number.isFinite(value)) return '—';
  return withSign(value, `${nf(0, 0).format(Math.round(Math.abs(value)))} VND`);
}

/**
 * Millions for narrow cells and sentences: 4300000 → "4.3 M" | "4.3 million".
 * Below one million keeps two decimals so −50,000 VND reads "−0.05 M".
 */
export function formatVndMillions(value: number, style: 'short' | 'long' = 'short'): string {
  if (!Number.isFinite(value)) return '—';
  const millions = Math.abs(value) / 1_000_000;
  const body = nf(0, millions >= 1 ? 1 : 2).format(millions);
  return withSign(value, `${body} ${style === 'short' ? 'M' : 'million'}`);
}

/** 41 → "41 units". */
export function formatUnits(value: number | null | undefined, maxFractionDigits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return `${formatNumber(value, maxFractionDigits)} units`;
}

/** Ratio 0–1 → "24%". */
export function formatPct(ratio: number, maxFractionDigits = 0): string {
  return `${formatNumber(ratio * 100, maxFractionDigits)}%`;
}

const timeFormat = new Intl.DateTimeFormat('en-GB', {
  timeZone: VN_TZ,
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});
const dateFormat = new Intl.DateTimeFormat('en-GB', { timeZone: VN_TZ, day: '2-digit', month: '2-digit' });

function toDate(value: string | Date | null | undefined): Date | null {
  if (value == null) return null;
  const d = typeof value === 'string' ? new Date(value) : value;
  return Number.isNaN(d.getTime()) ? null : d;
}

/** ISO (UTC or +07:00) → "14:59" in Vietnam time. */
export function formatTime(value: string | Date | null | undefined): string {
  const d = toDate(value);
  return d ? timeFormat.format(d) : '—';
}

/** ISO → "12/11" in Vietnam time. */
export function formatDate(value: string | Date | null | undefined): string {
  const d = toDate(value);
  return d ? dateFormat.format(d) : '—';
}

/** ISO → "12/11 10:00" in Vietnam time. */
export function formatDateTime(value: string | Date | null | undefined): string {
  const d = toDate(value);
  return d ? `${dateFormat.format(d)} ${timeFormat.format(d)}` : '—';
}

/** Local hour windows [[0,2],[12,14]] → "0–2h, 12–14h". */
export function formatHourWindows(windows: ReadonlyArray<readonly [number, number]>): string {
  return windows.map(([from, to]) => `${from}–${to}h`).join(', ');
}
