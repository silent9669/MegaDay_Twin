import { useHQSelection } from '../hq/selection';
// Tổng quan ca (09 §2.1): "Cần xem ngay" (top 5 products by priority) + the full SKU
// table with cursor paging. Reads GET /skus, /recommendations, /freshness and /me.
import { ChevronRight, Database, Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router';
import { useFreshness, useMe, useRecommendations, useSkuPages, useSkus } from '../api/queries';
import type { Recommendation, SkuListItem } from '../api/types';
import { ComputeStateTag, ConfidenceBadge } from '../components/badges';
import { Banner, EmptyState, ErrorState, PageHeader, SkeletonRows } from '../components/states';
import { formatNumber, formatTime, formatUnits, formatVndMillions } from '../lib/format';
import { viText } from '../lib/i18n';
import {
  coverText,
  displayName,
  groupAttention,
  isUpdating,
  lastComputed,
  SORT_KEYS,
  staleSources,
  stockoutAt,
  type AttentionGroup,
  type SortKey,
} from './overview/overviewLogic';

const t = viText.overview;
const ATTENTION_LIMIT = 5;
/** The attention block looks at the first priority page only: high-priority rows come first. */
const ATTENTION_FILTERS = { sort: 'priority', limit: '200' };
const PAGE_SIZE = '50';

function recommendationText(item: SkuListItem): string {
  const rec = item.recommendation;
  if (!rec) return '—';
  if (rec.status === 'none_meets_goal') return viText.recommendationStatus.none_meets_goal;
  if (!rec.best) return viText.recommendationStatus.undetermined;
  return viText.action(rec.best);
}

function stockText(item: SkuListItem, dataAsOf: string | null | undefined): string {
  if (item.stock_available == null) return '—';
  if (item.stock_available <= 0) return t.stockOut;
  const stock = formatUnits(item.stock_available, 0);
  const at = stockoutAt(dataAsOf, item);
  if (at) return t.stockUntil(stock, formatTime(at));
  const horizon = item.hours_of_cover?.horizon_hours;
  return horizon != null ? t.stockBeyond(stock, formatNumber(horizon, 0)) : stock;
}

function AttentionCard({
  group,
  rec,
  dataAsOf,
}: {
  group: AttentionGroup;
  rec: Recommendation | undefined;
  dataAsOf: string | null | undefined;
}) {
  const hq = useHQSelection();
  const item = group.lead;
  const many = group.items.length > 1;
  const updating = isUpdating(item);
  // The lead SKU's route resolves the shared campaign and keeps "← <SKU>" as the back link.
  const compareTo = `/sku/${encodeURIComponent(item.sku)}/compare`;
  return (
    <li className={`card flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 ${updating ? 'opacity-60' : ''}`}>
      <div className="min-w-56 flex-1">
        <p className="font-medium">
          <span>{many && group.sameProduct ? group.name : displayName(item)}</span>
          {many ? (
            <span className="ml-1 text-sm font-normal text-muted">
              ({group.sameProduct ? t.variants(group.items.length) : t.sameCampaign(group.items.length - 1)})
            </span>
          ) : null}
        </p>
        <p className="text-xs text-muted">
          {many ? group.items.map((i) => (group.sameProduct ? i.variant || i.sku : i.sku)).join(', ') : item.sku} ·{' '}
          {item.campaign ? `${item.campaign.id} · ${item.campaign.status_code ?? ''}` : t.noCampaign}
        </p>
      </div>
      <div className="min-w-48 text-sm">
        <p className="tabular-nums">{stockText(item, dataAsOf)}</p>
        <p className="text-xs text-muted">
          {item.deviation ? (t.deviation[item.deviation] ?? item.deviation) : t.noForecastCompare}
          {item.open_alerts > 0 ? ` · ${t.alertCount(item.open_alerts)}` : ''}
        </p>
      </div>
      <div className="min-w-48 text-sm">
        <p className="font-medium">{recommendationText(item)}</p>
        <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted">
          {item.recommendation ? <ConfidenceBadge value={item.recommendation.confidence} /> : null}
          {rec?.expected_gain_vnd != null && rec.expected_gain_vnd > 0 ? (
            <span className="tabular-nums">{t.gainLine(formatVndMillions(rec.expected_gain_vnd))}</span>
          ) : null}
          {item.compute_state && item.compute_state !== 'fresh' ? <ComputeStateTag state={item.compute_state} /> : null}
        </div>
      </div>
      <Link to={compareTo} onClick={e=>{if(hq){e.preventDefault();hq.open({sku:item.sku,name:displayName(item),mode:'model'},'comparisons');}}} className="btn-secondary ml-auto" aria-label={`${t.compare} ${many && group.sameProduct ? group.name : displayName(item)}`}>
        {t.compare}
        <ChevronRight aria-hidden className="size-4" />
      </Link>
      <Link to={`/sku-assistant?sku=${encodeURIComponent(item.sku)}`} className="btn-secondary" aria-label={`Plan goal ${item.sku}`}>Plan goal {many ? item.sku : ''}</Link>
    </li>
  );
}

function SkuTable({ items }: { items: SkuListItem[] }) {
  const c = t.columns;
  const hq = useHQSelection();
  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-border text-xs text-muted">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">{c.sku}</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">{c.stock}</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">{c.cover}</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">{c.soldToday}</th>
            <th scope="col" className="px-3 py-2 font-medium">{c.recommendation}</th>
            <th scope="col" className="px-3 py-2 font-medium">{c.confidence}</th>
            <th scope="col" className="px-3 py-2 font-medium">{c.updated}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const updating = isUpdating(item);
            return (
              <tr key={item.sku} className={`border-b border-border last:border-0 hover:bg-surface-2 ${updating ? 'opacity-60' : ''}`}>
                <td className="px-3 py-2">
                  <Link to={`/sku/${encodeURIComponent(item.sku)}`} onClick={e=>{if(hq){e.preventDefault();hq.open({sku:item.sku,name:displayName(item),mode:'model'},'details');}}} className="font-medium hover:underline">
                    {item.sku}
                  </Link>
                  <div className="text-xs text-muted">{displayName(item)}</div>
                  <Link to={`/sku-assistant?sku=${encodeURIComponent(item.sku)}`} className="inline-flex min-h-11 items-center text-sm underline" aria-label={`Plan goal ${item.sku}`}>Plan goal</Link>
                </td>
                <td className="px-3 py-2 text-right tabular-nums">{formatNumber(item.stock_available, 0)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{coverText(item)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{formatNumber(item.sold_today_units, 0)}</td>
                <td className="px-3 py-2">{recommendationText(item)}</td>
                <td className="px-3 py-2">
                  {item.recommendation ? (
                    <ConfidenceBadge value={item.recommendation.confidence} />
                  ) : item.confidence ? (
                    <ConfidenceBadge value={item.confidence} />
                  ) : (
                    '—'
                  )}
                </td>
                <td className="px-3 py-2">
                  {updating ? (
                    <span className="text-xs font-medium text-accent">{t.updating}</span>
                  ) : item.compute_state ? (
                    <ComputeStateTag state={item.compute_state} computedAt={item.computed_at ?? undefined} />
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

function AllSkus() {
  const [sort, setSort] = useState<SortKey>('priority');
  const [q, setQ] = useState('');
  const query = useDebounced(q.trim(), 300);
  const filters = useMemo(() => {
    const f: Record<string, string> = { sort, limit: PAGE_SIZE };
    if (query) f.q = query;
    return f;
  }, [sort, query]);
  const pages = useSkuPages(filters);
  const items = pages.data?.pages.flatMap((p) => p.data) ?? [];
  // rows matching the filters, all pages (meta.total); unknown on old payloads
  const total = pages.data?.pages[0]?.meta?.total ?? null;

  let body;
  if (pages.isPending) body = <SkeletonRows rows={5} />;
  else if (pages.isError) body = <ErrorState error={pages.error} onRetry={() => void pages.refetch()} />;
  else if (items.length === 0) body = <p className="card px-4 py-3 text-muted">{t.noResults(query)}</p>;
  else
    body = (
      <>
        <SkuTable items={items} />
        <div className="mt-2 flex items-center gap-3 text-sm text-muted">
          <span>{t.loadedCount(items.length, Boolean(pages.hasNextPage), total)}</span>
          {pages.hasNextPage ? (
            <button
              type="button"
              className="btn-secondary"
              disabled={pages.isFetchingNextPage}
              onClick={() => void pages.fetchNextPage()}
            >
              {pages.isFetchingNextPage ? t.loadingMore : t.loadMore}
            </button>
          ) : null}
        </div>
      </>
    );

  return (
    <section aria-labelledby="all-heading">
      <div className="mb-2 flex flex-wrap items-end gap-3">
        <h2 id="all-heading" className="font-medium">
          {total != null ? t.allSkus(total) : t.allSkusTitle}
        </h2>
        <label className="ml-auto flex items-center gap-1.5 text-sm">
          <Search aria-hidden className="size-4 text-muted" />
          <span className="sr-only">{t.searchLabel}</span>
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t.searchPlaceholder}
            className="w-56 rounded-md border border-border bg-surface px-2 py-1"
          />
        </label>
        <label className="flex items-center gap-1.5 text-sm">
          <span className="text-muted">{t.sortLabel}</span>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            className="rounded-md border border-border bg-surface px-2 py-1"
          >
            {SORT_KEYS.map((k) => (
              <option key={k} value={k}>
                {t.sort[k]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {body}
    </section>
  );
}

export function ShiftOverviewPage() {
  const skus = useSkus(ATTENTION_FILTERS);
  const recs = useRecommendations();
  const freshness = useFreshness();
  const me = useMe();
  const p = viText.pages.overview;

  const items = useMemo(() => skus.data?.data ?? [], [skus.data]);
  const groups = useMemo(() => groupAttention(items, ATTENTION_LIMIT), [items]);
  const recById = useMemo(() => new Map((recs.data?.data ?? []).map((r) => [r.recommendation_id, r])), [recs.data]);
  const dataAsOf = skus.data?.meta?.data_as_of ?? freshness.data?.data.data_as_of;
  const computed = lastComputed(items) ?? skus.data?.meta?.computed_at ?? null;
  const stale = staleSources(freshness.data?.data.sources);
  const meData = me.data?.data;

  let body;
  if (skus.isPending) {
    body = <SkeletonRows rows={4} />;
  } else if (skus.isError) {
    body = <ErrorState error={skus.error} onRetry={() => void skus.refetch()} />;
  } else if (items.length === 0) {
    body = (
      <EmptyState
        icon={Database}
        title={t.emptyTitle}
        body={t.emptyBody}
        action={
          <Link to="/import" className="btn-primary">
            {t.emptyAction}
          </Link>
        }
      />
    );
  } else {
    body = (
      <div className="space-y-6">
        <section aria-labelledby="attention-heading">
          <h2 id="attention-heading" className="font-medium">
            {t.needsAttention(groups.length)}
          </h2>
          <p className="mb-2 text-xs text-muted">{t.attentionNote}</p>
          {groups.length > 0 ? (
            <ul className="space-y-2">
              {groups.map((g) => (
                <AttentionCard
                  key={g.key}
                  group={g}
                  dataAsOf={dataAsOf}
                  rec={g.lead.recommendation ? recById.get(g.lead.recommendation.recommendation_id) : undefined}
                />
              ))}
            </ul>
          ) : (
            <p className="card px-4 py-3 text-muted">{t.nothingToReview(computed ? formatTime(computed) : '—')}</p>
          )}
        </section>
        <AllSkus />
      </div>
    );
  }

  return (
    <>
      <PageHeader title={p.title} description={p.description} />
      {meData ? <p className="-mt-3 mb-3 text-xs text-muted">{t.roleLine(viText.role[meData.role as keyof typeof viText.role]??meData.role, meData.id)}</p> : null}
      {stale.length ? (
        <Banner tone="warn">{viText.banner.stale(stale.map((s) => viText.freshnessSource[s.source] ?? s.label).join(', '))}</Banner>
      ) : null}
      {body}
    </>
  );
}
