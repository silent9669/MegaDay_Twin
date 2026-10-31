import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import type { Doc, WorkspaceKey } from "./workspace-types";
export async function requestJSON<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    headers: { ...options?.headers },
  });
  return responseJSON<T>(response);
}
async function responseJSON<T>(response: Response): Promise<T> {
  let body;
  try { body = await response.json(); }
  catch { throw Object.assign(Error(response.status === 401 ? "Sign in again to continue." : `The server returned an unreadable response (HTTP ${response.status}). Retry shortly.`), { status: response.status }); }
  if (!response.ok)
    throw Object.assign(Error(body.error?.message ?? "Request failed."), {
      status: response.status,
      code: body.error?.code,
    });
  return body as T;
}
export function useAccountEvents() {
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    let socket: WebSocket | undefined,
      timer: ReturnType<typeof setTimeout>,
      stopped = false,
      attempt = 0;
    const notify = (name?: string) =>
      window.dispatchEvent(
        new CustomEvent("workspace:changed", { detail: name }),
      );
    const connect = () => {
      socket = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/workspace/events`,
      );
      socket.onopen = () => {
        attempt = 0;
        setConnected(true);
        notify();
      };
      socket.onmessage = (event) => {
        if (event.data === "pong") return;
        try {
          const data = JSON.parse(event.data);
          notify(data.name);
        } catch {
          /* Invalid notification; reconcile on reconnect. */
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!stopped)
          timer = setTimeout(connect, Math.min(15000, 1000 * 2 ** attempt++));
      };
      socket.onerror = () => socket?.close();
    };
    const focus = () => notify();
    const heartbeat = setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) socket.send("ping");
    }, 30000);
    connect();
    window.addEventListener("focus", focus);
    window.addEventListener("online", focus);
    return () => {
      stopped = true;
      clearTimeout(timer);
      clearInterval(heartbeat);
      socket?.close();
      window.removeEventListener("focus", focus);
      window.removeEventListener("online", focus);
    };
  }, []);
  return connected;
}
// One document/read per mounted workspace, even when City, HQ and Milo all use it.
// Release the cache when its last consumer leaves; no account data is persisted here.
function createDocumentStore(name: WorkspaceKey) {
  let snapshot: { document: Doc<unknown> | null; error: string } = { document: null, error: "" };
  const listeners = new Set<() => void>();
  let pending: Promise<Doc<unknown> | null> | null = null;
  let refreshAgain = false;
  const publish = (next: Doc<unknown> | null, error = "") => {
    const document = next && (!snapshot.document || next.revision > snapshot.document.revision) ? next : snapshot.document;
    if (document === snapshot.document && error === snapshot.error) return;
    snapshot = { document, error };
    listeners.forEach(listener => listener());
  };
  const reload = () => {
    if (pending) return pending;
    pending = (async () => {
      do {
        refreshAgain = false;
        try {
          const previous = snapshot.document;
          const response = await fetch(`/api/workspace/${name}`, {
            cache: "no-store",
            headers: previous ? { "If-None-Match": `"${name}:${previous.revision}"` } : {},
          });
          const next = response.status === 304 && previous ? previous : await responseJSON<Doc<unknown>>(response);
          publish(next);
        } catch (error) {
          publish(null, error instanceof Error ? error.message : "Workspace unavailable.");
        }
        // A save or reconnect during a slow read must not lose the newer revision.
      } while (refreshAgain && listeners.size);
      return snapshot.error ? null : snapshot.document;
    })().finally(() => { pending = null; });
    return pending;
  };
  const refresh = () => {
    if (pending) refreshAgain = true;
    return reload();
  };
  const changed = (event: Event) => {
    const key = (event as CustomEvent).detail;
    if (key && key !== name) return;
    void refresh();
  };
  const store = {
    getSnapshot: () => snapshot,
    publish,
    reload: refresh,
    subscribe(listener: () => void) {
      listeners.add(listener);
      if (listeners.size === 1) {
        stores.set(name, store);
        window.addEventListener("workspace:changed", changed);
        void reload();
      }
      return () => {
        listeners.delete(listener);
        if (!listeners.size) {
          window.removeEventListener("workspace:changed", changed);
          stores.delete(name);
        }
      };
    },
  };
  return store;
}
const stores = new Map<WorkspaceKey, ReturnType<typeof createDocumentStore>>();
export function useAccountDocument<T>(name: WorkspaceKey) {
  const store = useMemo(() => {
    const existing = stores.get(name) ?? createDocumentStore(name);
    stores.set(name, existing);
    return existing;
  }, [name]);
  const { document, error } = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const save = useCallback(
    async (data: T, revision: number, scope?: "shop") => {
      const next = await requestJSON<Doc<T>>(`/api/workspace/${name}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data, revision, ...(scope ? { scope } : {}) }),
      });
      store.publish(next);
      window.dispatchEvent(
        new CustomEvent("workspace:changed", { detail: name }),
      );
      return next;
    },
    [name, store],
  );
  return { document: document as Doc<T> | null, error, reload: store.reload as () => Promise<Doc<T> | null>, save };
}
