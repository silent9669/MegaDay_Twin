// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";

afterEach(() => { vi.unstubAllEnvs(); vi.resetModules(); });

it("does not require optional Python secrets when publishing the web", async () => {
  vi.stubEnv("MEGADAY_LOCAL_BACKEND", "");
  vi.resetModules();
  const { default: config } = await import("../cloudflare.config");
  for (const name of ["DATA_BACKEND_URL", "DATA_BACKEND_USER", "DATA_BACKEND_ROLE"])
    expect(config.worker.env).not.toHaveProperty(name);
  for (const name of ["ADMIN_PASSWORD_HASH", "JUDGES_PASSWORD_HASH", "SESSION_SECRET", "OPENAI_API_KEY"])
    expect(config.worker.env).toHaveProperty(name);
});

it("exposes the local bridge secrets only with an explicit local opt-in", async () => {
  vi.stubEnv("MEGADAY_LOCAL_BACKEND", "1");
  vi.resetModules();
  const { default: config } = await import("../cloudflare.config");
  for (const name of ["DATA_BACKEND_URL", "DATA_BACKEND_USER", "DATA_BACKEND_ROLE"])
    expect(config.worker.env).toHaveProperty(name);
});
