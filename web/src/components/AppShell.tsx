import {
  Bell,
  Bot,
  ClipboardList,
  LayoutDashboard,
  Monitor,
  Moon,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
  Scale,
  Settings,
  Sun,
  Upload,
  X,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link, NavLink, Outlet } from 'react-router';
import { useAlerts, useFreshness } from '../api/queries';
import { config } from '../lib/config';
import { formatTime } from '../lib/format';
import { viText } from '../lib/i18n';
import { useTheme, type ThemePref } from '../lib/theme';
import { FreshnessBadge } from './badges';

type NavItem = { to: string; label: string; icon: LucideIcon; end?: boolean };

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: viText.nav.overview, icon: LayoutDashboard, end: true },
  { to: '/sku', label: viText.nav.skuDetail, icon: Package },
  { to: '/compare', label: viText.nav.compare, icon: Scale },
  { to: '/import', label: viText.nav.import, icon: Upload },
  { to: '/alerts', label: viText.nav.alerts, icon: Bell },
  { to: '/decisions', label: viText.nav.decisions, icon: ClipboardList },
  { to: '/assistant', label: viText.nav.assistant, icon: Bot },
  { to: '/settings', label: viText.nav.settings, icon: Settings },
];

const THEME_ORDER: ThemePref[] = ['system', 'light', 'dark'];
const THEME_ICON: Record<ThemePref, LucideIcon> = { system: Monitor, light: Sun, dark: Moon };

function ThemeToggle() {
  const { pref, setPref } = useTheme();
  const Icon = THEME_ICON[pref];
  const next = THEME_ORDER[(THEME_ORDER.indexOf(pref) + 1) % THEME_ORDER.length] ?? 'system';
  const label = `${viText.theme.label}: ${viText.theme[pref]}`;
  return (
    <button type="button" className="icon-btn" title={label} aria-label={label} onClick={() => setPref(next)}>
      <Icon aria-hidden className="size-4" />
    </button>
  );
}

function TopBar({ onToggleAssistant }: { onToggleAssistant: () => void }) {
  const freshness = useFreshness();
  const alerts = useAlerts('open');
  const sources = freshness.data?.data.sources ?? [];
  const orders = sources.find((s) => s.source === 'orders');
  const alertCount = alerts.data?.data.length ?? 0;

  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border bg-surface px-4">
      <Link to="/" className="flex items-baseline gap-2 whitespace-nowrap">
        <span className="font-semibold">{viText.app.name}</span>
        <span className="hidden text-muted md:inline">· {viText.app.shopName}</span>
      </Link>
      <span className="hidden rounded bg-accent-soft px-2 py-0.5 text-xs font-medium text-accent lg:inline">{viText.app.event}</span>
      {config.useFixtures ? (
        <span title={viText.topBar.fixtureModeHint} className="rounded border border-dashed border-warn px-2 py-0.5 text-xs text-warn">
          {viText.topBar.fixtureMode}
        </span>
      ) : null}

      <div className="ml-auto flex items-center gap-3">
        {orders ? (
          <span className="hidden whitespace-nowrap tabular-nums text-muted sm:inline">{viText.topBar.dataUntil(formatTime(orders.as_of))}</span>
        ) : null}
        <div className="hidden items-center gap-1 md:flex">
          {sources.map((s) => (
            <FreshnessBadge key={s.source} source={s} />
          ))}
        </div>
        <Link
          to="/alerts"
          aria-label={viText.topBar.alertsAria(alertCount)}
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs hover:bg-surface-2"
        >
          <Bell aria-hidden className="size-4" />
          <span className="tabular-nums">{viText.topBar.alerts(alertCount)}</span>
        </Link>
        <ThemeToggle />
        <button type="button" className="icon-btn" title={viText.topBar.openAssistant} aria-label={viText.topBar.openAssistant} onClick={onToggleAssistant}>
          <Bot aria-hidden className="size-4" />
        </button>
        <span className="hidden whitespace-nowrap text-sm lg:inline">
          {config.user.name} · <span className="text-muted">{viText.role[config.user.role]}</span>
        </span>
      </div>
    </header>
  );
}

function SideNav({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  return (
    <nav aria-label={viText.nav.label} className={`flex shrink-0 flex-col border-r border-border bg-surface ${collapsed ? 'w-14' : 'w-56'}`}>
      <ul className="flex-1 space-y-0.5 p-2">
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={end}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                `flex items-center gap-2.5 rounded-md px-2.5 py-2 ${
                  isActive ? 'bg-accent-soft font-medium text-accent' : 'text-fg hover:bg-surface-2'
                }`
              }
            >
              <Icon aria-hidden className="size-4 shrink-0" />
              <span className={collapsed ? 'sr-only' : 'truncate'}>{label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={onToggle}
        className="m-2 flex items-center gap-2 rounded-md px-2.5 py-2 text-muted hover:bg-surface-2"
        aria-label={collapsed ? viText.nav.expand : viText.nav.collapse}
      >
        {collapsed ? <PanelLeftOpen aria-hidden className="size-4" /> : <PanelLeftClose aria-hidden className="size-4" />}
        {collapsed ? null : <span>{viText.nav.collapse}</span>}
      </button>
    </nav>
  );
}

function AssistantPanel({ onClose }: { onClose: () => void }) {
  const t = viText.pages.assistant;
  return (
    <aside aria-label={t.panelTitle} className="hidden w-80 shrink-0 flex-col border-l border-border bg-surface lg:flex">
      <div className="flex h-11 items-center justify-between border-b border-border px-3">
        <span className="font-medium">{t.panelTitle}</span>
        <button type="button" className="icon-btn" aria-label={t.close} onClick={onClose}>
          <X aria-hidden className="size-4" />
        </button>
      </div>
      <div className="flex-1 p-4 text-muted">
        <p>{t.emptyBody}</p>
        <Link to="/assistant" className="mt-3 inline-block text-accent hover:underline">
          {t.openFull}
        </Link>
      </div>
    </aside>
  );
}

export function AppShell() {
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setAssistantOpen((v) => !v);
      } else if (e.key === 'Escape') {
        setAssistantOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className="flex h-full flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-10 focus:rounded focus:bg-surface focus:px-3 focus:py-2">
        {viText.app.skipToContent}
      </a>
      <TopBar onToggleAssistant={() => setAssistantOpen((v) => !v)} />
      <div className="flex min-h-0 flex-1">
        <SideNav collapsed={navCollapsed} onToggle={() => setNavCollapsed((v) => !v)} />
        <main id="main" className="min-w-0 flex-1 overflow-y-auto px-6 py-5">
          <Outlet />
        </main>
        {assistantOpen ? <AssistantPanel onClose={() => setAssistantOpen(false)} /> : null}
      </div>
    </div>
  );
}
