import { Goals } from "./Goals";
import { Campaigns } from "./Campaigns";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { X, Plus } from "lucide-react";
import { useAccountDocument } from "../studio/account";
import { SourceWorkspace } from "../studio/SourceWorkspace";
import { sourceView } from "../studio/source-view";
import type { SourcePack } from "../studio/sources";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../components/ui/tabs";
import { ShiftOverviewPage } from "../pages/ShiftOverviewPage";
import { ComparePage, CompareIndexPage } from "../pages/compare/ComparePage";
import { config } from "../lib/config";
import { HQSelection, type HQTab } from "./selection";
import {
  defaultHQ,
  productKey,
  type HQDocument,
  type TrackedProduct,
  type Worksheet,
} from "./types";
import { ScenarioWorksheet } from "./ScenarioWorksheet";
import { ProductDetails } from "./ProductDetails";
import { analyzeWithAI } from "../city/analysis-context";
import "./hq.css";
function logged(
  next: HQDocument,
  product: string,
  action: string,
  detail = "",
): HQDocument {
  return {
    ...next,
    activity: [
      {
        id: crypto.randomUUID(),
        product,
        action,
        detail,
        at: new Date().toISOString(),
      },
      ...next.activity,
    ].slice(0, 200),
  };
}
const tabs: [HQTab, string][] = [
  ["overview", "Overview"],
  ["details", "SKU Details"],
  ["comparisons", "Comparisons"],
  ["campaigns", "Campaigns"],
  ["goals", "Goals"],
  ["activity", "Activity Logs"],
];
export function HQWorkspace({
  pack,
  revision,
}: {
  pack: SourcePack | null;
  revision: number;
}) {
  const account = useAccountDocument<HQDocument>("hq"),
    data = account.document?.data ?? defaultHQ;
  const [params, setParams] = useSearchParams();
  const requested = params.get("hq");
  const tab: HQTab = tabs.some(([key]) => key === requested)
    ? (requested as HQTab)
    : "overview";
  const [mode, setMode] = useState<"workspace" | "model">(
      params.get("mode") === "model" ? "model" : "workspace",
    ),
    [search, setSearch] = useState(""),
    [page, setPage] = useState(0),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notes, setNotes] = useState<Record<string, string>>({});
  const [worksheetDrafts, setWorksheetDrafts] = useState<
    Record<string, Worksheet>
  >({});
  const inventory = useMemo(() => sourceView(pack, 24).stock, [pack]);
  const active =
    data.tracked.find((p) => productKey(p) === data.active) ?? null;
  const activeKey = active ? productKey(active) : "";
  const note = notes[activeKey] ?? "";
  const setNote = (value: string) =>
    setNotes((previous) => ({ ...previous, [activeKey]: value }));
  const saving = useRef(false);
  const { document, reload, save: saveDocument } = account;
  const changeTab = useCallback(
    (next: HQTab, clearTarget = false) => {
      setParams(
        (previous) => {
          const query = new URLSearchParams(previous);
          query.set("district", "hq");
          query.set("hq", next);
          query.delete("sku");
          if (clearTarget) {
            query.delete("campaign");
            query.delete("rec");
          }
          return query;
        },
        { replace: true },
      );
    },
    [setParams],
  );
  const save = useCallback(
    async (next: HQDocument) => {
      if (!document || saving.current) return false;
      saving.current = true;
      setBusy(true);
      setError("");
      try {
        await saveDocument(next, document.revision);
        return true;
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not save HQ.");
        await reload();
        return false;
      } finally {
        saving.current = false;
        setBusy(false);
      }
    },
    [document, saveDocument, reload],
  );
  const track = useCallback(
    async (
      product: TrackedProduct,
      next: HQTab = "details",
      deepLink = false,
    ) => {
      const key = productKey(product),
        exists = data.tracked.some((p) => productKey(p) === key);
      if (!exists && data.tracked.length >= 30) {
        setError("You can track up to 30 products. Remove one first.");
        return;
      }
      if (
        await save(
          logged(
            {
              ...data,
              tracked: exists ? data.tracked : [...data.tracked, product],
              active: key,
            },
            key,
            exists ? "Opened product" : "Started tracking",
          ),
        )
      )
        changeTab(next, !deepLink);
    },
    [data, save, changeTab],
  );
  function remove(product: TrackedProduct) {
    const key = productKey(product),
      tracked = data.tracked.filter((p) => productKey(p) !== key),
      worksheets = { ...data.worksheets };
    delete worksheets[key];
    void save(
      logged(
        {
          ...data,
          tracked,
          worksheets,
          active:
            data.active === key
              ? tracked[0]
                ? productKey(tracked[0])
                : null
              : data.active,
        },
        key,
        "Stopped tracking",
      ),
    );
  }
  const handledLink = useRef<string | null>(null);
  const requestedSku = params.get("sku"),
    requestedMode = params.get("mode");
  useEffect(() => {
    if (!requestedSku) {
      handledLink.current = null;
      return;
    }
    if (!account.document || busy || handledLink.current === requestedSku)
      return;
    handledLink.current = requestedSku;
    void track(
      {
        sku: requestedSku,
        name: requestedSku,
        mode: requestedMode === "model" ? "model" : "workspace",
      },
      tab,
      true,
    );
  }, [requestedSku, requestedMode, account.document, busy, track, tab]);
  const products = inventory.filter((p) =>
    (p.name + " " + p.code).toLowerCase().includes(search.toLowerCase()),
  );
  const currentPage = Math.min(
    page,
    Math.max(0, Math.ceil(products.length / 25) - 1),
  );
  const selectedStock = inventory.find((p) => p.code === active?.sku);
  const saveWorksheet = (worksheet: Worksheet) =>
    void save(
      logged(
        { ...data, worksheets: { ...data.worksheets, [activeKey]: worksheet } },
        activeKey,
        "Saved scenario assumptions",
        JSON.stringify(worksheet),
      ),
    );
  return (
    <HQSelection.Provider
      value={{
        product: active,
        open: track,
        tab: changeTab,
        log: (action, detail) =>
          void save(logged(data, activeKey, action, detail)),
      }}
    >
      <section className="hq-workspace" aria-label="HQ product workspace">
        {(error || account.error) && (
          <p role="alert" className="studio-error">
            {error || account.error}
          </p>
        )}
        <div className="hq-tracked" aria-label="Tracked products">
          {data.tracked.map((product) => (
            <div
              key={productKey(product)}
              data-active={productKey(product) === activeKey}
            >
              <button
                disabled={busy}
                onClick={() =>
                  void save({ ...data, active: productKey(product) }).then(
                    (ok) => {
                      if (ok) changeTab(tab, true);
                    },
                  )
                }
                aria-label={`Select ${product.sku}`}
                title={product.name}
              >
                {product.sku}
                <small>
                  {product.mode === "model" ? "Model" : "Workspace"}
                </small>
              </button>
              <button
                disabled={busy}
                aria-label={`Stop tracking ${product.sku}`}
                onClick={() => remove(product)}
              >
                <X size={14} />
              </button>
            </div>
          ))}
          <button className="hq-add" onClick={() => changeTab("overview")}>
            <Plus size={15} />
            Track product
          </button>
        </div>
        <Tabs
          value={tab}
          onValueChange={(value) => changeTab(value as HQTab)}
          className="hq-tabs"
        >
          <TabsList variant="line" aria-label="HQ sections">
            {tabs.map(([key, label]) => (
              <TabsTrigger key={key} value={key}>
                {label}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value="overview">
            <div className="hq-overview-toolbar">
              <h2>Overview</h2>
              <label>
                Data
                <select
                  aria-label="HQ data source"
                  value={mode}
                  onChange={(e) => setMode(e.target.value as typeof mode)}
                >
                  <option value="workspace">Workspace data</option>
                  <option value="model">
                    {config.useFixtures ? "Model demo" : "Decision backend"}
                  </option>
                </select>
              </label>
            </div>
            {mode === "model" ? (
              <>
                <p className="hq-provenance">
                  {config.useFixtures
                    ? "Saved demo results · independent of uploaded sources"
                    : "Connected decision backend"}
                </p>
                <ShiftOverviewPage />
              </>
            ) : (
              <>
                <details className="hq-source-summary">
                  <summary>
                    Source overview · {pack?.files.length ?? 0} tables
                  </summary>
                  <SourceWorkspace pack={pack} district="hq" />
                </details>
                <div className="hq-product-toolbar">
                  <h3>Products</h3>
                  <label>
                    <span className="sr-only">Search HQ products</span>
                    <input
                      placeholder="Search code or name"
                      value={search}
                      onChange={(e) => {
                        setSearch(e.target.value);
                        setPage(0);
                      }}
                    />
                  </label>
                </div>
                {!products.length ? (
                  <p className="hq-empty">
                    {inventory.length
                      ? "No matching products."
                      : "Import an inventory table in Data Studio to track products."}
                  </p>
                ) : (
                  <div className="hq-table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Product</th>
                          <th>Available units</th>
                          <th>Updated</th>
                          <th>Workspace</th>
                        </tr>
                      </thead>
                      <tbody>
                        {products
                          .slice(currentPage * 25, (currentPage + 1) * 25)
                          .map((product) => (
                            <tr key={product.code}>
                              <td>
                                <strong>{product.name}</strong>
                                <small>{product.code}</small>
                              </td>
                              <td>{product.stock ?? "—"}</td>
                              <td>{product.asOf || "—"}</td>
                              <td>
                                <button
                                  disabled={busy || !account.document}
                                  onClick={() =>
                                    track({
                                      sku: product.code,
                                      name: product.name,
                                      mode: "workspace",
                                    })
                                  }
                                >
                                  {data.tracked.some(
                                    (p) =>
                                      p.mode === "workspace" &&
                                      p.sku === product.code,
                                  )
                                    ? "Open"
                                    : "Track product"}
                                </button>
                                <button
                                  disabled={busy || !account.document}
                                  onClick={() =>
                                    track(
                                      {
                                        sku: product.code,
                                        name: product.name,
                                        mode: "workspace",
                                      },
                                      "comparisons",
                                    )
                                  }
                                >
                                  Compare
                                </button>
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {products.length > 25 && (
                  <nav className="hq-pagination" aria-label="HQ product pages">
                    <button
                      disabled={currentPage === 0}
                      onClick={() => setPage(currentPage - 1)}
                    >
                      Previous
                    </button>
                    <span>
                      {currentPage + 1} / {Math.ceil(products.length / 25)}
                    </span>
                    <button
                      disabled={(currentPage + 1) * 25 >= products.length}
                      onClick={() => setPage(currentPage + 1)}
                    >
                      Next
                    </button>
                  </nav>
                )}
                <div className="hq-overview-links">
                  <Link to="/mock-data">Data Studio</Link>
                  <button onClick={() => changeTab("goals")}>Plan a goal</button>
                </div>
              </>
            )}
          </TabsContent>
          <TabsContent value="goals">
            <Goals key={activeKey} product={active} />
          </TabsContent>
          <TabsContent value="campaigns">
            <Campaigns pack={pack} revision={revision} />
          </TabsContent>
          {(["details", "comparisons", "activity"] as const).map((key) => (
            <TabsContent key={key} value={key}>
              {key === "comparisons" && params.get("campaign") ? (
                <ComparePage />
              ) : !active ? (
                <div className="hq-empty">
                  <h3>Choose a product to work on</h3>
                  <button
                    className="btn-primary"
                    onClick={() => changeTab("overview")}
                  >
                    Browse products
                  </button>
                  {key === "comparisons" && <CompareIndexPage />}
                </div>
              ) : (
                <>
                  <div className="hq-product-heading">
                    <h2>{active.name}</h2>
                    <span>
                      {active.sku} ·{" "}
                      {active.mode === "model"
                        ? config.useFixtures
                          ? "Model snapshot"
                          : "Decision backend"
                        : `Workspace revision ${revision}`}
                    </span>
                  </div>
                  {key === "details" && (
                    <ProductDetails
                      product={active}
                      pack={pack}
                      stock={selectedStock}
                      revision={revision}
                    />
                  )}
                  {key === "comparisons" && (
                    <>
                      <ScenarioWorksheet
                        key={activeKey}
                        product={active}
                        saved={
                          worksheetDrafts[activeKey] ??
                          data.worksheets[activeKey]
                        }
                        onChange={(value) =>
                          setWorksheetDrafts((previous) => ({
                            ...previous,
                            [activeKey]: value,
                          }))
                        }
                        onSave={saveWorksheet}
                        busy={busy}
                      />
                      {active.mode === "model" || !config.useFixtures ? (
                        <details className="hq-model-comparison">
                          <summary>
                            Decision model · actions, reasons & evidence
                          </summary>
                          <ComparePage key={activeKey} sku={active.sku} />
                        </details>
                      ) : (
                        <p className="hq-provenance">
                          Model simulations require a connected decision
                          backend. This worksheet uses your assumptions only.
                        </p>
                      )}
                    </>
                  )}
                  {key === "activity" && (
                    <section className="hq-activity">
                      <header>
                        <h3>Workspace activity</h3>
                        <button
                          className="btn-secondary"
                          onClick={() =>
                            analyzeWithAI(
                              {
                                title: "Product activity",
                                source: "workspace",
                                sku: active.sku,
                                data: JSON.stringify(
                                  data.activity
                                    .filter((a) => a.product === activeKey)
                                    .slice(0, 30),
                                ),
                              },
                              "Summarize this product’s workspace activity in English. These are user-recorded workspace events, not verified external transactions. Highlight saved assumptions and open questions.",
                            )
                          }
                        >
                          Analyze with AI
                        </button>
                      </header>
                      <form
                        onSubmit={(e) => {
                          e.preventDefault();
                          if (note.trim()) {
                            void save(
                              logged(data, activeKey, "Note", note.trim()),
                            ).then((ok) => {
                              if (ok) setNote("");
                            });
                          }
                        }}
                      >
                        <label>
                          Add a note
                          <textarea
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            maxLength={2000}
                            rows={2}
                          />
                        </label>
                        <button
                          className="btn-secondary"
                          disabled={busy || !note.trim()}
                        >
                          Save note
                        </button>
                      </form>
                      <ol>
                        {data.activity
                          .filter((event) => event.product === activeKey)
                          .map((event) => (
                            <li key={event.id}>
                              <time>
                                {new Date(event.at).toLocaleString("en-GB")}
                              </time>
                              <strong>{event.action}</strong>
                              {event.detail && <p>{event.detail}</p>}
                            </li>
                          ))}
                      </ol>
                    </section>
                  )}
                </>
              )}
            </TabsContent>
          ))}
        </Tabs>
      </section>
    </HQSelection.Provider>
  );
}
