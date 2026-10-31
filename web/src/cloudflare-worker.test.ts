// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import worker from "../worker";
import { login, passwordHash, session } from "../server/auth";
import { createHmac, scryptSync } from "node:crypto";
import { makeSource } from "./studio/sources";
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
  it("sends only shop rows to ingestion; event rows enter through controlled replay", async () => {
    const { env, cookie } = await authenticated();
    const source = await makeSource("ton_kho_google_sheet.csv", "Mã nội bộ,Tên hàng,Tồn thực tế\nSKU-A,Mug,20\nSKU-A,Mug,7", "test");
    source.replay = { date: "2026-11-11", hours: [-1, 23], lagHours: 0 };
    const backend = { fetch: vi.fn(async (request: Request) => {
      const form = await request.formData();
      const file = form.get("files") as File;
      expect(await file.text()).toContain("SKU-A,Mug,20");
      expect(await file.text()).not.toContain("SKU-A,Mug,7");
      return Response.json({ data: { batches: [] } }, { status: 201 });
    }) };
    const response = await worker.fetch(new Request(origin + "/api/data-backend/import", {
      method: "POST", headers: { Cookie: cookie, Origin: origin },
    }), { ...env, DECISION_BACKEND: backend,
      WORKSPACE: { idFromName: name => name, get: () => ({ fetch: async () => Response.json({ data: { schema: "hackathon.sources.v1", files: [source] } }) }) },
    });
    expect(response.status).toBe(201);
    expect(backend.fetch).toHaveBeenCalledOnce();
  });
  it("only calls the private Python service after login and derives identity on the server", async () => {
    const { env, cookie } = await authenticated();
    const backend = { fetch: vi.fn(async (request: Request) => {
      expect(request.headers.get("X-User")).toBe("admin");
      expect(request.headers.get("X-Role")).toBe("lead");
      expect(request.headers.has("Cookie")).toBe(false);
      return Response.json({ data: { db: "ok" } });
    }) };
    const configured = { ...env, DECISION_BACKEND: backend };
    const anonymous = await worker.fetch(new Request(origin + "/api/v1/health"), configured);
    expect(anonymous.status).toBe(401);
    expect(backend.fetch).not.toHaveBeenCalled();
    const response = await worker.fetch(new Request(origin + "/api/v1/health", {
      headers: { Cookie: cookie, "X-User": "spoofed", "X-Role": "operator" },
    }), configured);
    expect(response.status).toBe(200);
    expect(backend.fetch).toHaveBeenCalledTimes(1);
    const status = await worker.fetch(new Request(origin + "/api/data-backend/status", {
      headers: { Cookie: cookie },
    }), configured);
    expect(await status.json()).toEqual({ configured: true });
    expect((await worker.fetch(new Request(origin + "/api/v1/live/control", {
      method: "POST", headers: { Cookie: cookie, Origin: "https://attacker.test" }, body: "{}",
    }), configured)).status).toBe(403);
    expect(backend.fetch).toHaveBeenCalledTimes(1);
  });
  it("permits the zone-injected Cloudflare analytics script without allowing arbitrary scripts", async () => {
    const response = await worker.fetch(new Request(origin + "/login"), base);
    const policy = response.headers.get("content-security-policy")!;
    expect(policy).toContain("script-src 'self' 'wasm-unsafe-eval' https://static.cloudflareinsights.com;");
    expect(policy).toContain("connect-src 'self' blob: ws: wss: https://cloudflareinsights.com;");
    expect(policy).not.toContain("'unsafe-inline'; style-src");
    expect(policy).not.toContain("'unsafe-eval'");
    expect(policy).toContain("frame-ancestors 'none'");
  });
  it("returns an actionable quota error without exposing internal storage details", async () => {
    const { env, cookie } = await authenticated();
    const response = await worker.fetch(new Request(origin + "/api/workspace/sources", { headers: { Cookie: cookie } }), {
      ...env,
      WORKSPACE: { idFromName: name => name, get: () => ({ fetch: async () => { throw Error("remote: Exceeded allowed rows written in Durable Objects free tier. private storage details"); } }) },
    });
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: {
      code: "WORKSPACE_WRITE_QUOTA",
      message: "Cloudflare's workspace write quota is exhausted. Saved data is retained. Retry after the quota resets or ask the owner to upgrade the Workers plan.",
    } });
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
  it("permits the judges account with its own password hash", async () => {
    const env = {
      ...base,
      SESSION_SECRET: "judges-test-secret-at-least-32-bytes",
      ADMIN_PASSWORD_HASH: await passwordHash("admin-password"),
      JUDGES_PASSWORD_HASH: await passwordHash("judges-password"),
    };
    const response = await login(
      new Request(origin + "/api/auth/login", {
        method: "POST",
        headers: { Origin: origin },
        body: JSON.stringify({ username: "judges", password: "judges-password" }),
      }),
      env,
    );
    expect(response.status).toBe(200);
    const cookie = response.headers.get("set-cookie")!.split(";")[0]!;
    expect(await session(new Request(origin, { headers: { Cookie: cookie } }), env)).toBe(true);
    const account = await worker.fetch(
      new Request(origin + "/api/auth/session", { headers: { Cookie: cookie } }),
      env,
    );
    expect(await account.json()).toEqual({ authenticated: true, account: "judges" });
    const settings = await worker.fetch(new Request(origin + "/api/workspace/settings", {
      headers: { Cookie: cookie },
    }), {
      ...env,
      WORKSPACE: {
        idFromName: (name: string) => {
          expect(name).toBe("admin");
          return name;
        },
        get: () => ({ fetch: async () => Response.json({ revision: 1 }) }),
      },
    });
    expect(settings.status).toBe(200);
    expect(await session(new Request(origin, {
      headers: { Cookie: cookie.replace("judges.", "admin.") },
    }), env)).toBe(false);
    expect(await session(new Request(origin, {
      headers: { Cookie: cookie },
    }), { ...env, JUDGES_PASSWORD_HASH: undefined })).toBe(false);
    for (const [username, password] of [
      ["judges", "wrong"],
      ["judges", "admin-password"],
      ["admin", "judges-password"],
      ["unknown", "judges-password"],
    ]) {
      const denied = await login(new Request(origin + "/api/auth/login", {
        method: "POST",
        headers: { Origin: origin },
        body: JSON.stringify({ username, password }),
      }), env);
      expect(denied.status).toBe(401);
      expect(denied.headers.has("set-cookie")).toBe(false);
    }
  });
});
