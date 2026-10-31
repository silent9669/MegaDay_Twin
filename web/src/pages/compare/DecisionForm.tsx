// Minimal "Tạo quyết định" form (09 §2.3, 08 §3.10). Saves a draft via POST /decisions,
// then offers "Gửi duyệt". Disabled with a stated reason when it cannot be sent.
import { useHQSelection } from '../../hq/selection';
import { useId, useState } from 'react';
import { Link } from 'react-router';
import { useCreateDecision, useSubmitDecision } from '../../api/queries';
import type { Recommendation, ScenarioResult } from '../../api/types';
import { ErrorState } from '../../components/states';
import { config } from '../../lib/config';
import { viText } from '../../lib/i18n';
import { actionKey, orderResults, sameAction, violationLabel } from './compareLogic';

const t = viText.compare.decision;

function newKey(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `dec-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

export function DecisionForm({
  rec,
  results,
  scenarioId,
  whatIfActive,
  onClose,
}: {
  rec: Recommendation;
  results: ScenarioResult[];
  scenarioId: string;
  whatIfActive: boolean;
  onClose: () => void;
}) {
  const hq=useHQSelection();
  const ordered = orderResults(results);
  const defaultChoice = ordered.find((r) => sameAction(r.action, rec.best)) ?? ordered.find((r) => r.feasible);
  const [choice, setChoice] = useState<string>(defaultChoice ? actionKey(defaultChoice.action) : '');
  const [rationale, setRationale] = useState(rec.reasons.join('; '));
  const [idempotencyKey] = useState(newKey);
  const create = useCreateDecision();
  const submit = useSubmitDecision();
  const reasonId = useId();
  const rationaleId = useId();

  const selected = ordered.find((r) => actionKey(r.action) === choice);
  const override = selected ? !sameAction(selected.action, rec.best) : false;

  let disabledReason: string | null = null;
  if (config.useFixtures) disabledReason = t.disabledFixture;
  else if (config.user.role === 'viewer') disabledReason = t.disabledViewer;
  else if (!selected || !selected.feasible) disabledReason = t.disabledInfeasible;
  else if (!rationale.trim()) disabledReason = t.disabledRationale;

  const draft = create.data?.data;

  function save() {
    if (disabledReason || !selected) return;
    create.mutate({
      idempotencyKey,
      body: {
        target: { campaign_id: rec.campaign_id },
        action: selected.action,
        scenario_id: scenarioId,
        recommendation_id: rec.recommendation_id,
        rationale: rationale.trim(),
        override_of_recommendation: override,
      },
    },{onSuccess:result=>hq?.log('Decision draft saved',`Decision ${result.data.id} · ${rec.campaign_id}`)});
  }

  return (
    <form
      aria-label={t.title}
      className="mt-3 space-y-3 rounded-lg border border-border bg-surface-2 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <fieldset>
        <legend className="mb-1 text-sm font-medium">{t.action}</legend>
        <div className="grid gap-1 sm:grid-cols-2">
          {ordered.map((r) => {
            const key = actionKey(r.action);
            return (
              <label key={key} className={`flex items-start gap-2 text-sm ${r.feasible ? '' : 'text-muted'}`}>
                <input
                  type="radio"
                  name="decision-action"
                  value={key}
                  checked={choice === key}
                  disabled={!r.feasible}
                  onChange={() => setChoice(key)}
                  className="mt-1"
                />
                <span>
                  {viText.action(r.action)}
                  {!r.feasible ? ` · ${r.violations.map(violationLabel).join('; ')}` : ''}
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>
      <label className="block text-sm">
        <span className="font-medium">{t.rationale}</span>
        <textarea
          id={rationaleId}
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={3}
          className="mt-1 block w-full rounded-md border border-border bg-surface px-2 py-1.5"
        />
        <span className="text-xs text-muted">{t.rationaleHint}</span>
      </label>
      {override ? <p className="text-sm text-warn">{t.override}</p> : null}
      {whatIfActive ? <p className="text-sm text-warn">{t.whatIf}</p> : null}

      {create.isError ? <ErrorState error={create.error} onRetry={save} /> : null}
      {submit.isError ? <ErrorState error={submit.error} /> : null}

      <div className="flex flex-wrap items-center gap-2">
        {draft ? (
          <>
            <span className="text-sm">{t.saved(draft.id)}</span>
            {submit.data ? (
              <span className="text-sm">
                {t.submitted(draft.id)} <Link to="/decisions" className="text-accent hover:underline">{viText.nav.decisions}</Link>
              </span>
            ) : (
              <button type="button" className="btn-primary" disabled={submit.isPending} onClick={() => submit.mutate(draft.id,{onSuccess:()=>hq?.log('Decision submitted',`Decision ${draft.id} · ${rec.campaign_id}`)})}>
                {t.submit}
              </button>
            )}
          </>
        ) : (
          <button
            type="submit"
            className="btn-primary disabled:cursor-not-allowed disabled:opacity-50"
            disabled={Boolean(disabledReason) || create.isPending}
            aria-describedby={disabledReason ? reasonId : undefined}
          >
            {t.save}
          </button>
        )}
        <button type="button" className="btn-secondary" onClick={onClose}>
          {t.cancel}
        </button>
      </div>
      {disabledReason && !draft ? (
        <p id={reasonId} className="text-sm text-muted">
          {disabledReason}
        </p>
      ) : null}
      <p className="text-xs text-muted">{t.simulatedNote}</p>
    </form>
  );
}
