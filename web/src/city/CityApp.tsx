import { AdsStats } from "./AdsStats";
import { HQWorkspace } from "../hq/HQWorkspace";
import { useAssistant } from "./assistant-context";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import {
  ArrowRight,
  Building2,
  Bell,
  Check,
  CircleHelp,
  Database,
  Home,
  Layers3,
  LogOut,
  Megaphone,
  Maximize2,
  Minimize2,
  MessageCircle,
  Minus,
  Moon,
  Sun,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Radio,
  Settings2,
  Warehouse,
  X,
} from "lucide-react";
import {
  useAccountDocument,
  requestJSON,
} from "../studio/account";
import {
  defaultSettings,
  type ViewSettings,
  type Sandbox,
} from "../studio/workspace-types";
import type { SourcePack } from "../studio/sources";
import { SourceWorkspace } from "../studio/SourceWorkspace";
import { ProductLabels } from "../studio/ProductLabels";
import "../studio/studio.css";
import { visibleSources } from "../studio/replay";
import { usePlaybackHour } from "../studio/use-playback-hour";
import { sourceView } from "../studio/source-view";
import { DataStudio } from "../studio/DataStudio";
import { CampaignHQ } from "../campaign/CampaignHQ";
import { useCampaignWatch } from "../campaign/useCampaignWatch";
import { PLATFORM_IDS, PLATFORMS } from "./platforms";
import type { createCity, District, WorldState } from "./world";
import "./city.css";
import { Sheet, SheetContent, SheetTitle } from "../components/ui/sheet";
import { Tutorial } from "./Tutorial";
import { usePanelResize } from "./usePanelResize";
import { TOUR, TOUR_KEY } from "./tutorial-steps";
import { Brand, BrandMark } from "./Brand";
const DISTRICTS = [
  { id: "hq", name: "MegaDay HQ", icon: Building2 },
  { id: "warehouse", name: "Warehouse", icon: Warehouse },
  { id: "ads", name: "Ads Platform", icon: Megaphone },
  { id: "homes", name: "Residential district", icon: Home },
] as const;
export function CityApp({ studio = false, campaignHQ = false }: { studio?: boolean; campaignHQ?: boolean }) {
  const pageWorkspace = studio || campaignHQ;
  const campaignWatch = useCampaignWatch();
  const [mobile, setMobile] = useState(
    () => matchMedia("(max-width: 1023px)").matches,
  );
  useEffect(() => {
    const media = matchMedia("(max-width: 1023px)");
    const update = () => setMobile(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const assistant = useAssistant();
  const { connected, setLauncherAnchor: setChatAnchor, open: chatOpen, setOpen: setChatOpen } = assistant;
  const settings = useAccountDocument<ViewSettings>("settings"),
    sources = useAccountDocument<SourcePack>("sources"),
    sandbox = useAccountDocument<Sandbox>("sandbox");
  const panel = usePanelResize(1080, 560, "right", chatOpen ? assistant.width + 12 : 0);
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
  const hour = usePlaybackHour(sandbox.document?.data);
  const visiblePack = useMemo(() => visibleSources(sources.document?.data ?? null, hour), [sources.document, hour]);
  const feed = useMemo(
    () =>
      sourceView(
        visiblePack,
        hour,
      ),
    [visiblePack, hour],
  );
  const [compact, setCompact] = useState(true),
    [district, setDistrict] = useState<District | null>(null),
    [workspace, setWorkspace] = useState(false),
    [tour, setTour] = useState<number | null>(null),
    [ready, setReady] = useState(false),
    [failure, setFailure] = useState(""),
    [error, setError] = useState("");
  const setWorkspaceOpen=assistant.setWorkspaceOpen;
  const [params, setParams] = useSearchParams();
  useEffect(() => { setWorkspaceOpen(workspace && !pageWorkspace); return () => setWorkspaceOpen(false); }, [workspace, pageWorkspace, setWorkspaceOpen]);
  const settingsOpen = params.get("panel") === "settings";
  const navigate = useNavigate();
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
  useEffect(() => {
    worldRef.current?.setPanelInset(!mobile && workspace ? panel.style?.width ?? 0 : 0);
  }, [mobile, workspace, chatOpen, panel.style?.width, ready]);
  const enter = (next: District | null, tourIndex: number | null = null) => {
    setTour(tourIndex);
    if (mobile) setChatOpen(false);
    setDistrict(next);
    if(next)setParams(previous=>{if(previous.get("district")===next)return previous;const query=new URLSearchParams(previous);query.set("district",next);return query;},{replace:true});
    setWorkspace(next !== null);
    if (!next) setParams(previous=>{const query=new URLSearchParams(previous);for(const key of ["district","hq","stock","sku","campaign","rec","mode"])query.delete(key);return query;},{replace:true});
    worldRef.current?.focus(next);
  };
  const enterRef = useRef(enter);
  enterRef.current = enter;
  useEffect(() => {
    if (pageWorkspace || !host.current) return;
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
  }, [pageWorkspace]);
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
        else enterRef.current(null);
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
    try { localStorage.setItem(TOUR_KEY,"seen"); } catch { /* Device preference is optional. */ }
    enter(TOUR[index]?.place ?? null, index);
  }
  useEffect(() => {
    if (!ready || pageWorkspace) return;
    const initial = setTimeout(() => {
      try {
        if (!localStorage.getItem(TOUR_KEY) && !window.location.search) {
          localStorage.setItem(TOUR_KEY,"seen");
          enterRef.current(TOUR[0]!.place, 0);
        }
      } catch {
        /* Replay remains available. */
      }
    }, 100);
    return () => clearTimeout(initial);
  }, [ready, pageWorkspace]);
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
      className={`city-app ${pageWorkspace ? "city-studio" : ""} ${chatOpen ? "milo-open" : ""}`}
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
          {campaignHQ ? "Campaign CD002" : studio
            ? "Data Studio"
            : district
              ? DISTRICTS.find((d) => d.id === district)?.name
              : "Commerce city"}
        </span>
        <div className="city-account">
          <button className="city-appearance" aria-label="Thông báo Milo"
            onClick={() => window.dispatchEvent(new Event("workspace:updates"))}>
            <Bell size={16} /><span>{assistant.unread ? assistant.unread : "Milo"}</span>
          </button>
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
            aria-current={!pageWorkspace && !district ? "page" : undefined}
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
              aria-current={!pageWorkspace && district === id ? "location" : undefined}
              onClick={() => {
                if (pageWorkspace) navigate(`/city-demo?district=${id}`);
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
              if (pageWorkspace) navigate("/city-demo?tour=replay");
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
        {campaignHQ ? <CampaignHQ watch={campaignWatch} /> : studio ? (
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
                    {String(hour).padStart(2, "0")}
                    :00 · Source clock
                  </span>
                  <ArrowRight size={16} />
                </Link>
              </section>
            )}
            {workspace && district && (!mobile || !chatOpen) && (
              <Sheet open disablePointerDismissal={!mobile} onOpenChange={open => { if (!open) { finishTour(); enter(null); } }} modal={mobile}>
                <SheetContent
                  showCloseButton={false}
                  style={panel.style}
                  overlay={mobile}
                  className={`district-workspace city-sheet ${district === "hq" ? "hq-panel" : ""}`}
                  aria-label={`${DISTRICTS.find((d) => d.id === district)?.name} workspace`}
                >
                  <div className="panel-resize-grip" {...panel.grip} />
                  <header>
                    <SheetTitle>
                      {DISTRICTS.find((d) => d.id === district)?.name}
                    </SheetTitle>
                    <button className="city-icon panel-expand" aria-label={panel.expanded ? "Restore workspace" : "Expand workspace"}
                      onClick={() => panel.setExpanded(!panel.expanded)}>{panel.expanded ? <Minimize2 /> : <Maximize2 />}</button>
                    <button
                      className="city-icon"
                      aria-label="Close workspace"
                      onClick={() => { finishTour(); enter(null); }}
                    >
                      <X />
                    </button>
                  </header>
                  {tour !== null && <Tutorial step={tour} onStep={tourStep} onFinish={finishTour} />}
                  <div className="district-workspace-body">
                    {district === "hq" ? (
                      <>
                        <div className="workspace-tabs">
                          <Link to="/campaign-hq">CD002 · Phân tích và đề xuất <ArrowRight aria-hidden size={16} /></Link>
                        </div>
                        <HQWorkspace pack={visiblePack} revision={sources.document?.revision ?? 0} />
                      </>
                    ) : district === "warehouse" ? (
                      <>
                        <ProductLabels
                          sourceRevision={sources.document?.revision ?? 0}
                          inventory={feed.stock}
                          blocked={!sources.document || !!sources.error}
                          selectedCode={selectedStock ?? undefined}
                        />
                      </>
                    ) : district === "ads" ? (
                      <>
                        <AdsStats pack={visiblePack} revision={sources.document?.revision??0}/>
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
                      <SourceWorkspace pack={visiblePack} district="residential" />
                    )}
                  </div>
                </SheetContent>
              </Sheet>
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
        locationKey={String(studio) + (params.get("district") ?? "") + (params.get("stock") ?? "") + (params.get("tour") ?? "")}
        ready={ready}
        onDistrict={(id) => {
          if(district!==id)enterRef.current(id);
          if (params.has("stock")) setWorkspace(true);
        }}
        onTour={() => { tourStep(0); setParams(previous=>{const query=new URLSearchParams(previous);query.delete("tour");return query;},{replace:true}); }}
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
