// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { modelList, pmChat } from "./pm";
afterEach(() => vi.unstubAllGlobals());
describe("PM model discovery", () => {
  it("lists accessible text models across families without the eight-model cap", async () => {
    const ids = [
      "gpt-6.1-sol",
      "gpt-6-astra",
      "gpt-6-luna",
      "gpt-5.6-sol",
      "gpt-5",
      "gpt-5-mini",
      "gpt-5-nano",
      "gpt-4.1",
      "gpt-4.1-mini",
      "gpt-4o",
      "o3",
      "o4-mini",
    ];
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          Response.json({
            data: [
              ...ids,
              "gpt-4o-realtime-preview",
              "gpt-image-1",
              "text-embedding-3-small",
              "gpt-4o-audio-preview",
              "gpt-5-codex",
            ].map((id) => ({ id, created: 1 })),
          }),
        ),
    );
    const result = await modelList({
      OPENAI_API_KEY: "test",
      OPENAI_MODEL: "gpt-6.1-sol",
    });
    expect(result.models).toEqual(expect.arrayContaining(ids));
    expect(result.models).toHaveLength(ids.length);
    expect(result.defaultModel).toBe("gpt-6.1-sol");
  });
  it("returns an empty list without credentials and rejects unlisted models", async () => {
    expect(await modelList({})).toMatchObject({
      configured: false,
      models: [],
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          Response.json({ data: [{ id: "gpt-4.1", created: 1 }] }),
        ),
    );
    const response = await pmChat(
      new Request("https://test/api/pm/chat", {
        method: "POST",
        body: JSON.stringify({
          model: "invented",
          messages: [{ role: "user", content: "Hello" }],
        }),
      }),
      { OPENAI_API_KEY: "test" },
      {},
    );
    expect(response.status).toBe(422);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
