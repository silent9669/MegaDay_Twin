import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useAccountDocument } from "./account";
import { StrictMode } from "react";

const doc = (revision: number) => ({ revision, updatedAt: "2026-10-31T00:00:00Z", data: { hour: revision } });
afterEach(() => vi.unstubAllGlobals());
const changed = () => act(() => { window.dispatchEvent(new CustomEvent("workspace:changed", { detail: "sandbox" })); });

it("shares one read and the same document between mounted panels", async () => {
  const fetch = vi.fn(async () => Response.json(doc(1)));
  vi.stubGlobal("fetch", fetch);
  const first = renderHook(() => useAccountDocument("sandbox"));
  const second = renderHook(() => useAccountDocument("sandbox"));
  await waitFor(() => expect(first.result.current.document?.revision).toBe(1));
  await waitFor(() => expect(second.result.current.document?.revision).toBe(1));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(first.result.current.document).toBe(second.result.current.document);
});

it("shares notifications in StrictMode and releases data when the last panel unmounts", async () => {
  const fetch = vi.fn(async () => Response.json(doc(1)));
  vi.stubGlobal("fetch", fetch);
  const wrapper = ({ children }: { children: React.ReactNode }) => <StrictMode>{children}</StrictMode>;
  const first = renderHook(() => useAccountDocument("sandbox"), { wrapper });
  const second = renderHook(() => useAccountDocument("sandbox"), { wrapper });
  await waitFor(() => expect(second.result.current.document?.revision).toBe(1));
  expect(fetch).toHaveBeenCalledTimes(1);
  changed();
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  first.unmount();
  second.unmount();
  const third = renderHook(() => useAccountDocument("sandbox"), { wrapper });
  expect(third.result.current.document).toBeNull();
  await waitFor(() => expect(third.result.current.document?.revision).toBe(1));
  expect(fetch).toHaveBeenCalledTimes(3);
});

it("revalidates without replacing unchanged documents or reading a 304 body", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json(doc(1)))
    .mockResolvedValueOnce(new Response(null, { status: 304 }));
  vi.stubGlobal("fetch", fetch);
  const hook = renderHook(() => useAccountDocument("sandbox"));
  await waitFor(() => expect(hook.result.current.document?.revision).toBe(1));
  const original = hook.result.current.document;
  await act(async () => { await hook.result.current.reload(); });
  expect(fetch.mock.calls[1]?.[1].headers["If-None-Match"]).toBe('"sandbox:1"');
  expect(hook.result.current.document).toBe(original);
  expect(hook.result.current.error).toBe("");
});

it.each(["notification", "reload"])("reconciles a %s arriving during an active read", async trigger => {
  let finish!: (response: Response) => void;
  const fetch = vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }))
    .mockResolvedValueOnce(Response.json(doc(2)));
  vi.stubGlobal("fetch", fetch);
  const hook = renderHook(() => useAccountDocument("sandbox"));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  if (trigger === "notification") changed();
  else act(() => { void hook.result.current.reload(); });
  await act(async () => { finish(Response.json(doc(1))); });
  await waitFor(() => expect(hook.result.current.document?.revision).toBe(2));
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("shares saves immediately and never lets an older read overwrite a save", async () => {
  let finish!: (response: Response) => void;
  const fetch = vi.fn().mockResolvedValueOnce(Response.json(doc(1)))
    .mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }))
    .mockResolvedValueOnce(Response.json(doc(3)))
    .mockResolvedValue(Response.json(doc(3)));
  vi.stubGlobal("fetch", fetch);
  const first = renderHook(() => useAccountDocument("sandbox"));
  const second = renderHook(() => useAccountDocument("sandbox"));
  await waitFor(() => expect(first.result.current.document?.revision).toBe(1));
  let pending!: Promise<unknown>;
  act(() => { pending = first.result.current.reload(); });
  await act(async () => { await first.result.current.save({ hour: 3 }, 1); });
  expect(second.result.current.document?.revision).toBe(3);
  await act(async () => { finish(Response.json(doc(2))); await pending; });
  expect(first.result.current.document?.revision).toBe(3);
  expect(second.result.current.document).toBe(first.result.current.document);
});

it("preserves data on failed saves and recovers after a failed refresh", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(Response.json(doc(1)))
    .mockResolvedValueOnce(Response.json({ error: { message: "Conflict" } }, { status: 409 }))
    .mockRejectedValueOnce(new Error("Offline"))
    .mockResolvedValueOnce(Response.json(doc(2)));
  vi.stubGlobal("fetch", fetch);
  const hook = renderHook(() => useAccountDocument("sandbox"));
  await waitFor(() => expect(hook.result.current.document?.revision).toBe(1));
  await expect(hook.result.current.save({ hour: 2 }, 1)).rejects.toMatchObject({ status: 409 });
  expect(hook.result.current.document?.revision).toBe(1);
  await act(async () => { await hook.result.current.reload(); });
  expect(hook.result.current.error).toBe("Offline");
  await act(async () => { await hook.result.current.reload(); });
  expect(hook.result.current.document?.revision).toBe(2);
  expect(hook.result.current.error).toBe("");
});
