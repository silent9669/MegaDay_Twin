// Pure helpers for Tổng quan ca (09 §2.1). No React so they are easy to test.
import type { FreshnessSource, SkuListItem } from '../../api/types';
import { formatNumber } from '../../lib/format';
import { viText } from '../../lib/i18n';

const t = viText.overview;

/** 09 §2.1: open alert, or a recommendation that is not plain "continue". */
export function needsAttention(item: SkuListItem): boolean {
  if (item.open_alerts > 0) return true;
  const rec = item.recommendation;
  if (!rec) return false;
  if (rec.status === 'none_meets_goal') return true;
  return rec.best != null && rec.best.kind !== 'continue';
}

export type AttentionGroup = {
  /**
   * One card per decision: rows sharing a recommendation (one campaign) are merged, so
   * the 3 variants of a shirt, or a fryer + its paper + the combo, are one card (09 §2.1).
   * Rows without a recommendation are grouped by product_id.
   */
  key: string;
  name: string;
  /** All rows are variants of the same product. */
  sameProduct: boolean;
  items: SkuListItem[];
  /** Highest-priority row of the group; its recommendation drives the card. */
  lead: SkuListItem;
};

/** Top `limit` products by priority among rows that need attention. Input order is kept within a group. */
export function groupAttention(items: readonly SkuListItem[], limit = 5): AttentionGroup[] {
  const sorted = [...items].filter(needsAttention).sort((a, b) => b.priority_score - a.priority_score);
  const groups = new Map<string, AttentionGroup>();
  for (const item of sorted) {
    const key = item.recommendation?.recommendation_id ?? `product:${item.product_id || item.sku}`;
    const g = groups.get(key);
    if (g) {
      g.items.push(item);
      g.sameProduct = g.sameProduct && item.product_id === g.lead.product_id;
    } else groups.set(key, { key, name: item.name, sameProduct: true, items: [item], lead: item });
  }
  return [...groups.values()].slice(0, limit);
}

export function displayName(item: Pick<SkuListItem, 'name' | 'variant'>): string {
  return item.variant ? `${item.name} · ${item.variant}` : item.name;
}

/** "Đủ bán (giờ)": p50 of hours of cover. null p50 = does not run out within the horizon. */
export function coverText(item: Pick<SkuListItem, 'hours_of_cover'>): string {
  const c = item.hours_of_cover;
  if (!c) return '—';
  if (c.p50 == null) return c.horizon_hours != null ? t.coverBeyond(formatNumber(c.horizon_hours, 0)) : '—';
  return formatNumber(c.p50);
}

/** Clock time the stock runs out at p50 (data time + hours of cover); null if no stock-out is expected. */
export function stockoutAt(dataAsOf: string | null | undefined, item: Pick<SkuListItem, 'hours_of_cover' | 'stock_available'>): Date | null {
  const p50 = item.hours_of_cover?.p50;
  if (!dataAsOf || p50 == null || p50 <= 0 || !item.stock_available) return null;
  const base = new Date(dataAsOf);
  if (Number.isNaN(base.getTime())) return null;
  return new Date(base.getTime() + p50 * 3_600_000);
}

/** Sources past their threshold or missing: the yellow banner of 09 §4. */
export function staleSources(sources: readonly FreshnessSource[] | undefined): FreshnessSource[] {
  return (sources ?? []).filter((s) => s.status === 'stale' || s.status === 'missing');
}

export function isUpdating(item: Pick<SkuListItem, 'compute_state'>): boolean {
  return item.compute_state === 'computing' || item.compute_state === 'queued';
}

/** Latest computed_at among rows (for "Lần tính gần nhất lúc …"). */
export function lastComputed(items: readonly Pick<SkuListItem, 'computed_at'>[]): string | null {
  let best: string | null = null;
  let bestT = -Infinity;
  for (const i of items) {
    if (!i.computed_at) continue;
    const ts = new Date(i.computed_at).getTime();
    if (ts > bestT) {
      bestT = ts;
      best = i.computed_at;
    }
  }
  return best;
}

export const SORT_KEYS = ['priority', 'sku', 'cover', 'sold_today'] as const;
export type SortKey = (typeof SORT_KEYS)[number];
