// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { modelJSON, ModelRequestError } from "./model-json";
afterEach(() => vi.unstubAllGlobals());
it.each([400, 401, 403, 404, 429, 500])("reports safe actionable provider error %s", async status => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("secret-key-sensitive-upstream", { status })));
  const result = modelJSON({ OPENAI_API_KEY: "private" }, "test", "test", {}, {}, new AbortController().signal);
  await expect(result).rejects.toBeInstanceOf(ModelRequestError);
  await expect(result).rejects.not.toThrow("secret-key");
});
