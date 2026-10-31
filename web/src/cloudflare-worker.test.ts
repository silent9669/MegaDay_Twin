import { describe, expect, it } from "vitest";
import worker from "../worker";
import { login, passwordHash, session } from "../server/auth";
import { createHmac, scryptSync } from "node:crypto";
const base = {
  DB: { prepare: () => ({ first: async () => ({ ok: 1 }) }) },
  ASSETS: { fetch: async () => new Response("repo-owned app") },
};
const origin = "https://example.test";
async function authenticated() {
  const env = {
    ...base,
    SESSION_SECRET: "workspace-test-secret-at-least-32-bytes",
    ADMIN_PASSWORD_HASH: await passwordHash("test-password"),
  };
  const response = await login(
    new Request(`${origin}/api/auth/login`, {
      method: "POST",
      headers: { Origin: origin },
      body: JSON.stringify({ username: "admin", password: "test-password" }),
    }),
    env,
  );
  return { env, cookie: response.headers.get("set-cookie")!.split(";")[0]! };
}
describe("repository-owned deployment", () => {
  it("permits the zone-injected Cloudflare analytics script without allowing arbitrary scripts", async () => {
    const response = await worker.fetch(new Request(origin + "/login"), base);
    const policy = response.headers.get("content-security-policy")!;
    expect(policy).toContain("script-src 'self' https://static.cloudflareinsights.com;");
    expect(policy).toContain("connect-src 'self' ws: wss: https://cloudflareinsights.com;");
    expect(policy).not.toContain("'unsafe-inline'; style-src");
    expect(policy).toContain("frame-ancestors 'none'");
  });
  it("checks D1 without requiring a session", async () => {
    const response = await worker.fetch(new Request(`${origin}/healthz`), base);
    expect(await response.json()).toEqual({
      status: "ok",
      db: "ok",
      mode: "fixtures",
    });
    const unhealthy = await worker.fetch(new Request(`${origin}/healthz`), {
      ...base,
      DB: {
        prepare: () => ({
          first: async () => {
            throw Error("private database error");
          },
        }),
      },
    });
    expect(unhealthy.status).toBe(503);
    expect(await unhealthy.text()).not.toContain("private database");
  });
  it("requires authentication for city, sources and API", async () => {
    for (const path of ["/city-demo", "/mock-data", "/settings"])
      expect(
        (await worker.fetch(new Request(origin + path), base)).status,
      ).toBe(307);
    for (const path of ["/api/workspace/sources", "/api/v1/skus"])
      expect(
        (await worker.fetch(new Request(origin + path), base)).status,
      ).toBe(401);
    expect(
      (await worker.fetch(new Request(origin + "/login"), base)).status,
    ).toBe(200);
  });
  it("serves all frontend assets from this repo without another Worker", async () => {
    const { env, cookie } = await authenticated();
    for (const path of ["/city-demo", "/mock-data", "/?workspace=operations"]) {
      const response = await worker.fetch(
        new Request(origin + path, { headers: { Cookie: cookie } }),
        env,
      );
      expect(await response.text()).toBe("repo-owned app");
    }
  });
  it("assigns the workspace account server-side and rejects cross-origin writes", async () => {
    const { env, cookie } = await authenticated();
    const workspace = {
      idFromName: (name: string) => {
        expect(name).toBe("admin");
        return "fixed";
      },
      get: () => ({ fetch: async () => Response.json({ revision: 1 }) }),
    };
    const response = await worker.fetch(
      new Request(origin + "/api/workspace/settings", {
        method: "PUT",
        headers: { Cookie: cookie, Origin: origin },
        body: "{}",
      }),
      { ...env, WORKSPACE: workspace },
    );
    expect(response.status).toBe(200);
    const denied = await worker.fetch(
      new Request(origin + "/api/workspace/settings", {
        method: "PUT",
        headers: { Cookie: cookie, Origin: "https://other.test" },
        body: "{}",
      }),
      { ...env, WORKSPACE: workspace },
    );
    expect(denied.status).toBe(403);
  });
  it("does not grant backend roles from request headers", async () => {
    const { env, cookie } = await authenticated();
    const response = await worker.fetch(
      new Request(origin + "/api/v1/skus", {
        headers: { Cookie: cookie, "X-Role": "lead" },
      }),
      env,
    );
    expect(response.status).toBe(503);
    expect(await response.text()).toContain("Python backend is not connected");
  });
});
describe("account access", () => {
  it("preserves the deployed scrypt password and existing megaday_session cookies", async () => {
    const secret = "existing-production-secret-at-least-32-bytes";
    const salt = "legacy-test-salt";
    const env = {
      ...base,
      SESSION_SECRET: secret,
      ADMIN_PASSWORD_HASH: `scrypt:${salt}:${scryptSync("existing-password", salt, 64).toString("base64url")}`,
    };
    const payload = `admin.${Math.floor(Date.now() / 1000) + 3600}.existing-session`;
    const token = `${payload}.${createHmac("sha256", secret).update(payload).digest("base64url")}`;
    expect(
      await session(
        new Request(origin, {
          headers: { Cookie: `megaday_session=${token}` },
        }),
        env,
      ),
    ).toBe(true);
    const response = await login(
      new Request(origin + "/api/auth/login", {
        method: "POST",
        headers: { Origin: origin },
        body: JSON.stringify({
          username: "admin",
          password: "existing-password",
        }),
      }),
      env,
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("set-cookie")).toMatch(/^megaday_session=/);
    const denied = await login(
      new Request(origin + "/api/auth/login", {
        method: "POST",
        headers: { Origin: origin },
        body: JSON.stringify({ username: "admin", password: "wrong" }),
      }),
      env,
    );
    expect(denied.status).toBe(401);
  });
  it("sets a signed HttpOnly cookie and rejects a changed token", async () => {
    const { env, cookie } = await authenticated();
    expect(
      await session(new Request(origin, { headers: { Cookie: cookie } }), env),
    ).toBe(true);
    expect(
      await session(
        new Request(origin, { headers: { Cookie: cookie + "x" } }),
        env,
      ),
    ).toBe(false);
    expect(await session(new Request(origin), env)).toBe(false);
  });
  it("rejects bad credentials, cross-origin login and logout", async () => {
    const { env } = await authenticated();
    expect(
      (
        await login(
          new Request(origin + "/api/auth/login", {
            method: "POST",
            headers: { Origin: origin },
            body: JSON.stringify({ username: "admin", password: "wrong" }),
          }),
          env,
        )
      ).status,
    ).toBe(401);
    expect(
      (
        await login(
          new Request(origin + "/api/auth/login", {
            method: "POST",
            body: "{}",
          }),
          env,
        )
      ).status,
    ).toBe(403);
    expect(
      (
        await worker.fetch(
          new Request(origin + "/api/auth/logout", { method: "POST" }),
          env,
        )
      ).status,
    ).toBe(403);
  });
});
