import { act, renderHook, waitFor } from "@testing-library/react";
import { useProductIdentity } from "./useProductIdentity";
import type { TrackedProduct } from "./types";

vi.mock("../lib/config", () => ({ config: { useFixtures: false } }));
afterEach(() => vi.unstubAllGlobals());
const product = (sku: string): TrackedProduct => ({ sku, name: sku, mode: "workspace" });

it("resolves shop codes and clears them when changing products or ingesting new data", async () => {
  const fetcher = vi.fn(async (url: string) => Response.json({ data: {
    code: new URL(url, "https://test").searchParams.get("code"), sku: "NOICHIEN2L", campaign_ids: ["CD002"],
  } }));
  vi.stubGlobal("fetch", fetcher);
  const { result, rerender } = renderHook(({ item }) => useProductIdentity(item), { initialProps: { item: product("GD0003") } });
  expect(result.current.loading).toBe(true);
  await waitFor(() => expect(result.current.sku).toBe("NOICHIEN2L"));
  expect(result.current.campaignIds).toEqual(["CD002"]);
  act(() => window.dispatchEvent(new CustomEvent("workspace:backend-refresh", { detail: { reason: "goal-applied" } })));
  expect(result.current.sku).toBe("NOICHIEN2L");
  expect(fetcher).toHaveBeenCalledTimes(1);
  fetcher.mockImplementation(async url => Response.json({ data: { code: new URL(url, "https://test").searchParams.get("code"), sku: null, campaign_ids: [] } }));
  act(() => window.dispatchEvent(new CustomEvent("workspace:backend-refresh")));
  expect(result.current.sku).toBeUndefined();
  await waitFor(() => expect(result.current.error).toContain("no confirmed backend SKU"));
  rerender({ item: product("DIFFERENT") });
  expect(result.current.sku).toBeUndefined();
  await waitFor(() => expect(result.current.loading).toBe(false));
});

it("does not send canonical model identifiers through inventory matching", () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  const { result } = renderHook(() => useProductIdentity({ ...product("NOICHIEN2L"), mode: "model" }));
  expect(result.current.sku).toBe("NOICHIEN2L");
  expect(fetcher).not.toHaveBeenCalled();
});
