import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useWorkspaceMonitor } from "./useWorkspaceMonitor";
import type { SourcePack } from "../../studio/sources";
import type { Doc, Sandbox } from "../../studio/workspace-types";
const source = (revision: number, stock: number): Doc<SourcePack> => ({
  revision,
  updatedAt: "",
  data: {
    schema: "hackathon.sources.v1",
    files: [
      {
        kind: "inventory",
        name: "inventory.csv",
        origin: "upload",
        hash: String(revision),
        text: `Mã nội bộ,Tồn thực tế,Tên hàng,Thời điểm cập nhật\nSKU1,${stock},Kettle,2026-11-11 00:00\n`,
      },
    ],
  },
});
const sandbox: Doc<Sandbox> = {
  revision: 1,
  updatedAt: "",
  data: { hour: 1, event: "observe" },
};
afterEach(() => vi.useRealTimers());
it("coalesces a burst and reconnects against its last baseline without fetching AI", () => {
  vi.useFakeTimers();
  const fetch = vi.spyOn(globalThis, "fetch");
  const { result, rerender } = renderHook(
    ({ revision, stock, connected }) =>
      useWorkspaceMonitor(source(revision, stock), sandbox, connected),
    { initialProps: { revision: 1, stock: 10, connected: true } },
  );
  act(() => vi.advanceTimersByTime(1000));
  expect(result.current.notices).toHaveLength(0);
  rerender({ revision: 2, stock: 3, connected: true });
  act(() => vi.advanceTimersByTime(500));
  rerender({ revision: 3, stock: 0, connected: true });
  act(() => vi.advanceTimersByTime(1000));
  expect(
    result.current.notices.filter((n) => n.kind === "source"),
  ).toHaveLength(1);
  expect(result.current.toast?.kind).toBe("stock-zero");
  act(() => result.current.markRead());
  expect(result.current.unread).toBe(0);
  rerender({ revision: 3, stock: 0, connected: false });
  rerender({ revision: 7, stock: 9, connected: true });
  act(() => vi.advanceTimersByTime(1000));
  expect(
    result.current.notices.find((n) => n.kind === "stock-recovered")
      ?.reconnected,
  ).toBe(true);
  expect(fetch).not.toHaveBeenCalled();
  fetch.mockRestore();
});
