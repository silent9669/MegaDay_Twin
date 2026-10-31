import { afterEach, expect, it, vi } from "vitest";
import { requestJSON } from "./account";
afterEach(() => vi.unstubAllGlobals());
it.each([401, 502])("explains non-JSON server responses (%s) without exposing the response body", async status => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>private upstream details</html>", { status })));
  await expect(requestJSON("/api/product-labels")).rejects.toMatchObject({
    status,
    message: status === 401 ? "Sign in again to continue." : "The server returned an unreadable response (HTTP 502). Retry shortly.",
  });
});
