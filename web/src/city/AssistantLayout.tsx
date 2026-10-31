import { useCallback, useState } from "react";
import { Outlet, useLocation, useSearchParams } from "react-router";
import { CityChat } from "./CityChat";
import { AssistantContext } from "./assistant-context";
import { useAccountDocument, useAccountEvents } from "../studio/account";
import type { Sandbox } from "../studio/workspace-types";
import type { SourcePack } from "../studio/sources";
import { useWorkspaceMonitor } from "./monitor/useWorkspaceMonitor";
export function AssistantLayout() {
  const [localOpen, setLocalOpen] = useState(false),
    [width, setWidth] = useState(400),
    [workspaceOpen, setWorkspaceOpen] = useState(false),
    [launcherAnchor, setLauncherAnchor] = useState<HTMLElement | null>(null);
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const open = localOpen || params.get("assistant") === "open";
  const setOpen = useCallback(
    (value: boolean) => {
      setLocalOpen(value);
      if (params.has("assistant")) {
        const query = new URLSearchParams(params);
        query.delete("assistant");
        setParams(query, { replace: true });
      }
    },
    [params, setParams],
  );
  const connected = useAccountEvents(),
    sources = useAccountDocument<SourcePack>("sources"),
    sandbox = useAccountDocument<Sandbox>("sandbox");
  const updates = useWorkspaceMonitor(
    sources.document,
    sandbox.document,
    connected,
  );
  return (
    <AssistantContext.Provider
      value={{
        open,
        setOpen,
        width,
        setWidth,
        workspaceOpen,
        setWorkspaceOpen,
        setLauncherAnchor,
        connected,
        unread: updates.unread,
      }}
    >
      <Outlet />
      <div className="monitor-live sr-only" role="status" aria-live="polite">
        {updates.toast?.title ?? ""}
      </div>
      <CityChat
        context={{
          screen:
            location.pathname === "/mock-data"
              ? "data-studio"
              : location.pathname.slice(1),
          ...(params.get("campaign")
            ? { campaignId: params.get("campaign")! }
            : {}),
        }}
        open={open}
        showLauncher={location.pathname !== "/mock-data"}
        onOpenChange={setOpen}
        launcherAnchor={launcherAnchor}
        updates={updates}
        workspaceOpen={workspaceOpen}
        onWidthChange={setWidth}
      />
    </AssistantContext.Provider>
  );
}
