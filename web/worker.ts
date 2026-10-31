import {
  fail,
  login,
  logout,
  sameOrigin,
  sessionAccount,
  sessionExpiry,
  type AccessEnv,
} from "./server/auth";
import { repositorySources } from "./server/repository";
import { modelList, type PMEnv } from "./server/pm";
import { savedChat } from "./server/chat-store";
import { SOURCE_DEFS, type SourcePack } from "./src/studio/sources";
export { AccountWorkspace } from "./server/workspace";
export type Env = AccessEnv &
  PMEnv & {
    DB: { prepare(sql: string): { first(): Promise<unknown> } };
    ASSETS: { fetch(request: Request): Promise<Response> };
    WORKSPACE?: {
      idFromName(name: string): unknown;
      get(id: unknown): { fetch(request: Request): Promise<Response> };
    };
    PM_LIMITER?: {
      limit(input: { key: string }): Promise<{ success: boolean }>;
    };
    DATA_BACKEND_URL?: string;
    DATA_BACKEND_USER?: string;
    DATA_BACKEND_ROLE?: string;
  };
async function handle(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url),
    path = url.pathname;
  if (path === "/healthz") {
    let healthy = true;
    try {
      await env.DB.prepare("SELECT 1 AS ok").first();
    } catch {
      healthy = false;
    }
    return Response.json(
      {
        status: healthy ? "ok" : "degraded",
        db: healthy ? "ok" : "error",
        mode: "fixtures",
      },
      { status: healthy ? 200 : 503 },
    );
  }
  if (path === "/api/auth/login" && request.method === "POST")
    return login(request, env);
  if (path === "/api/auth/logout" && request.method === "POST")
    return logout(request);
  const account = await sessionAccount(request, env);
  const authenticated = !!account;
  if (path === "/api/auth/session")
    return Response.json(
      { authenticated, account },
      { status: authenticated ? 200 : 401 },
    );
  const publicAsset =
    /^\/(assets\/|icon\.svg$|theme-init\.js$|favicon\.ico$)/.test(path);
  if (!authenticated && path !== "/login" && !publicAsset) {
    if (path.startsWith("/api/"))
      return fail(401, "Sign in to continue.", "UNAUTHENTICATED");
    return Response.redirect(
      new URL(`/login?next=${encodeURIComponent(path + url.search)}`, url),
      307,
    );
  }
  if (path.startsWith("/api/")) {
    if (!["GET", "HEAD"].includes(request.method) && !sameOrigin(request))
      return fail(403, "Request not allowed.");
    if (
      path === "/api/workspace/events" &&
      request.headers.get("origin") !== url.origin
    )
      return fail(403, "Request not allowed.");
    if (path.startsWith("/api/workspace/")) {
      if (!env.WORKSPACE)
        return fail(503, "Account storage is not configured.");
      const headers = new Headers(request.headers);
      headers.set("X-Session-Expires", String(sessionExpiry(request)));
      return env.WORKSPACE.get(env.WORKSPACE.idFromName("admin")).fetch(
        new Request(request, { headers }),
      );
    }
    if (path === "/api/repository-sources" && request.method === "GET") {
      const result = await repositorySources();
      return Response.json(result);
    }
    if (path === "/api/pm/models") {
      try {
        return Response.json(await modelList(env));
      } catch {
        return fail(502, "OpenAI connection unavailable.");
      }
    }
    if (path === "/api/pm/chat" && request.method === "POST") {
      if (
        env.PM_LIMITER &&
        !(await env.PM_LIMITER.limit({ key: "admin" })).success
      )
        return fail(429, "PM is busy. Try again in a minute.");
      let context: unknown = { sources: "unavailable" };
      if (env.WORKSPACE) {
        const response = await env.WORKSPACE.get(
          env.WORKSPACE.idFromName("admin"),
        ).fetch(new Request(new URL("/api/workspace/sources", url)));
        const document = (await response.json()) as {
          revision: number;
          data: SourcePack;
        };
        context = {
          revision: document.revision,
          sources: document.data.files.map((f) => ({
            type: f.kind,
            name: f.name,
            hash: f.hash,
            origin: f.origin,
          })),
          decisionToolsConnected: !!env.DATA_BACKEND_URL,
        };
      }
      if (!env.WORKSPACE)
        return fail(503, "Account storage is not configured.");
      return savedChat(
        request,
        env,
        context,
        env.WORKSPACE.get(env.WORKSPACE.idFromName("admin")),
      );
    }
    if (path === "/api/data-backend/status")
      return Response.json({ configured: !!env.DATA_BACKEND_URL });
    if (path.startsWith("/api/v1/") || path === "/api/data-backend/import") {
      if (
        !env.DATA_BACKEND_URL ||
        !env.DATA_BACKEND_USER ||
        !env.DATA_BACKEND_ROLE
      )
        return fail(
          503,
          "Python backend is not connected. Source workspace is still saved.",
        );
      const base = new URL(
        env.DATA_BACKEND_URL.endsWith("/")
          ? env.DATA_BACKEND_URL
          : `${env.DATA_BACKEND_URL}/`,
      );
      if (
        base.protocol !== "https:" &&
        !["localhost", "127.0.0.1"].includes(base.hostname)
      )
        return fail(503, "Backend URL must use HTTPS.");
      const headers = new Headers({
        "X-User": env.DATA_BACKEND_USER,
        "X-Role": env.DATA_BACKEND_ROLE,
      });
      if (path === "/api/data-backend/import") {
        if (request.method !== "POST" || env.DATA_BACKEND_ROLE === "viewer")
          return fail(403, "Import requires an assigned operator.");
        if (!env.WORKSPACE) return fail(503, "Workspace unavailable.");
        const stored = await env.WORKSPACE.get(
          env.WORKSPACE.idFromName("admin"),
        ).fetch(new Request(new URL("/api/workspace/sources", url)));
        const doc = (await stored.json()) as { data: SourcePack };
        const form = new FormData();
        for (const f of doc.data.files)
          form.append(
            "files",
            new Blob([f.text], {
              type: f.name.endsWith(".csv") ? "text/csv" : "application/json",
            }),
            SOURCE_DEFS.find((d) => d.kind === f.kind)!.file,
          );
        return fetch(new URL("ingest/files", base), {
          method: "POST",
          headers,
          body: form,
          redirect: "error",
        });
      }
      const relative = path.slice("/api/v1/".length);
      if (relative.includes("..") || !/^[\w/.-]+$/.test(relative))
        return fail(400, "Invalid backend path.");
      if (!["GET", "HEAD"].includes(request.method)) {
        headers.set("Content-Type", "application/json");
      }
      return fetch(new URL(relative + url.search, base), {
        method: request.method,
        headers,
        body: ["GET", "HEAD"].includes(request.method)
          ? undefined
          : request.body,
        redirect: "error",
      });
    }
    return fail(404, "API route not found.");
  }
  if (path === "/" && url.searchParams.get("workspace") !== "operations")
    return Response.redirect(new URL("/city-demo", url), 307);
  return env.ASSETS.fetch(request);
}
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    let response: Response;
    try {
      response = await handle(request, env);
    } catch {
      response = fail(
        503,
        "Service temporarily unavailable. Your saved workspace is retained.",
      );
    }
    if (response.status === 101) return response;
    const headers = new Headers(response.headers);
    headers.set("X-Content-Type-Options", "nosniff");
    headers.set("X-Frame-Options", "DENY");
    headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
    headers.set(
      "Content-Security-Policy",
      "default-src 'self'; script-src 'self' https://static.cloudflareinsights.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' ws: wss: https://cloudflareinsights.com; font-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    );
    if (!new URL(request.url).pathname.startsWith("/assets/"))
      headers.set("Cache-Control", "no-store");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  },
};
