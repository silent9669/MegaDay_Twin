// Number display primitives (09 §3 `Money`, `Units`, `Range`; copy rules in 09 §5).
import type { Quantiles } from '../api/types';
import { formatNumber, formatVnd, formatVndMillions } from '../lib/format';
import { viText } from '../lib/i18n';

export type MoneyStyle = 'full' | 'short' | 'long';

function moneyText(value: number, style: MoneyStyle = 'full'): string {
  return style === 'full' ? formatVnd(value) : formatVndMillions(value, style);
}

/** VND amount: "531.000đ" (full), "4,3 tr" (short, narrow cells), "4,3 triệu" (long, sentences). */
export function MoneyVND({ value, style = 'full', className = '' }: { value: number; style?: MoneyStyle; className?: string }) {
  return <span className={`whitespace-nowrap tabular-nums ${className}`}>{moneyText(value, style)}</span>;
}

/**
 * p10–p90 range: "(−4,9 … −4,1)". Renders nothing when either bound is missing,
 * so a partial `Quantiles` never shows a fake interval.
 */
export function Range({
  q,
  format = (v) => formatNumber(v),
  brackets = true,
  className = '',
}: {
  q: Pick<Quantiles, 'p10' | 'p90'>;
  format?: (v: number) => string;
  brackets?: boolean;
  className?: string;
}) {
  if (q.p10 == null || q.p90 == null) return null;
  const from = format(q.p10);
  const to = format(q.p90);
  const body = `${from} … ${to}`;
  return (
    <span aria-label={viText.range.aria(from, to)} className={`whitespace-nowrap tabular-nums text-muted ${className}`}>
      {brackets ? `(${body})` : body}
    </span>
  );
}
