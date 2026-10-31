import { useCallback, useEffect, useState } from "react";
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
export function useAccountDocument<T>(name: WorkspaceKey) {
  const [document, setDocument] = useState<Doc<T> | null>(null),
    [error, setError] = useState("");
  const reload = useCallback(async () => {
    try {
      const next = await requestJSON<Doc<T>>(`/api/workspace/${name}`);
      setDocument((old) =>
        !old || next.revision >= old.revision ? next : old,
      );
      setError("");
      return next;
    } catch (error) {
      setError(
        error instanceof Error ? error.message : "Workspace unavailable.",
      );
      return null;
    }
  }, [name]);
  useEffect(() => {
    let active = true;
    const changed = (event: Event) => {
      const key = (event as CustomEvent).detail;
      if (active && (!key || key === name)) void reload();
    };
    const initial = setTimeout(() => void reload(), 0);
    window.addEventListener("workspace:changed", changed);
    return () => {
      active = false;
      clearTimeout(initial);
      window.removeEventListener("workspace:changed", changed);
    };
  }, [name, reload]);
  const save = useCallback(
    async (data: T, revision: number) => {
      const next = await requestJSON<Doc<T>>(`/api/workspace/${name}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data, revision }),
      });
      setDocument((old) =>
        !old || next.revision >= old.revision ? next : old,
      );
      window.dispatchEvent(
        new CustomEvent("workspace:changed", { detail: name }),
      );
      return next;
    },
    [name],
  );
  return { document, error, reload, save };
}
