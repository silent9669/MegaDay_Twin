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
import { sourceView } from "../studio/source-view";
import { DataStudio } from "../studio/DataStudio";
import { ShiftOverviewPage } from "../pages/ShiftOverviewPage";
import { CompareIndexPage } from "../pages/compare/ComparePage";
import { CityChat } from "./CityChat";
import { PLATFORM_IDS, PLATFORMS } from "./platforms";
import type { createCity, District, WorldState } from "./world";
import "./city.css";
const DISTRICTS = [
  { id: "hq", name: "MegaDay HQ", icon: Building2 },
  { id: "warehouse", name: "Warehouse", icon: Warehouse },
  { id: "ads", name: "Ads Platform", icon: Megaphone },
  { id: "homes", name: "Residential district", icon: Home },
] as const;
function Mark() {
  return (
    <span className="city-mark" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}
const TOUR = [
  {
    place: null,
    title: "Your commerce city",
    text: "Explore the districts or use the sidebar. The city follows your account’s six source files.",
  },
  {
    place: "warehouse",
    title: "Start with your stock",
    text: "Open Warehouse to inspect source inventory by internal code and timestamp. Unknown and negative stock stay visible for review.",
  },
  {
    place: "ads",
    title: "Connect the ad district",
    text: "Shopee Ads is the default building. Add another platform here; campaign decisions still belong to the backend.",
  },
  {
    place: "hq",
    title: "Bring the team together",
    text: "HQ opens a full-size workspace with your overview and comparisons. Every campaign change needs a human.",
  },
  {
    place: null,
    title: "Shape the view",
    text: "View settings holds Signal core, animation and quality. The compact sidebar keeps these controls within reach.",
  },
  {
    place: null,
    title: "Your sources, one workspace",
    text: "Data Studio loads the six repository sources. Import exports, review format logs and move the shared sandbox clock there.",
  },
  {
    place: null,
    title: "Ask your PM",
    text: "Open the assistant from any screen. It can explain the workspace, while verified decisions remain with the backend tools.",
  },
] as const;
export function CityApp({ studio = false }: { studio?: boolean }) {
  const [chatAnchor, setChatAnchor] = useState<HTMLSpanElement | null>(null);
  const connected = useAccountEvents();
  const settings = useAccountDocument<ViewSettings>("settings"),
    sources = useAccountDocument<SourcePack>("sources"),
    sandbox = useAccountDocument<Sandbox>("sandbox");
  const viewSettings = settings.document?.data ?? defaultSettings;
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
    motion: viewSettings.motion && !reduced.current,
    orders: feed.orderRows,
    adRows: feed.adRows,
    occupancy: feed.stock.length
      ? feed.stock.filter((s) => (s.stock ?? 0) > 0).length / feed.stock.length
      : 0,
  };
  const latest = useRef(state);
  latest.current = state;
  const enter = (next: District | null) => {
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
      if (e.key === "Escape") {
        if (tour !== null) finishTour();
        else if (workspace) setWorkspace(false);
        else enter(null);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [workspace, tour]);
  const setSettings = async (next: ViewSettings) => {
    if (!settings.document) return;
    try {
      await settings.save(next, settings.document.revision);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Settings were not saved.");
      await settings.reload();
    }
  };
  function finishTour() {
    setTour(null);
    try {
      localStorage.setItem("hackathon.city.tour", "complete");
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
        if (!localStorage.getItem("hackathon.city.tour")) setTour(0);
      } catch {
        /* Replay remains available. */
      }
    }, 100);
    return () => clearTimeout(initial);
  }, [ready, studio]);
  const closeSettings = () => {
    const next = new URLSearchParams(params);
    next.delete("panel");
    setParams(next);
  };
  return (
    <div
      className={`city-app ${studio ? "city-studio" : ""}`}
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
          <Mark />
          <span>
            MegaDay <b>Twin</b>
          </span>
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
          <span className={`connection-dot ${connected ? "connected" : ""}`} />
          <span>{connected ? "Account synced" : "Connecting…"}</span>
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
      <main id="city-main" className="city-main">
        {studio ? (
          <DataStudio />
        ) : (
          <>
            <div ref={host} className="city-canvas" />
            {!ready && (
              <div className="city-loading" role="status">
                <Mark />
                <span>Opening your city…</span>
              </div>
            )}
            {failure && (
              <p className="city-failure" role="status">
                {failure}
              </p>
            )}
            <div className="city-scene-title">
              <span className="eyebrow">SENSE → PLAN → CONTROL</span>
              <h1>
                {district
                  ? DISTRICTS.find((d) => d.id === district)?.name
                  : "Commerce city"}
              </h1>
              <span>
                {district === "warehouse"
                  ? `${feed.stock.length} source inventory codes`
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
                    onClick={() => setWorkspace(!workspace)}
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
            {workspace && district && (
              <section
                className="district-workspace"
                aria-label={`${DISTRICTS.find((d) => d.id === district)?.name} workspace`}
              >
                <header>
                  <h2>{DISTRICTS.find((d) => d.id === district)?.name}</h2>
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
                        <h3>Source inventory</h3>
                        <Link to="/mock-data">
                          Manage sources
                          <ArrowRight size={16} />
                        </Link>
                      </div>
                      <p className="workspace-note">
                        Internal warehouse codes · source snapshots · backend
                        SKU matching required
                      </p>
                      <div className="warehouse-table">
                        <table>
                          <thead>
                            <tr>
                              <th>Internal code</th>
                              <th>Product</th>
                              <th>Available units</th>
                              <th>Source timestamp</th>
                            </tr>
                          </thead>
                          <tbody>
                            {feed.stock.map((row) => (
                              <tr key={row.code}>
                                <td>
                                  <code>{row.code}</code>
                                </td>
                                <td>{row.name}</td>
                                <td>
                                  {row.stock === null ? (
                                    <span className="stock-unknown">
                                      Unknown
                                    </span>
                                  ) : (
                                    row.stock.toLocaleString()
                                  )}
                                </td>
                                <td>{row.asOf}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {!feed.stock.length && (
                          <p>
                            No inventory observations at this clock. Change the
                            source clock in Data Studio.
                          </p>
                        )}
                      </div>
                    </>
                  ) : district === "ads" ? (
                    <>
                      <h3>Platform buildings</h3>
                      <div className="platform-grid">
                        {PLATFORM_IDS.map(
                          (p) => (
                            <article key={p} className={`platform-card platform-card-${p}`}>
                              <span className={`platform-logo brand-${p}`}>
                                <img src={PLATFORMS[p].logo} alt="" width={32} height={32} />
                              </span>
                              <h4>{PLATFORMS[p].name}</h4>
                              <p className="platform-signature">{PLATFORMS[p].signature}</p>
                              <button
                                aria-label={p === "shopee" ? "Shopee Ads building is built" : `${viewSettings.platforms.includes(p) ? "Remove" : "Add"} ${PLATFORMS[p].name} building`}
                                disabled={!settings.document || p === "shopee"}
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
                                    {p === "shopee" ? <Check size={16} /> : <Minus size={16} />}
                                    {p === "shopee" ? "Built" : "Remove building"}
                                  </>
                                ) : (
                                  <>
                                    <Plus size={16} />
                                    Add building
                                  </>
                                )}
                              </button>
                            </article>
                          ),
                        )}
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
              </section>
            )}
            {tour !== null && (
              <section className="city-tour" aria-label="City tutorial">
                <header>
                  <span>
                    YOUR CITY / {tour + 1} OF {TOUR.length}
                  </span>
                  <button aria-label="Close tutorial" onClick={finishTour}>
                    <X size={18} />
                  </button>
                </header>
                <h2>{TOUR[tour]!.title}</h2>
                <p>{TOUR[tour]!.text}</p>
                <footer>
                  <button
                    disabled={tour === 0}
                    onClick={() => tourStep(tour - 1)}
                  >
                    Back
                  </button>
                  <div>
                    {TOUR.map((_, i) => (
                      <button
                        key={i}
                        aria-label={`Tutorial step ${i + 1}`}
                        aria-current={tour === i ? "step" : undefined}
                        onClick={() => tourStep(i)}
                      />
                    ))}
                  </div>
                  <button
                    className="primary"
                    onClick={() =>
                      tour === TOUR.length - 1
                        ? finishTour()
                        : tourStep(tour + 1)
                    }
                  >
                    {tour === TOUR.length - 1 ? "Explore" : "Next"}
                    <ArrowRight size={16} />
                  </button>
                </footer>
              </section>
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
      <CityChat launcherAnchor={chatAnchor} />
      <dialog
        ref={settingsDialog}
        className="city-settings"
        onCancel={closeSettings}
      >
        <header>
          <div>
            <span className="eyebrow">City preferences</span>
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
              disabled={!settings.document}
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
          <label>
            <span>City motion</span>
            <input
              type="checkbox"
              checked={viewSettings.motion}
              disabled={!settings.document}
              onChange={(e) =>
                void setSettings({ ...viewSettings, motion: e.target.checked })
              }
            />
          </label>
          <label>
            <span>Render quality</span>
            <select
              value={viewSettings.quality}
              disabled={!settings.document}
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
        onDistrict={(id) => enterRef.current(id)}
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
