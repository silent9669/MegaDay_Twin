import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePlaybackHour } from "../../studio/use-playback-hour";
import type { SourcePack } from "../../studio/sources";
import type { Doc, Sandbox } from "../../studio/workspace-types";
import { snapshot, WorkspaceMonitor, type ChangeNotice } from "./changes";

export function useWorkspaceMonitor(
  sources: Doc<SourcePack> | null,
  sandbox: Doc<Sandbox> | null,
  connected: boolean,
) {
  const monitor = useRef(new WorkspaceMonitor());
  const interrupted = useRef(false);
  const wasConnected = useRef(false);
  const [notices, setNotices] = useState<ChangeNotice[]>([]);
  const [read, setRead] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<ChangeNotice | null>(null);
  const hour = usePlaybackHour(sandbox?.data);
  useEffect(() => {
    const changed = (event: Event) => {
      const detail = (event as CustomEvent<{ revision: string; dataAsOf: string | null }>).detail;
      const notice: ChangeNotice = {
        id: `backend-CD002-${detail.revision}`, kind: "campaigns", backendCampaign: true,
        title: "CD002 · Có cập nhật từ backend",
        detail: "Dữ liệu hoặc mục tiêu đã đổi. Mở HQ để cập nhật và đề xuất lại.",
        before: null, after: detail.revision, important: true, simulation: false,
        reconnected: false, observedAt: new Date().toISOString(), href: "/campaign-hq",
        // Backend notices link to HQ; never send this as source-workspace evidence.
        reference: { kind: "campaigns", sourceRevision: 0, sandboxRevision: 0,
          hour: 0, before: null, after: detail.revision },
      };
      setNotices(old => [notice, ...old.filter(n => !n.backendCampaign)].slice(0, 100));
      setToast(notice);
    };
    window.addEventListener("workspace:campaign-update", changed);
    return () => window.removeEventListener("workspace:campaign-update", changed);
  }, []);
  const next = useMemo(
    () => (sources && sandbox ? snapshot(sources, { ...sandbox, data: { ...sandbox.data, hour } }) : null),
    [sources, sandbox, hour],
  );
  useEffect(() => {
    if (wasConnected.current && !connected) interrupted.current = true;
    if (connected) wasConnected.current = true;
  }, [connected]);
  useEffect(() => {
    if (!next || !connected) return;
    const timer = setTimeout(() => {
      const changes = monitor.current.observe(next, interrupted.current);
      interrupted.current = false;
      if (!changes.length) return;
      setNotices((old) => [...[...changes].reverse(), ...old].slice(0, 100));
      const important = changes.find((n) => n.important);
      if (important) setToast(important);
    }, 1000);
    return () => clearTimeout(timer);
  }, [next, connected]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 8000);
    return () => clearTimeout(timer);
  }, [toast]);
  const markRead = useCallback(
    () => setRead(new Set(notices.map((n) => n.id))),
    [notices],
  );
  return {
    notices,
    toast,
    unread: notices.filter((n) => !read.has(n.id)).length,
    dismissToast: () => setToast(null),
    markRead,
    readAll: () => {
      setRead(new Set());
      setNotices([]);
      setToast(null);
    },
    dismiss: (id: string) => {
      setNotices((old) => old.filter((n) => n.id !== id));
      setToast((old) => (old?.id === id ? null : old));
    },
  };
}
export type WorkspaceUpdates = ReturnType<typeof useWorkspaceMonitor>;
