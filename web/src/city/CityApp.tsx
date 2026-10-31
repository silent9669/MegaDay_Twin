import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  Check,
  CircleHelp,
  Database,
  Home,
  Layers3,
  LogOut,
  Megaphone,
  MessageCircle,
  Minus,
  Moon,
  Sun,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Radio,
  RotateCcw,
  Settings2,
  Warehouse,
  X,
} from "lucide-react";
import {
  useAccountDocument,
  useAccountEvents,
  requestJSON,
} from "../studio/account";
import {
  defaultSettings,
  type ViewSettings,
  type Sandbox,
} from "../studio/workspace-types";
import type { SourcePack } from "../studio/sources";
import { ProductLabels } from "../studio/ProductLabels";
import "../studio/studio.css";
import { sourceView } from "../studio/source-view";
import { DataStudio } from "../studio/DataStudio";
import { ShiftOverviewPage } from "../pages/ShiftOverviewPage";
import { CompareIndexPage } from "../pages/compare/ComparePage";
import { CityChat } from "./CityChat";
import { PLATFORM_IDS, PLATFORMS } from "./platforms";
import type { createCity, District, WorldState } from "./world";
import "./city.css";
import { Sheet, SheetContent, SheetTitle } from "../components/ui/sheet";
import { Tutorial } from "./Tutorial";
import { TOUR, TOUR_KEY } from "./tutorial-steps";
import { useWorkspaceMonitor } from "./monitor/useWorkspaceMonitor";
import type { Landmark } from "./map-labels";
import { Brand, BrandMark } from "./Brand";
const DISTRICTS = [
  { id: "hq", name: "MegaDay HQ", icon: Building2 },
  { id: "warehouse", name: "Warehouse", icon: Warehouse },
  { id: "ads", name: "Ads Platform", icon: Megaphone },
  { id: "homes", name: "Residential district", icon: Home },
] as const;
export function CityApp({ studio = false }: { studio?: boolean }) {
  const [mobile, setMobile] = useState(
    () => matchMedia("(max-width: 899px)").matches,
  );
  useEffect(() => {
    const media = matchMedia("(max-width: 899px)");
    const update = () => setMobile(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const [chatAnchor, setChatAnchor] = useState<HTMLSpanElement | null>(null);
  const connected = useAccountEvents();
  const settings = useAccountDocument<ViewSettings>("settings"),
    sources = useAccountDocument<SourcePack>("sources"),
    sandbox = useAccountDocument<Sandbox>("sandbox");
  const updates = useWorkspaceMonitor(
    sources.document,
    sandbox.document,
    connected,
  );
  const [chatOpen, setChatOpen] = useState(false);
  const viewSettings = settings.document?.data ?? defaultSettings;
  const appearance = viewSettings.appearance ?? "morning";
  useEffect(() => {
    const previousTheme = document.documentElement.dataset.theme;
    document.body.dataset.cityAppearance = appearance;
    document.documentElement.dataset.theme = appearance === "night" ? "dark" : "light";
    return () => {
      delete document.body.dataset.cityAppearance;
      if (previousTheme) document.documentElement.dataset.theme = previousTheme;
      else delete document.documentElement.dataset.theme;
    };
  }, [appearance]);
  const [savingSettings, setSavingSettings] = useState(false);
  const feed = useMemo(
    () =>
      sourceView(
        sources.document?.data ?? null,
        sandbox.document?.data.hour ?? 1,
      ),
    [sources.document, sandbox.document],
  );
  const [compact, setCompact] = useState(true),
    [district, setDistrict] = useState<District | null>(null),
    [workspace, setWorkspace] = useState(false),
    [hqTab, setHqTab] = useState<"overview" | "compare">("overview"),
    [tour, setTour] = useState<number | null>(null),
    [ready, setReady] = useState(false),
    [failure, setFailure] = useState(""),
    [error, setError] = useState("");
  const [params, setParams] = useSearchParams();
  const settingsOpen = params.get("panel") === "settings";
  const navigate = useNavigate(),
    location = useLocation();
  const worldRef = useRef<ReturnType<typeof createCity> | null>(null),
    host = useRef<HTMLDivElement>(null),
    settingsDialog = useRef<HTMLDialogElement>(null);
  const reduced = useRef(false);
  const state: WorldState = {
    ...viewSettings,
    appearance,
    motion: viewSettings.motion,
    orders: feed.orderRows,
    adRows: feed.adRows,
    occupancy: feed.stock.length
      ? feed.stock.filter((s) => (s.stock ?? 0) > 0).length / feed.stock.length
      : 0,
  };
  const latest = useRef(state);
  latest.current = state;
  const enter = (next: District | null) => {
    setChatOpen(false);
    setDistrict(next);
    setWorkspace(false);
    worldRef.current?.focus(next);
  };
  const enterRef = useRef(enter);
  enterRef.current = enter;
  useEffect(() => {
    if (studio || !host.current) return;
    let active = true;
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    reduced.current = media.matches;
    const onMedia = () => {
      reduced.current = media.matches;
      worldRef.current?.update({
        ...latest.current,
        motion: latest.current.motion && !media.matches,
      });
    };
    media.addEventListener("change", onMedia);
    import("./world")
      .then(({ createCity }) => {
        if (!active || !host.current) return;
        try {
          worldRef.current = createCity(
            host.current,
            {
              ...latest.current,
              motion: latest.current.motion && !reduced.current,
            },
            (next) => enterRef.current(next),
          );
          setReady(true);
        } catch {
          setFailure(
            "3D is unavailable on this device. District controls remain available.",
          );
          setReady(true);
        }
      })
      .catch(() => {
        setFailure("City could not load. Use the district controls.");
        setReady(true);
      });
    return () => {
      active = false;
      media.removeEventListener("change", onMedia);
      worldRef.current?.dispose();
      worldRef.current = null;
    };
  }, [studio]);
  useEffect(() => {
    worldRef.current?.update({
      ...latest.current,
      motion: latest.current.motion && !reduced.current,
    });
  }, [viewSettings, feed]);
  useEffect(() => {
    if (settingsOpen) settingsDialog.current?.showModal();
    else settingsDialog.current?.close();
  }, [settingsOpen]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !chatOpen) {
        if (tour !== null) finishTour();
        else if (workspace) setWorkspace(false);
        else enter(null);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [workspace, tour, chatOpen]);
  const setSettings = async (next: ViewSettings) => {
    if (!settings.document || savingSettings) return;
    setSavingSettings(true);
    try {
      await settings.save(next, settings.document.revision);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Settings were not saved.");
      await settings.reload();
    } finally {
      setSavingSettings(false);
    }
  };
  function finishTour() {
    setTour(null);
    try {
      localStorage.setItem(TOUR_KEY, "complete");
    } catch {
      /* Optional device preference. */
    }
  }
  function tourStep(index: number) {
    setTour(index);
    enter(TOUR[index]?.place ?? null);
  }
  useEffect(() => {
    if (!ready || studio) return;
    const initial = setTimeout(() => {
      try {
        if (!localStorage.getItem(TOUR_KEY)) setTour(0);
      } catch {
        /* Replay remains available. */
      }
    }, 100);
    return () => clearTimeout(initial);
  }, [ready, studio]);
  const selectedStock = params.get('stock');
  useEffect(() => {
    if (!workspace || district !== 'warehouse' || chatOpen || !selectedStock) return;
    const frame = requestAnimationFrame(() => document.querySelector('.stock-highlight')?.scrollIntoView({ block: 'center', inline: 'nearest' }));
    return () => cancelAnimationFrame(frame);
  }, [workspace, district, chatOpen, selectedStock]);
  const closeSettings = () => {
    const next = new URLSearchParams(params);
    next.delete("panel");
    setParams(next);
  };
  return (
    <div
      className={`city-app ${studio ? "city-studio" : ""} ${chatOpen ? "milo-open" : ""}`}
      data-compact={compact}
    >
      <a className="city-skip" href="#city-main">
        Skip to content
      </a>
      <header className="city-topbar">
        <button
          aria-label={compact ? "Expand sidebar" : "Collapse sidebar"}
          className="city-icon"
          onClick={() => setCompact(!compact)}
        >
          {compact ? <PanelLeftOpen /> : <PanelLeftClose />}
        </button>
        <Link className="city-brand" to="/city-demo">
          <Brand />
        </Link>
        <span className="city-route">
          /{" "}
          {studio
            ? "Data Studio"
            : district
              ? DISTRICTS.find((d) => d.id === district)?.name
              : "Commerce city"}
        </span>
        <div className="city-account">
          <button className="city-appearance" aria-label="Night mode" aria-pressed={appearance === "night"}
            disabled={!settings.document || savingSettings}
            onClick={() => void setSettings({ ...viewSettings, appearance: appearance === "night" ? "morning" : "night" })}>
            {appearance === "night" ? <Moon size={16} /> : <Sun size={16} />}
            <span>{appearance === "night" ? "Night" : "Morning"}</span>
          </button>
          <span className={`connection-dot ${connected ? "connected" : ""}`} />
          <span className="city-connection-label">{connected ? "Account synced" : "Connecting…"}</span>
          <span className="city-demo-badge">Sandbox</span>
        </div>
      </header>
      <nav className="city-sidebar" aria-label="City sidebar">
        <div className="city-nav-main">
          <Link
            to="/city-demo"
            title="View city"
            aria-label="View city"
            aria-current={!studio && !district ? "page" : undefined}
            onClick={() => enter(null)}
          >
            <Layers3 />
            <span>View city</span>
          </Link>
          <div className="city-nav-divider" />
          {DISTRICTS.map(({ id, name, icon: Icon }) => (
            <button
              key={id}
              title={name}
              aria-label={name}
              aria-current={!studio && district === id ? "location" : undefined}
              onClick={() => {
                if (studio) navigate(`/city-demo?district=${id}`);
                else enter(id);
              }}
            >
              <Icon />
              <span>{name}</span>
            </button>
          ))}
        </div>
        <div className="city-nav-tools">
          <Link
            to="/mock-data"
            title="Data Studio"
            aria-label="Data Studio"
            aria-current={studio ? "page" : undefined}
          >
            <Database />
            <span>Data Studio</span>
          </Link>
          <button
            title="View settings"
            aria-label="View settings"
            onClick={() =>
              setParams({ ...Object.fromEntries(params), panel: "settings" })
            }
          >
            <Settings2 />
            <span>View settings</span>
          </button>
          <button
            title="Replay tutorial"
            aria-label="Replay tutorial"
            onClick={() => {
              if (studio) navigate("/city-demo?tour=replay");
              else tourStep(0);
            }}
          >
            <CircleHelp />
            <span>Replay tutorial</span>
          </button>
          <button
            title="Open PM assistant"
            aria-label="Open PM assistant"
            onClick={() => window.dispatchEvent(new Event("workspace:chat"))}
          >
            <MessageCircle />
            <span>Ask your PM</span>
          </button>
          <button
            title="Sign out"
            aria-label="Sign out"
            onClick={() =>
              void requestJSON("/api/auth/logout", { method: "POST" }).then(
                () => window.location.assign("/login"),
              )
            }
          >
            <LogOut />
            <span>Sign out</span>
          </button>
        </div>
      </nav>
      {error && !settingsOpen && <p role="alert" className="city-save-error">{error}</p>}
      <main id="city-main" className="city-main">
        {studio ? (
          <DataStudio />
        ) : (
          <>
            <div ref={host} className="city-canvas" />
            {!ready && (
              <div className="city-loading" role="status">
                <BrandMark />
                <span>Opening your city…</span>
              </div>
            )}
            {failure && (
              <p className="city-failure" role="status">
                {failure}
              </p>
            )}
            <div className="city-scene-title">
              <h1>
                {district
                  ? DISTRICTS.find((d) => d.id === district)?.name
                  : "Commerce city"}
              </h1>
              <span>
                {district === "warehouse"
                  ? `${feed.stock.filter(row => (row.stock ?? 0) > 0).length} products in stock`
                  : district === "ads"
                    ? `${viewSettings.platforms.length} platform${viewSettings.platforms.length === 1 ? "" : "s"}`
                    : ""}
              </span>
              {district && (
                <div className="city-district-actions">
                  <button onClick={() => enter(null)}>
                    <ArrowLeft size={16} />
                    City
                  </button>
                  <button
                    className="primary"
                    onClick={() => {
                      setChatOpen(false);
                      setWorkspace(!workspace);
                    }}
                  >
                    {workspace
                      ? "Hide workspace"
                      : `Open ${district === "hq" ? "HQ" : "workspace"}`}
                    <ArrowRight size={16} />
                  </button>
                </div>
              )}
            </div>
            {!district && (
              <section className="city-stats" aria-label="Source overview">
                <span className="city-pm-anchor" ref={setChatAnchor} />
                <div>
                  <span>Inventory codes</span>
                  <strong>{feed.stock.length.toLocaleString()}</strong>
                </div>
                <div>
                  <span>Order observations</span>
                  <strong>{feed.orderRows.toLocaleString()}</strong>
                </div>
                <div>
                  <span>Campaigns</span>
                  <strong>{feed.campaignCount}</strong>
                </div>
                <Link to="/mock-data">
                  <Database size={16} />
                  <span>
                    {String(sandbox.document?.data.hour ?? 1).padStart(2, "0")}
                    :00 · Source clock
                  </span>
                  <ArrowRight size={16} />
                </Link>
              </section>
            )}
            <div className="city-camera">
              <select aria-label="Explore landmarks" defaultValue="" onChange={e => {
                const place = e.currentTarget.value as Landmark;
                enter(null); worldRef.current?.focusLandmark(place); e.currentTarget.value = "";
              }}>
                <option value="" disabled>Explore</option>
                <option value="river">River & bridge</option>
                <option value="bitexco">Bitexco</option>
                <option value="landmark81">Landmark 81</option>
              </select>
              <button
                aria-label="Reset camera"
                title="Reset camera"
                onClick={() => worldRef.current?.focus(district)}
              >
                <RotateCcw />
              </button>
              <button
                aria-label="Zoom in"
                title="Zoom in"
                onClick={() => worldRef.current?.zoom(0.85)}
              >
                <Plus />
              </button>
              <button
                aria-label="Zoom out"
                title="Zoom out"
                onClick={() => worldRef.current?.zoom(1.15)}
              >
                <Minus />
              </button>
            </div>
            {workspace && district && !chatOpen && (
              <Sheet open onOpenChange={setWorkspace} modal={mobile}>
                <SheetContent
                  showCloseButton={false}
                  overlay={mobile}
                  className={`district-workspace city-sheet ${district === "hq" ? "hq-workspace" : ""}`}
                  aria-label={`${DISTRICTS.find((d) => d.id === district)?.name} workspace`}
                >
                  <header>
                    <SheetTitle>
                      {DISTRICTS.find((d) => d.id === district)?.name}
                    </SheetTitle>
                    {district === "hq" && (
                      <div className="workspace-tabs">
                        <button
                          aria-pressed={hqTab === "overview"}
                          onClick={() => setHqTab("overview")}
                        >
                          Overview
                        </button>
                        <button
                          aria-pressed={hqTab === "compare"}
                          onClick={() => setHqTab("compare")}
                        >
                          Comparisons
                        </button>
                      </div>
                    )}
                    <button
                      className="city-icon"
                      aria-label="Close workspace"
                      onClick={() => setWorkspace(false)}
                    >
                      <X />
                    </button>
                  </header>
                  <div className="district-workspace-body">
                    {district === "hq" ? (
                      hqTab === "overview" ? (
                        <ShiftOverviewPage />
                      ) : (
                        <CompareIndexPage />
                      )
                    ) : district === "warehouse" ? (
                      <>
                        <div className="warehouse-title">
                          <Link to="/mock-data">
                            Manage sources
                            <ArrowRight size={16} />
                          </Link>
                        </div>
                        <ProductLabels
                          sourceRevision={sources.document?.revision ?? 0}
                          inventory={feed.stock}
                          blocked={!sources.document || !!sources.error}
                          selectedCode={selectedStock ?? undefined}
                        />
                      </>
                    ) : district === "ads" ? (
                      <>
                        <h3>Platform buildings</h3>
                        <div className="platform-grid">
                          {PLATFORM_IDS.map((p) => (
                            <article
                              key={p}
                              className={`platform-card platform-card-${p}`}
                            >
                              <span className={`platform-logo brand-${p}`}>
                                <img
                                  src={PLATFORMS[p].logo}
                                  alt=""
                                  width={32}
                                  height={32}
                                />
                              </span>
                              <h4>{PLATFORMS[p].name}</h4>
                              <p className="platform-signature">
                                {PLATFORMS[p].signature}
                              </p>
                              <button
                                aria-label={
                                  p === "shopee"
                                    ? "Shopee Ads building is built"
                                    : `${viewSettings.platforms.includes(p) ? "Remove" : "Add"} ${PLATFORMS[p].name} building`
                                }
                                disabled={!settings.document || savingSettings || p === "shopee"}
                                onClick={() =>
                                  void setSettings({
                                    ...viewSettings,
                                    platforms: viewSettings.platforms.includes(
                                      p,
                                    )
                                      ? viewSettings.platforms.filter(
                                          (v) => v !== p,
                                        )
                                      : [...viewSettings.platforms, p],
                                  })
                                }
                              >
                                {viewSettings.platforms.includes(p) ? (
                                  <>
                                    {p === "shopee" ? (
                                      <Check size={16} />
                                    ) : (
                                      <Minus size={16} />
                                    )}
                                    {p === "shopee"
                                      ? "Built"
                                      : "Remove building"}
                                  </>
                                ) : (
                                  <>
                                    <Plus size={16} />
                                    Add building
                                  </>
                                )}
                              </button>
                            </article>
                          ))}
                        </div>
                        <p className="workspace-note">
                          Building presence is a workspace setting. Connected
                          campaign data comes through the backend.
                        </p>
                        <Link className="btn-secondary" to="/compare">
                          Campaign comparisons
                          <ArrowRight size={16} />
                        </Link>
                      </>
                    ) : (
                      <>
                        <h3>Order observations</h3>
                        <p>
                          {feed.orderRows.toLocaleString()} rows through the
                          shared source clock.
                        </p>
                        <Link to="/mock-data">
                          Inspect order sources
                          <ArrowRight size={16} />
                        </Link>
                      </>
                    )}
                  </div>
                </SheetContent>
              </Sheet>
            )}
            {tour !== null && (
              <Tutorial step={tour} onStep={tourStep} onFinish={finishTour}
                actionDisabled={TOUR[tour]?.action === "appearance" && (!settings.document || savingSettings)}
                onAction={() => {
                  if (TOUR[tour]?.action === "river") worldRef.current?.focusRiver();
                  else void setSettings({ ...viewSettings, appearance: appearance === "night" ? "morning" : "night" });
                }} />
            )}
          </>
        )}
      </main>
      <footer className="city-footer">
        <span>Sandbox data · Motion illustrates observations</span>
        <span>
          {sources.document
            ? `Source revision ${sources.document.revision}`
            : "Loading account"}
        </span>
      </footer>
      <CityChat
        launcherAnchor={chatAnchor}
        updates={updates}
        onOpenChange={setChatOpen}
        open={chatOpen}
      />
      <div className="monitor-live sr-only" role="status" aria-live="polite">
        {updates.toast?.title ?? ""}
      </div>
      <dialog
        ref={settingsDialog}
        className="city-settings"
        onCancel={closeSettings}
      >
        <header>
          <div>
            <h2>View settings</h2>
          </div>
          <button aria-label="Close settings" onClick={closeSettings}>
            <X />
          </button>
        </header>
        <section>
          <h3>
            <Radio size={18} />
            Signal core
          </h3>
          <label>
            <span>Commerce signals</span>
            <input
              type="checkbox"
              checked={viewSettings.signals}
              disabled={!settings.document || savingSettings}
              onChange={(e) =>
                void setSettings({ ...viewSettings, signals: e.target.checked })
              }
            />
          </label>
          <p>
            Intermittent pulses appear when source observations are available.
          </p>
        </section>
        <section>
          <h3>Rendering</h3>
          <label><span>Appearance</span><select aria-label="Appearance" value={appearance}
            disabled={!settings.document || savingSettings}
            onChange={e => void setSettings({ ...viewSettings, appearance: e.target.value as ViewSettings["appearance"] })}>
            <option value="morning">Morning</option><option value="night">Night</option>
          </select></label>
          <label>
            <span>City motion</span>
            <input
              type="checkbox"
              checked={viewSettings.motion}
              disabled={!settings.document || savingSettings}
              onChange={(e) =>
                void setSettings({ ...viewSettings, motion: e.target.checked })
              }
            />
          </label>
          <label>
            <span>Render quality</span>
            <select
              value={viewSettings.quality}
              disabled={!settings.document || savingSettings}
              onChange={(e) =>
                void setSettings({
                  ...viewSettings,
                  quality: e.target.value as ViewSettings["quality"],
                })
              }
            >
              <option value="balanced">Balanced</option>
              <option value="high">High</option>
            </select>
          </label>
        </section>
        <section>
          <h3>Workspace</h3>
          <Link onClick={closeSettings} to="/mock-data">
            <Database size={18} />
            Data Studio
            <ArrowRight size={16} />
          </Link>
          <span className="settings-sync">
            {connected
              ? "Connected to your account"
              : "Reconnecting to account…"}
          </span>
        </section>
        {(error || settings.error) && (
          <p role="alert" className="studio-error">
            {error || settings.error}
          </p>
        )}
      </dialog>
      <RouteSteering
        locationKey={location.key}
        ready={ready}
        onDistrict={(id) => {
          enterRef.current(id);
          if (params.has("stock")) setWorkspace(true);
        }}
        onTour={() => tourStep(0)}
      />
    </div>
  );
}
function RouteSteering({
  locationKey,
  ready,
  onDistrict,
  onTour,
}: {
  locationKey: string;
  ready: boolean;
  onDistrict: (id: District) => void;
  onTour: () => void;
}) {
  const callbacks = useRef({ onDistrict, onTour });
  useEffect(() => {
    callbacks.current = { onDistrict, onTour };
  }, [onDistrict, onTour]);
  useEffect(() => {
    if (!ready) return;
    const params = new URLSearchParams(window.location.search),
      district = params.get("district");
    if (DISTRICTS.some((d) => d.id === district))
      callbacks.current.onDistrict(district as District);
    if (params.get("tour") === "replay") callbacks.current.onTour();
  }, [locationKey, ready]);
  return null;
}
