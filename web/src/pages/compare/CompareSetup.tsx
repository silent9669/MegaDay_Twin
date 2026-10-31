// What the Compare screen shows before there is a recommendation (real API only):
// no goal → lead sets one with POST /goals (others are told who must); goal but no
// run → operator runs POST /scenarios/run; 422 BLOCKING_DATA_ISSUE → what is missing.
import { useQueryClient } from '@tanstack/react-query';
import { useHQSelection } from '../../hq/selection';
import { Play, Target, TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { useCreateGoal, useMe, useRunScenario } from '../../api/queries';
import type { CampaignDetail, Envelope, GoalKind, ScenarioRun } from '../../api/types';
import { EmptyState, ErrorState } from '../../components/states';
import { config } from '../../lib/config';
import { queryKeys } from '../../lib/queryKeys';
import { viText } from '../../lib/i18n';
import { blockingInfo, DEFAULT_SEED, defaultDeadline, fromLocalInput, goalText, toLocalInput } from './compareLogic';

const t = viText.compare.setup;
const GOAL_KINDS: GoalKind[] = ['profit', 'sell_target', 'clear_stock', 'efficiency'];

/** Runs the first simulation and opens its recommendation (?rec=). */
function useFirstRun(campaignId: string) {
  const run = useRunScenario();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  function start(goalId: string | null) {
    run.mutate(
      { body: { campaign_id: campaignId, goal_id: goalId, samples: 1000, seed: DEFAULT_SEED } },
      {
        onSuccess: (res: Envelope<ScenarioRun>) => {
          const rec = res.data.recommendation;
          qc.setQueryData(queryKeys.scenario(res.data.scenario_id), res);
          void qc.invalidateQueries({ queryKey: ['recommendations'] });
          if (rec) {
            qc.setQueryData(queryKeys.recommendation(rec.recommendation_id), { data: rec, meta: res.meta });
            const next = new URLSearchParams(search);
            next.set('rec', rec.recommendation_id);
            next.set('campaign', campaignId);
            void navigate(`?${next}`, { replace: true });
          }
        },
      },
    );
  }
  return { run, start };
}

function BlockedNotice({ error }: { error: unknown }) {
  const info = blockingInfo(error);
  if (!info) return <ErrorState error={error} />;
  if (info.kind === 'goal') return <p className="text-sm text-warn">{info.message}</p>;
  return (
    <div className="rounded-lg border border-warn/50 bg-warn-soft p-3 text-sm">
      <p className="flex items-center gap-1.5 font-medium">
        <TriangleAlert aria-hidden className="size-4" />
        {t.blockedTitle}
      </p>
      <ul className="mt-1 list-disc pl-5">
        {(info.reasons.length ? info.reasons : [info.message]).map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
      <Link to="/import" className="btn-secondary mt-2">
        {t.importAction}
      </Link>
    </div>
  );
}

function GoalForm({ campaign, dataAsOf, onCreated }: { campaign: CampaignDetail; dataAsOf: string | null | undefined; onCreated: (goalId: string) => void }) {
  const create = useCreateGoal();
  const [kind, setKind] = useState<GoalKind>('profit');
  const [deadline, setDeadline] = useState(() => toLocalInput(defaultDeadline(dataAsOf)));
  const [target, setTarget] = useState('');
  const [reason, setReason] = useState('');
  const needsTarget = kind === 'sell_target';
  const invalid = !deadline || (needsTarget && !(Number(target) > 0));

  return (
    <form
      aria-label={t.goalFormTitle}
      className="mt-3 grid max-w-xl gap-3 rounded-lg border border-border bg-surface-2 p-3 text-sm"
      onSubmit={(e) => {
        e.preventDefault();
        if (invalid) return;
        create.mutate(
          {
            scope: 'campaign',
            scope_id: campaign.campaign_id,
            kind,
            deadline: fromLocalInput(deadline),
            target_units: needsTarget ? Number(target) : null,
            reason: reason.trim() || null,
          },
          { onSuccess: (res) => onCreated(res.data.goal_id) },
        );
      }}
    >
      <p className="font-medium">{t.goalFormTitle}</p>
      <label className="grid gap-1">
        <span>{t.goalKind}</span>
        <select value={kind} onChange={(e) => setKind(e.target.value as GoalKind)} className="rounded-md border border-border bg-surface px-2 py-1">
          {GOAL_KINDS.map((k) => (
            <option key={k} value={k}>
              {t.goalKinds[k]}
            </option>
          ))}
        </select>
      </label>
      <label className="grid gap-1">
        <span>{t.deadline}</span>
        <input type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} className="rounded-md border border-border bg-surface px-2 py-1" />
      </label>
      {needsTarget ? (
        <label className="grid gap-1">
          <span>{t.targetUnits}</span>
          <input type="number" min={1} value={target} onChange={(e) => setTarget(e.target.value)} className="rounded-md border border-border bg-surface px-2 py-1" />
        </label>
      ) : null}
      <label className="grid gap-1">
        <span>{t.reason}</span>
        <input value={reason} onChange={(e) => setReason(e.target.value)} className="rounded-md border border-border bg-surface px-2 py-1" />
      </label>
      {create.isError ? <ErrorState error={create.error} /> : null}
      <div>
        <button type="submit" className="btn-primary disabled:opacity-50" disabled={invalid || create.isPending}>
          <Target aria-hidden className="size-4" />
          {create.isPending ? t.savingGoal : t.saveGoal}
        </button>
      </div>
    </form>
  );
}

export function NeedsRecommendation({ campaign, dataAsOf }: { campaign: CampaignDetail; dataAsOf: string | null | undefined }) {
  const hq = useHQSelection();
  const me = useMe();
  const { run, start } = useFirstRun(campaign.campaign_id);
  // /me decides; until it answers (or if it fails) fall back to the configured dev role.
  const canGoal = me.data ? me.data.data.can_edit_goals : config.user.role === 'lead';
  const canRun = me.data ? me.data.data.can_run_scenarios : config.user.role !== 'viewer';
  const blocked = run.isError ? blockingInfo(run.error) : null;
  const goal = campaign.goal;

  if (!goal || blocked?.kind === 'goal') {
    return (
      <div className="card p-4">
        <EmptyState icon={Target} title={t.needGoalTitle(campaign.campaign_id)} body={t.needGoalBody} />
        {hq ? (
          <Link className="btn-primary mt-2" to={`/city-demo?district=hq&hq=goals&campaign=${encodeURIComponent(campaign.campaign_id)}`}>Review a goal and preview before applying</Link>
        ) : canGoal ? (
          <GoalForm campaign={campaign} dataAsOf={dataAsOf} onCreated={(goalId) => canRun && start(goalId)} />
        ) : (
          <p className="mt-2 text-sm text-warn">{t.needGoalAsk}</p>
        )}
        {run.isPending ? <p className="mt-2 text-sm text-muted">{t.running}</p> : null}
        {run.isError && blocked?.kind !== 'goal' ? <div className="mt-2"><BlockedNotice error={run.error} /></div> : null}
      </div>
    );
  }

  return (
    <div className="card p-4">
      <EmptyState icon={Play} title={t.noRecTitle(campaign.campaign_id)} body={t.noRecBody(goalText(goal, goal.goal_id))} />
      <div className="mt-2 space-y-2">
        {canRun ? (
          <button type="button" className="btn-primary" disabled={run.isPending} onClick={() => start(goal.goal_id)}>
            <Play aria-hidden className="size-4" />
            {run.isPending ? t.running : t.run}
          </button>
        ) : (
          <p className="text-sm text-muted">{t.runViewer}</p>
        )}
        {run.isError ? <BlockedNotice error={run.error} /> : null}
      </div>
    </div>
  );
}
