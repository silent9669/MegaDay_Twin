// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { backendConnection, backendFetch } from "./backend-fetch";

afterEach(() => vi.unstubAllGlobals());
it("uses a private binding without a URL or credentials and never calls public fetch", async () => {
  const service = { fetch: vi.fn(async () => Response.json({ ok: true })) };
  const publicFetch = vi.fn();
  vi.stubGlobal("fetch", publicFetch);
  const connection = backendConnection({ DECISION_BACKEND: service, DATA_BACKEND_URL: "https://wrong.example" })!;
  expect(connection.base.href).toBe("https://decision.internal/api/v1/");
  const response = await backendFetch(new URL("me", connection.base), { headers: { "X-User": "admin", "X-Role": "lead" } }, connection.service);
  expect(response.ok).toBe(true);
  expect(service.fetch).toHaveBeenCalledWith(expect.any(Request));
  expect(publicFetch).not.toHaveBeenCalled();
});
it("does not report malformed or incomplete legacy connections as configured", () => {
  expect(backendConnection({ DATA_BACKEND_URL: "https://example.com" })).toBeNull();
  expect(backendConnection({ DATA_BACKEND_URL: "http://example.com", DATA_BACKEND_USER: "a", DATA_BACKEND_ROLE: "lead" })).toBeNull();
});
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
