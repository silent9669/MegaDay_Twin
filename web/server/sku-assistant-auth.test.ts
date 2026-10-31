// @vitest-environment node
import { createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "../worker";

const secret = "sku-assistant-test-session-secret-32-bytes";
const env = {
  SESSION_SECRET: secret, ADMIN_PASSWORD_HASH: "configured",
  DB: { prepare: () => ({ first: async () => ({ ok: true }) }) },
  ASSETS: { fetch: async () => new Response("app") },
};
const signed = `admin.${Math.floor(Date.now() / 1000) + 3600}.nonce`;
const cookie = `megaday_session=${signed}.${createHmac("sha256", secret).update(signed).digest("base64url")}`;
afterEach(() => vi.unstubAllGlobals());

describe("SKU assistant worker boundary", () => {
  it("protects SKU data and conversation before any backend or AI call", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    for (const path of ["skus", "context?sku=SKU1", "turn"]) {
      const response = await worker.fetch(new Request(`https://app.test/api/sale-assistant/${path}`, {
        method: path === "turn" ? "POST" : "GET", ...(path === "turn" ? { body: "{}" } : {}),
      }), env);
      expect(response.status).toBe(401);
    }
    expect(fetch).not.toHaveBeenCalled();
  });
  it("requires same-origin conversation writes and enforces the shared rate limit", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    const limit = vi.fn(async () => ({ success: false }));
    const request = (origin: string) => new Request("https://app.test/api/sale-assistant/turn", {
      method: "POST", headers: { Cookie: cookie, Origin: origin, "Content-Type": "application/json" }, body: "{}",
    });
    expect((await worker.fetch(request("https://other.test"), { ...env, PM_LIMITER: { limit } })).status).toBe(403);
    expect(limit).not.toHaveBeenCalled();
    expect((await worker.fetch(request("https://app.test"), { ...env, PM_LIMITER: { limit } })).status).toBe(429);
    expect(limit).toHaveBeenCalledWith({ key: "admin" });
    expect(fetch).not.toHaveBeenCalled();
  });
});
it('keeps HQ documents and Milo runtime behind authenticated, same-origin routes',async()=>{
 const storage={fetch:vi.fn(async(request:Request)=>Response.json({ok:request.method==="POST"}))};
 const workspace={idFromName:vi.fn(()=> 'account'),get:vi.fn(()=>storage)};
 expect((await worker.fetch(new Request('https://app.test/api/workspace/hq'),{...env,WORKSPACE:workspace})).status).toBe(401);
 expect((await worker.fetch(new Request('https://app.test/api/pm/chat',{method:'POST',headers:{cookie,Origin:'https://other.test'},body:'{}'}),{...env,WORKSPACE:workspace})).status).toBe(403);
 expect(storage.fetch).not.toHaveBeenCalled();
 expect((await worker.fetch(new Request('https://app.test/api/pm/chat',{method:'POST',headers:{cookie,Origin:'https://app.test'},body:'{}'}),{...env,WORKSPACE:workspace})).status).toBe(200);
 expect(new URL(storage.fetch.mock.calls[0]![0].url).pathname).toBe('/internal/milo/chat');
});
