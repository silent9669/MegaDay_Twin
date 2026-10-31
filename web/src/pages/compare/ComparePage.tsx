import { analyzeWithAI } from '../../city/analysis-context';
import { useHQSelection } from '../../hq/selection';
// So sánh lựa chọn (09 §2.3): tier 1 conclusion + ZoneBar + 5 action cards,
// tier 2 reasons / flip conditions / what-if sliders, tier 3 audit table and lineage.
import { ArrowLeft, ChevronRight, FilePlus2, Scale } from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router';
import { useFreshness, useRecommendations, useRunScenario } from '../../api/queries';
import { ComputeStateTag, ConfidenceBadge } from '../../components/badges';
import { Banner, EmptyState, ErrorState, PageHeader, SkeletonRows, UpdatingRegion } from '../../components/states';
import { config } from '../../lib/config';
import { formatTime, formatVnd } from '../../lib/format';
import { viText } from '../../lib/i18n';
import { useStoredBoolean } from '../../lib/storage';
import {
  ActionCards,
  FlipList,
  Lineage,
  ParamSliders,
  ReasonList,
  ResultsTable,
  TierSection,
  ZoneBarBlock,
} from './CompareParts';
import { NeedsRecommendation } from './CompareSetup';
import {
  BASE_WHAT_IF,
  clampLeftover,
  goalText,
  holdBetterReason,
  leftoverMax as leftoverMaxFor,
  leftoverMin as leftoverMinFor,
  noZoneBarText,
  outOfRangeMax,
  betterThanBothLine,
  buildSimRequest,
  flipHits,
  headline,
  isWhatIfActive,
  localZoneBar,
  pauseBlockedLine,
  whatIfKey,
  type WhatIf,
} from './compareLogic';
import { DecisionForm } from './DecisionForm';
import { useCompareData } from './useCompareData';

const t = viText.compare;
const DRAG_SAMPLES = 300;
const RELEASE_SAMPLES = 1000;
const DEBOUNCE_MS = 250;
/** Hand-written fixture cases (CompareIndexPage examples); the rest is an API snapshot. */
const ILLUSTRATIVE_RECS = new Set(['rec-510']);

function CompareSkeleton() {
  return (
    <div className="space-y-4" role="status" aria-label={viText.state.loading}>
      <div className="h-8 w-2/3 animate-pulse rounded bg-surface-2" />
      <div className="h-36 animate-pulse rounded-lg bg-surface-2" />
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-5">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="h-28 animate-pulse rounded-lg bg-surface-2" />
        ))}
      </div>
      <div className="h-12 animate-pulse rounded-lg bg-surface-2" />
    </div>
  );
}

export function ComparePage({sku}: {sku?:string} = {}) {
  const hq = useHQSelection();
  const { target, rec: recQuery, scenario: scenarioQuery } = useCompareData(sku);
  const freshness = useFreshness();
  const location = useLocation();

  const [tier2Open, setTier2Open] = useStoredBoolean('compare.tier2.open', false);
  const [tier3Open, setTier3Open] = useStoredBoolean('compare.tier3.open', false);
  const [decisionOpen, setDecisionOpen] = useState(false);

  const [whatIf, setWhatIf] = useState<WhatIf>(BASE_WHAT_IF);
  const [samples, setSamples] = useState(DRAG_SAMPLES);
  const run = useRunScenario();
  const { mutate: runMutate, reset: runReset } = run;
  const lastSent = useRef<string>('');

  const recEnv = recQuery.data;
  const scenarioEnv = scenarioQuery.data;
  const baseRec = recEnv?.data;
  const baseRun = scenarioEnv?.data;

  // Citation links to "#tier-3" open the audit tier.
  useEffect(() => {
    if (location.hash === '#tier-3') {
      setTier3Open(true);
      document.getElementById('tier-3')?.scrollIntoView?.({ block: 'start' });
    }
  }, [location.hash, setTier3Open]);

  // Real API: re-run the simulation for the what-if (debounced; 300 samples while
  // dragging, 1000 on release). Fixture mode only uses the browser estimate.
  useEffect(() => {
    if (config.useFixtures || !baseRec) return;
    if (!isWhatIfActive(whatIf)) {
      lastSent.current = '';
      runReset();
      return;
    }
    const key = `${whatIfKey(whatIf)}#${samples}`;
    if (key === lastSent.current) return;
    const timer = window.setTimeout(() => {
      lastSent.current = key;
      runMutate({ body: buildSimRequest(baseRun, baseRec, whatIf, samples) });
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [whatIf, samples, baseRec, baseRun, runMutate, runReset]);

  const serverForCurrent =
    run.data && run.variables && whatIfKey(whatIf) !== whatIfKey(BASE_WHAT_IF)
      ? (run.variables.body.param_overrides?.leftover_value_per_unit ?? null) === whatIf.leftoverValue &&
        (run.variables.body.param_overrides?.restock_delay_days ?? 0) === whatIf.restockDelayDays
      : false;

  const view = useMemo(() => {
    if (!baseRec) return null;
    const rerun = serverForCurrent ? run.data?.data : undefined;
    const rec = rerun?.recommendation ?? baseRec;
    const results = rerun?.results ?? baseRun?.results ?? [];
    const local = rerun ? { bar: rec.zone_bar, estimated: false } : localZoneBar(baseRec, whatIf);
    return { rec, results, local, rerun };
  }, [baseRec, baseRun, run.data, serverForCurrent, whatIf]);

  // ------------------------------------------------------------------ states
  if (target.resolving || (recQuery.isPending && target.recommendationId) || (!target.recommendationId && target.campaignPending)) {
    return (
      <>
        <PageHeader title={viText.pages.compare.title} description={viText.pages.compare.description} />
        <CompareSkeleton />
      </>
    );
  }
  if (target.resolveError) {
    return (
      <>
        <PageHeader title={viText.pages.compare.title} description={viText.pages.compare.description} />
        <ErrorState error={target.resolveError} onRetry={target.retryResolve} />
      </>
    );
  }
  if (!target.recommendationId) {
    const header = (
      <PageHeader
        title={target.campaignId ? t.title(target.campaignId) : viText.pages.compare.title}
        description={target.campaign ? t.campaignName(target.campaign.name) : viText.pages.compare.description}
      />
    );
    if (!config.useFixtures && target.campaign) {
      return (
        <>
          {header}
          <NeedsRecommendation campaign={target.campaign} dataAsOf={freshness.data?.data.data_as_of} />
        </>
      );
    }
    if (target.skuItem && !target.skuItem.campaign) {
      return (
        <>
          {header}
          <EmptyState icon={Scale} title={t.noCampaignTitle} body={t.noCampaignBody} />
        </>
      );
    }
    return (
      <>
        {header}
        {target.sku && !target.skuItem ? <p className="mb-3 text-muted">{t.notFoundSku(target.sku)}</p> : null}
        <EmptyState
          icon={Scale}
          title={t.noRecommendationTitle}
          body={t.noRecommendationBody}
          action={
            <Link to="/" className="btn-secondary">
              {viText.pages.notFound.back}
            </Link>
          }
        />
      </>
    );
  }
  if (recQuery.isError) {
    return (
      <>
        <PageHeader title={viText.pages.compare.title} description={viText.pages.compare.description} />
        <ErrorState error={recQuery.error} onRetry={() => void recQuery.refetch()} />
      </>
    );
  }
  if (!view || !baseRec) return null;

  // ------------------------------------------------------------------ ready
  const { rec, results, local, rerun } = view;
  const meta = rerun ? run.data?.meta : (scenarioEnv?.meta ?? recEnv?.meta);
  const dataAsOf = rec.inputs_ref.data_as_of ?? meta?.data_as_of ?? null;
  const computedAt = meta?.computed_at ?? recEnv?.meta?.computed_at ?? rec.created_at ?? null;
  const confidence = meta?.confidence ?? rec.confidence;
  const computeState = meta?.compute_state ?? 'fresh';
  const staleSources = (freshness.data?.data.sources ?? []).filter((s) => s.status === 'stale' || s.status === 'missing');
  const holdReason = holdBetterReason(rec);
  const campaign = target.campaign;
  const vMax = leftoverMaxFor(campaign, baseRec, config.useFixtures);
  const vMin = leftoverMinFor(campaign, config.useFixtures);
  const apiMax = run.isError ? outOfRangeMax(run.error) : null;
  const whatIfActive = isWhatIfActive(whatIf);
  const updating = run.isPending || computeState === 'computing' || computeState === 'queued';

  const inputs = baseRec.zone_bar?.inputs ?? null;
  const leftoverBase = inputs?.leftover_value_per_unit_vnd ?? 0;
  const hasRestockFlip = baseRec.flip_conditions.some((f) => f.factor === 'restock_delay_days');
  const showLeftover = Boolean(inputs) || !config.useFixtures;
  const showRestock = hasRestockFlip || !config.useFixtures;
  const hits = flipHits(baseRec, whatIf);

  let sliderStatus: ReactNode = null;
  if (whatIfActive) {
    const lines: string[] = [t.whatIfActive];
    if (config.useFixtures) {
      if (whatIf.leftoverValue !== null && !inputs) lines.push(t.leftoverNoInputs);
      lines.push(hits.length ? hits.map((h) => t.flipHit(h.text)).join(' ') : t.flipNotHit);
    } else if (run.isError) {
      lines.push(t.serverError(run.error instanceof Error ? run.error.message : String(run.error)));
      if (apiMax != null) lines.push(t.clampedTo(formatVnd(apiMax)));
    } else if (run.isPending) {
      lines.push(t.serverRunning(run.variables?.body.samples ?? samples));
    } else if (rerun && run.data) {
      lines.push(t.serverDone(run.variables?.body.samples ?? samples, run.data.data.scenario_id));
    }
    sliderStatus = lines.map((l) => <p key={l}>{l}</p>);
  }

  const head = headline(rec);
  const backTo = target.sku ? `/sku/${encodeURIComponent(target.sku)}` : '/';
  const backLabel = target.skuItem ? target.skuItem.name : t.back;
  const skuNames = target.campaignSkuNames;

  return (
    <>
      <div className="mb-2">
        <Link to={backTo} onClick={e=>{if(hq){e.preventDefault();hq.tab('details');}}} className="inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
          <ArrowLeft aria-hidden className="size-4" />
          {backLabel}
        </Link>
      </div>
      <PageHeader
        title={t.title(rec.campaign_id)}
        description={[campaign ? t.campaignName(campaign.name) : null, skuNames.length ? t.skusInCampaign(skuNames.join(', ')) : null].filter(Boolean).join(' · ') || viText.pages.compare.description}
      />
      <div className="-mt-3 mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <span>{t.goal(goalText(campaign?.goal, rec.inputs_ref.goal_id))}</span>
        <span className="text-muted tabular-nums">
          {t.dataLine(formatTime(dataAsOf), formatTime(computedAt))}
        </span>
        <ConfidenceBadge value={confidence} />
        {computeState !== 'fresh' ? <ComputeStateTag state={computeState} computedAt={computedAt ?? undefined} /> : null}
        {config.useFixtures ? (
          <span className="text-xs text-warn">{ILLUSTRATIVE_RECS.has(rec.recommendation_id) ? t.fixtureNote : t.snapshotNote}</span>
        ) : null}
      </div>

      {computeState === 'stale' || staleSources.length ? (
        <Banner tone="warn">
          {viText.banner.stale(
            staleSources.length
              ? staleSources.map((s) => viText.freshnessSource[s.source] ?? s.source).join(', ')
              : viText.computeState.stale,
          )}
        </Banner>
      ) : null}
      {confidence === 'scenario' ? <Banner tone="neutral">{viText.banner.scenario}</Banner> : null}

      <div className="space-y-4">
        <TierSection id="tier-1" title={t.tier1} open collapsible={false}>
          <UpdatingRegion updating={updating} dataAsOf={dataAsOf} className="space-y-4">
            {rec.status === 'none_meets_goal' ? (
              <div className="rounded-lg border border-warn/50 bg-warn-soft p-3">
                <p className="font-medium">{t.noneMeetsGoal}</p>
                <button type="button" className="btn-secondary mt-2" disabled title={t.notifyLeadDisabled} aria-describedby="notify-lead-reason">
                  {t.notifyLead}
                </button>
                <p id="notify-lead-reason" className="mt-1 text-xs text-muted">
                  {t.notifyLeadDisabled}
                </p>
              </div>
            ) : (
              <>
                {head ? <p className="text-lg font-semibold leading-snug">{head}</p> : null}
                <ZoneBarBlock
                  bar={local.bar}
                  estimated={local.estimated}
                  noBarText={noZoneBarText(rec)}
                  extraLines={[pauseBlockedLine(local.bar, results, Boolean(holdReason)), betterThanBothLine(rec)]}
                />
              </>
            )}
            <ActionCards results={results} rec={rec} />
            <button className="btn-secondary" onClick={()=>analyzeWithAI({title:'Decision model comparison',source:config.useFixtures?'model-snapshot':'model-api',sku:target.sku,data:JSON.stringify({recommendation:rec,results,whatIf,dataAsOf,computedAt}).slice(0,24000)},'Explain this comparison in English. Trace expected outcomes, constraints and confidence to the supplied model results. Explain what assumptions would change the conclusion. Do not treat snapshots as live data or user assumptions as validated forecasts.')}>Analyze with AI</button>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <button
                type="button"
                className="btn-primary"
                aria-expanded={decisionOpen}
                onClick={() => setDecisionOpen((v) => !v)}
              >
                <FilePlus2 aria-hidden className="size-4" />
                {t.decision.open}
              </button>
            </div>
            {decisionOpen ? (
              <DecisionForm
                rec={rec}
                results={results}
                scenarioId={rerun?.scenario_id ?? baseRun?.scenario_id ?? rec.scenario_id ?? ''}
                whatIfActive={whatIfActive}
                onClose={() => setDecisionOpen(false)}
              />
            ) : null}
          </UpdatingRegion>
        </TierSection>

        <TierSection id="tier-2" title={t.tier2} open={tier2Open} onToggle={setTier2Open}>
          <div className="grid gap-5 lg:grid-cols-2">
            <div className="space-y-4">
              <ReasonList rec={rec.reasons.length ? rec : baseRec} computedAt={computedAt ?? undefined} />
              <FlipList rec={rec.flip_conditions.length ? rec : baseRec} />
            </div>
            {showLeftover || showRestock ? (
              <ParamSliders
                whatIf={whatIf}
                showLeftover={showLeftover}
                leftoverMax={vMax}
                leftoverMin={vMin}
                leftoverBase={leftoverBase}
                leftoverIsDefault={!inputs && whatIf.leftoverValue === null}
                leftoverHint={config.useFixtures ? null : t.sliderLeftoverHint(formatVnd(vMin), formatVnd(vMax))}
                showRestock={showRestock}
                onChange={(next) => {
                  setSamples(DRAG_SAMPLES);
                  setWhatIf(next.leftoverValue === null ? next : { ...next, leftoverValue: clampLeftover(next.leftoverValue, vMax, vMin) });
                }}
                onRelease={() => setSamples(RELEASE_SAMPLES)}
                onReset={() => {
                  setWhatIf(BASE_WHAT_IF);
                  setSamples(DRAG_SAMPLES);
                }}
                status={sliderStatus}
              />
            ) : null}
          </div>
        </TierSection>

        <TierSection id="tier-3" title={t.tier3} open={tier3Open} onToggle={setTier3Open}>
          {scenarioQuery.isError ? (
            <ErrorState error={scenarioQuery.error} onRetry={() => void scenarioQuery.refetch()} />
          ) : scenarioQuery.isPending ? (
            <SkeletonRows rows={3} />
          ) : (
            <>
              <ResultsTable results={results} rec={rec} />
              <Lineage rec={rec} run={rerun ?? baseRun} meta={meta} campaign={campaign} />
            </>
          )}
        </TierSection>
      </div>
    </>
  );
}

/** /compare without a target: pick an active recommendation (or an illustrative case in fixture mode). */
export function CompareIndexPage() {
  const recs = useRecommendations();
  const p = t.pick;
  const examples = [
    { to: '/campaign/CD002/compare', label: p.exampleItems.fryer1111 },
    { to: '/campaign/VIDU-T10/compare?rec=rec-510', label: p.exampleItems.page10 },
  ];
  return (
    <>
      <PageHeader title={viText.pages.compare.title} description={viText.pages.compare.description} />
      <section aria-labelledby="active-recs" className="mb-6">
        <h2 id="active-recs" className="mb-2 font-medium">
          {p.active}
        </h2>
        {recs.isPending ? (
          <SkeletonRows rows={2} />
        ) : recs.isError ? (
          <ErrorState error={recs.error} onRetry={() => void recs.refetch()} />
        ) : recs.data.data.length ? (
          <ul className="space-y-2">
            {recs.data.data.map((r) => (
              <li key={r.recommendation_id} className="card flex flex-wrap items-center gap-3 px-4 py-3">
                <span className="font-medium">{r.campaign_id}</span>
                <span>{r.best ? viText.action(r.best) : viText.recommendationStatus.none_meets_goal}</span>
                <ConfidenceBadge value={r.confidence} />
                <Link to={`/campaign/${encodeURIComponent(r.campaign_id)}/compare`} className="btn-secondary ml-auto">
                  {p.open}
                  <ChevronRight aria-hidden className="size-4" />
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted">{p.none}</p>
        )}
      </section>
      {config.useFixtures ? (
        <section aria-labelledby="example-recs">
          <h2 id="example-recs" className="mb-1 font-medium">
            {p.examples}
          </h2>
          <p className="mb-2 text-sm text-muted">{p.examplesNote}</p>
          <ul className="space-y-2">
            {examples.map((e) => (
              <li key={e.to} className="card flex flex-wrap items-center gap-3 px-4 py-3">
                <span>{e.label}</span>
                <Link to={e.to} className="btn-secondary ml-auto">
                  {p.open}
                  <ChevronRight aria-hidden className="size-4" />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}
