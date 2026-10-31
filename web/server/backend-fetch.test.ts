// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { backendFetch } from "./backend-fetch";

afterEach(() => vi.unstubAllGlobals());
it("uses Workerd's manual redirect mode and preserves successful responses", async () => {
  const response = Response.json({ ok: true });
  vi.stubGlobal("fetch", vi.fn(async (_url, init) => {
    if (init.redirect !== "manual") throw Error("Unsupported Workerd redirect mode");
    return response;
  }));
  expect(await backendFetch(new URL("http://localhost:8000/api/v1/health"), {})).toBe(response);
});
it.each([301, 302, 303, 307, 308])("rejects %s without forwarding headers to its location", async status => {
  const fetch = vi.fn(async () => new Response(null, { status, headers: { Location: "https://elsewhere.test/" } }));
  vi.stubGlobal("fetch", fetch);
  await expect(backendFetch(new URL("https://backend.test/api/v1/me"), { headers: { "X-Role": "lead" } })).rejects.toThrow("Backend redirects");
  expect(fetch).toHaveBeenCalledTimes(1);
});
