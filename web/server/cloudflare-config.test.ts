import { describe, expect, it } from "vitest";
import config from "../cloudflare.config";

describe("production secret requirements", () => {
  it("requires authentication and AI secrets but allows an unconfigured optional backend", () => {
    const env = config.worker.env;
    const required = Object.entries(env).filter(([, binding]) => binding.type === "secret").map(([name]) => name);
    expect(required).toEqual(expect.arrayContaining(["ADMIN_PASSWORD_HASH", "JUDGES_PASSWORD_HASH", "SESSION_SECRET", "OPENAI_API_KEY"]));
    for (const name of ["DATA_BACKEND_URL", "DATA_BACKEND_USER", "DATA_BACKEND_ROLE"]) {
      expect(required).not.toContain(name);
      expect(env).not.toHaveProperty(name);
    }
  });
});
