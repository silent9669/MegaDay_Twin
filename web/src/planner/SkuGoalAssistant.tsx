import { useEffect, useState } from 'react';
import { ArrowRight } from 'lucide-react';
import type { AssistantContext } from './assistant-contract';
import { openMiloForSku } from '../city/milo-contract';
import './assistant.css';

async function request<T>(path: string, signal: AbortSignal, body?: unknown): Promise<T> {
  const response = await fetch(`/api/sale-assistant/${path}`, { signal, cache: 'no-store',
    method: body === undefined ? 'GET' : 'POST',
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload) throw Error(payload?.error?.message || 'Could not read backend data. Try again.');
  return payload as T;
}
const number = (value: number | null, money = false) => value == null ? 'No data' : `${new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }).format(value)}${money ? ' ₫' : ''}`;
const date = (value: string | null) => value ? new Date(value).toLocaleString('en-US', { timeZone: 'Asia/Ho_Chi_Minh' }) : 'Not provided';

export function SkuGoalAssistant({ sku }: { sku: string }) {
  const [context, setContext] = useState<AssistantContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [contextError, setContextError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void request<AssistantContext>(`context?sku=${encodeURIComponent(sku)}`, controller.signal).then((value) => {
      if (value.sku.sku !== sku) throw Error('Returned context does not match the selected SKU.');
      if (!controller.signal.aborted) { setContext(value); setContextError(''); }
    }).catch((e: unknown) => { if (!controller.signal.aborted) { setContext(null); setContextError(e instanceof Error ? e.message : 'Could not read SKU data.'); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [sku, reload]);
  if (loading) return <section className="planner-card" role="status">Loading data for {sku}…</section>;
  if (!context) return <section className="planner-card" role="alert"><h2>Could not load SKU {sku}</h2><p>{contextError}</p><button type="button" onClick={() => { setLoading(true); setReload((v) => v + 1); }}>Retry context</button></section>;
  return <div className="sale-planner planner-embedded sku-conversation">
    <section className="planner-card sku-context" aria-labelledby="assistant-sku-title"><span className="planner-eyebrow">PRODUCT CONTEXT</span><h2 id="assistant-sku-title">{context.sku.name || sku}</h2><p><strong>{sku}</strong>{context.sku.variant ? ` · ${context.sku.variant}` : ''}</p><p className="planner-help">Data as of {date(context.dataAsOf)} (Vietnam time). Expand a value to view its source.</p>
      <div className="sku-facts">{([
        ['Available stock', context.sku.stockAvailable, false, 'stock_available'],
        ['Sold today', context.sku.soldTodayUnits, false, 'sold_today_units'],
        ['Effective price', context.sku.priceEffectiveVnd, true, 'price_effective_vnd'],
        ['Contribution per unit', context.sku.contributionPerUnitVnd, true, 'contribution_per_unit_vnd'],
      ] as const).map(([label, value, money, field]) => <details key={field}><summary><span>{label}</span><strong>{number(value, money)}</strong></summary><p>Field <code>{field}</code> in backend SKU data. {context.source?.label ?? 'Source name not provided.'}</p><p>Source ID: {context.source?.id ?? 'Not provided'} · as of {date(context.source?.data_as_of ?? context.dataAsOf)}.</p>{field === 'contribution_per_unit_vnd' && <p>Backend contribution excludes shared ad spend. Detailed calculations are saved with simulation traces.</p>}</details>)}</div>
      <div className="sku-campaign-scope"><strong>Campaign scope</strong>{context.campaigns.length ? <ul>{context.campaigns.map((campaign) => <li key={campaign.id}>{campaign.name} ({campaign.id}) · SKU: {campaign.skus.join(', ')}</li>)}</ul> : <p>No linked campaigns. You can discuss goals; link a campaign before simulating.</p>}<p>Sales and clearance quantities can target this SKU. Budget, expected loss and economic value J always apply to the whole campaign, not to this SKU alone.</p></div>
      {!!context.notices.length && <ul className="planner-notices">{context.notices.map((notice, i) => <li key={i}>{notice}</li>)}</ul>}
    </section>
    <section className="planner-card sku-chat"><h2>Milo</h2><p>{context.opening}</p><button type="button" className="planner-primary" onClick={() => openMiloForSku(sku)}>Discuss with Milo <ArrowRight aria-hidden size={16} /></button><p className="planner-help">Milo keeps conversations per SKU. Review and confirm goals before simulation.</p></section>
  </div>;
}
