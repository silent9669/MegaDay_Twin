// Placeholder screens for WEB-01. Each one gets its real content in WEB-03..WEB-09.
import { Bell, Bot, ClipboardList, Package, Settings, Upload } from 'lucide-react';
import { Link, useParams } from 'react-router';
import { EmptyState, PageHeader } from '../components/states';
import { viText } from '../lib/i18n';

const p = viText.pages;

export function SkuDetailPage() {
  const { sku } = useParams();
  return (
    <>
      <PageHeader title={sku ? `${p.skuDetail.title} · ${sku}` : p.skuDetail.title} description={p.skuDetail.description} />
      <EmptyState
        icon={Package}
        title={p.skuDetail.emptyTitle}
        body={p.skuDetail.emptyBody}
        action={
          sku ? (
            <Link to={`/sku/${encodeURIComponent(sku)}/compare`} className="btn-secondary">
              {viText.nav.compare}
            </Link>
          ) : undefined
        }
      />
    </>
  );
}

export function ImportPage() {
  return (
    <>
      <PageHeader title={p.import.title} description={p.import.description} />
      <EmptyState icon={Upload} title={p.import.emptyTitle} body={p.import.emptyBody} />
    </>
  );
}

export function AlertsPage() {
  return (
    <>
      <PageHeader title={p.alerts.title} description={p.alerts.description} />
      <EmptyState icon={Bell} title={p.alerts.emptyTitle} body={p.alerts.emptyBody} />
    </>
  );
}

export function DecisionsPage() {
  return (
    <>
      <PageHeader title={p.decisions.title} description={p.decisions.description} />
      <EmptyState icon={ClipboardList} title={p.decisions.emptyTitle} body={p.decisions.emptyBody} />
    </>
  );
}

export function AssistantPage() {
  return (
    <>
      <PageHeader title={p.assistant.title} description={p.assistant.description} />
      <EmptyState icon={Bot} title={p.assistant.emptyTitle} body={p.assistant.emptyBody} />
    </>
  );
}

export function SettingsPage() {
  return (
    <>
      <PageHeader title={p.settings.title} description={p.settings.description} />
      <EmptyState icon={Settings} title={p.settings.emptyTitle} body={p.settings.emptyBody} />
    </>
  );
}

export function NotFoundPage() {
  return (
    <>
      <PageHeader title={p.notFound.title} description={p.notFound.description} />
      <Link to="/" className="btn-secondary">
        {p.notFound.back}
      </Link>
    </>
  );
}
