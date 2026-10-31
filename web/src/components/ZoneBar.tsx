// Decision zone bar (09 §4, 05 §7). Hand-drawn SVG, no chart library.
// Horizontal axis = units sold per hour if ads pause. Left of x* "continue is
// better", right "pause is better". Solid line = threshold, dashed = alternative
// thresholds, thick bar = p10–p90 forecast, dot = p50.
import type { ZoneBar as ZoneBarData } from '../api/types';
import { formatNumber } from '../lib/format';
import { viText } from '../lib/i18n';
import { defaultDomain, drawableAlts, niceStep, verdictSentence, zoneBarAriaLabel } from '../lib/zoneBarText';

const t = viText.zoneBar;
const fmt = (v: number) => formatNumber(v, 1);

const W = 640;
const H = 132;
const PAD_X = 18;
const BAND_TOP = 28;
const BAND_H = 40;
const BAND_MID = BAND_TOP + BAND_H / 2;
const AXIS_Y = 116;

export type ZoneBarProps = {
  bar: ZoneBarData;
  /** Axis domain; default [0, max(p90, threshold, alt thresholds) × 1,4] (09 §4). */
  domain?: [number, number];
  /** Shown under the bar when the bar is a browser-side estimate. */
  estimated?: boolean;
};

export function ZoneBar({ bar, domain, estimated = false }: ZoneBarProps) {
  const [d0, d1] = domain ?? defaultDomain(bar);
  const span = d1 - d0 || 1;
  const plotW = W - PAD_X * 2;
  const x = (v: number) => PAD_X + (Math.min(Math.max(v, d0), d1) - d0) * (plotW / span);

  const [p10, p50, p90] = bar.interval;
  const xt = x(bar.threshold);
  const x10 = x(p10);
  const x50 = x(p50);
  const x90 = x(p90);

  const step = niceStep(span);
  const ticks: number[] = [];
  for (let v = Math.ceil(d0 / step) * step; v <= d1 + 1e-9; v += step) ticks.push(Number(v.toFixed(6)));

  // Interval label sits right of p90 unless it would overflow the plot.
  const intervalText = `${fmt(p10)}–${fmt(p90)}`;
  const intervalRight = x90 + 8 + intervalText.length * 7 < W - PAD_X;
  const anchorFor = (px: number) => (px < PAD_X + 50 ? 'start' : px > W - PAD_X - 50 ? 'end' : 'middle');
  const label = zoneBarAriaLabel(bar);
  // Pause not allowed (05 §7.2): the pause side is hatched and dimmed, and verdict_note
  // replaces "Pause tốt hơn" so the bar never reads as a pause recommendation.
  const pauseBlocked = bar.pause_feasible === false;
  const hatchId = 'zonebar-blocked-hatch';

  return (
    <figure className="m-0">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={label} className="block max-h-44 overflow-visible">
        <title>{label}</title>
        {pauseBlocked ? (
          <defs>
            <pattern id={hatchId} width={8} height={8} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1={0} y1={0} x2={0} y2={8} strokeWidth={3} className="stroke-muted" opacity={0.45} />
            </pattern>
          </defs>
        ) : null}
        {/* Zone names */}
        <text x={PAD_X} y={18} className="fill-fg text-[13px] font-medium">
          ◀ {t.continueSide}
        </text>
        <text
          x={W - PAD_X}
          y={18}
          textAnchor="end"
          className={`text-[13px] font-medium ${pauseBlocked ? 'fill-muted line-through' : 'fill-fg'}`}
        >
          {pauseBlocked ? t.pauseSideBlocked : t.pauseSide} ▶
        </text>

        {/* Regions */}
        <rect x={PAD_X} y={BAND_TOP} width={Math.max(xt - PAD_X, 0)} height={BAND_H} className="fill-zone-continue" rx={4} />
        <rect
          x={xt}
          y={BAND_TOP}
          width={Math.max(W - PAD_X - xt, 0)}
          height={BAND_H}
          className="fill-zone-pause"
          opacity={pauseBlocked ? 0.4 : 1}
          rx={4}
        />
        {pauseBlocked ? (
          <rect x={xt} y={BAND_TOP} width={Math.max(W - PAD_X - xt, 0)} height={BAND_H} fill={`url(#${hatchId})`} rx={4} />
        ) : null}

        {/* Alternative thresholds (dashed) */}
        {drawableAlts(bar).map((alt) => {
          const xa = x(alt.threshold);
          return (
            <g key={alt.label}>
              <line x1={xa} x2={xa} y1={BAND_TOP - 2} y2={BAND_TOP + BAND_H + 2} strokeDasharray="5 4" strokeWidth={1.5} className="stroke-muted" />
              <text x={xa} y={102} textAnchor={anchorFor(xa)} className="fill-muted text-[11px]">
                {alt.label}: {fmt(alt.threshold)}
              </text>
            </g>
          );
        })}

        {/* Main threshold (solid) */}
        <line x1={xt} x2={xt} y1={BAND_TOP - 6} y2={BAND_TOP + BAND_H + 6} strokeWidth={2.5} className="stroke-fg" />
        <text x={xt} y={88} textAnchor={anchorFor(xt)} className="fill-fg text-[12px] font-semibold">
          {t.threshold(fmt(bar.threshold))}
        </text>

        {/* Forecast interval p10–p90 and p50 */}
        <rect x={x10} y={BAND_MID - 5} width={Math.max(x90 - x10, 2)} height={10} rx={5} className="fill-fg" />
        <circle cx={x50} cy={BAND_MID} r={7} strokeWidth={3} className="fill-surface stroke-fg" />
        <text
          x={intervalRight ? x90 + 10 : x10 - 10}
          y={BAND_MID + 4}
          textAnchor={intervalRight ? 'start' : 'end'}
          className="fill-fg text-[12px] tabular-nums"
        >
          {intervalText}
        </text>

        {/* Axis */}
        <line x1={PAD_X} x2={W - PAD_X} y1={AXIS_Y - 6} y2={AXIS_Y - 6} strokeWidth={1} className="stroke-border" />
        {ticks.map((v) => (
          <g key={v}>
            <line x1={x(v)} x2={x(v)} y1={AXIS_Y - 9} y2={AXIS_Y - 3} strokeWidth={1} className="stroke-muted" />
            <text x={x(v)} y={AXIS_Y + 10} textAnchor="middle" className="fill-muted text-[11px] tabular-nums">
              {fmt(v)}
            </text>
          </g>
        ))}
      </svg>
      <figcaption className="mt-1 space-y-0.5">
        <p className="text-xs text-muted">{bar.axis_label}</p>
        {pauseBlocked ? (
          <p role="note" className="rounded-md border border-warn/60 bg-warn-soft px-2 py-1 font-medium">
            {verdictSentence(bar)}
          </p>
        ) : (
          <p className="font-medium">{verdictSentence(bar)}</p>
        )}
        <p className="text-sm text-muted tabular-nums">
          {t.summary(fmt(p10), fmt(p50), fmt(p90), fmt(bar.threshold))}
          {drawableAlts(bar).map((a) => ` ${t.altSummary(a.label, fmt(a.threshold))}`).join('')}
        </p>
        {estimated ? <p className="text-xs text-warn">{t.estimated}</p> : null}
      </figcaption>
    </figure>
  );
}
