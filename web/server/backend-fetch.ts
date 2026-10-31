/** Workerd supports manual/follow, not redirect:error. Never forward backend credentials. */
export type BackendService = { fetch(request: Request): Promise<Response> };
export type BackendEnv = {
  DECISION_BACKEND?: BackendService;
  DATA_BACKEND_URL?: string;
  DATA_BACKEND_USER?: string;
  DATA_BACKEND_ROLE?: string;
};

/** Private service binding takes precedence over a legacy public URL. */
export function backendConnection(env: BackendEnv) {
  if (env.DECISION_BACKEND) return {
    base: new URL("https://decision.internal/api/v1/"),
    user: env.DATA_BACKEND_USER || "cloudflare-workspace",
    role: env.DATA_BACKEND_ROLE || "lead",
    service: env.DECISION_BACKEND,
  };
  if (!env.DATA_BACKEND_URL || !env.DATA_BACKEND_USER || !env.DATA_BACKEND_ROLE) return null;
  try {
    const base = new URL(env.DATA_BACKEND_URL.endsWith("/") ? env.DATA_BACKEND_URL : `${env.DATA_BACKEND_URL}/`);
    if (base.username || base.password || base.search || base.hash) return null;
    if (base.protocol !== "https:" && !(base.protocol === "http:" && ["localhost", "127.0.0.1"].includes(base.hostname))) return null;
    return { base, user: env.DATA_BACKEND_USER, role: env.DATA_BACKEND_ROLE, service: undefined };
  } catch { return null; }
}

export async function backendFetch(url: URL, init: RequestInit, service?: BackendService): Promise<Response> {
  const options = { ...init, redirect: "manual" as const };
  const response = service ? await service.fetch(new Request(url, options)) : await fetch(url, options);
  if (response.status >= 300 && response.status < 400) {
    throw new Error("Backend redirects are not allowed.");
  }
  return response;
}
