import { fail } from "./auth";

export function workspaceQuotaFailure(error: unknown): Response | null {
  if (!(error instanceof Error) || !error.message.includes("Exceeded allowed rows written in Durable Objects free tier")) return null;
  return fail(503,
    "Cloudflare's workspace write quota is exhausted. Saved data is retained. Retry after the quota resets or ask the owner to upgrade the Workers plan.",
    "WORKSPACE_WRITE_QUOTA",
  );
}
