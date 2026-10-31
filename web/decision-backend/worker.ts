import { Container } from "@cloudflare/containers";
import type { DurableObjectNamespace, R2Bucket } from "@cloudflare/workers-types";
import { snapshotStorage } from "./storage";
export { ContainerProxy } from "@cloudflare/containers";

interface Env {
  PYTHON: DurableObjectNamespace;
  STATE: R2Bucket;
  OPENAI_API_KEY?: string;
}

export class DecisionBackend extends Container<Env> {
  defaultPort = 8000;
  sleepAfter = "30m";
  envVars: Record<string, string> = {
    APP_ENV: "demo", DEMO_MODE: "true", LIVE_ENABLED: "false",
    DUCKDB_PATH: "/state/megaday.duckdb", LIVE_DIR: "/state/live",
    DATA_DIR: "/seed-input", MEGADAY_STATE_URL: "http://state.internal/state",
    MEGADAY_STATE_ROOT: "/state", MEGADAY_SEED_DB: "/seed/megaday.duckdb",
    AI_ENABLED: this.env.OPENAI_API_KEY ? "true" : "false",
    ...(this.env.OPENAI_API_KEY ? { OPENAI_API_KEY: this.env.OPENAI_API_KEY } : {}),
  };
  static outboundByHost = {
    "state.internal": (request: Request, env: Env) => snapshotStorage(request, env.STATE),
  };
  override async fetch(request: Request): Promise<Response> {
    await this.startAndWaitForPorts({ ports: [8000], cancellationOptions: { portReadyTimeoutMS: 180000 } });
    return this.containerFetch(request);
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    // No workers.dev / routes. Only the authenticated web Worker has this service binding.
    if (!url.pathname.startsWith("/api/v1/") || !request.headers.get("X-User") ||
        !["viewer", "operator", "lead"].includes(request.headers.get("X-Role") || ""))
      return Response.json({ error: { code: "FORBIDDEN", message: "Private backend service." } }, { status: 403 });
    try {
      return await env.PYTHON.get(env.PYTHON.idFromName("admin")).fetch(request);
    } catch {
      return Response.json({ error: { code: "BACKEND_UNAVAILABLE", message: "Backend is starting or could not restore saved data. Try again shortly." } }, { status: 503 });
    }
  },
};
