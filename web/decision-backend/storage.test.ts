// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import type { R2Bucket } from "@cloudflare/workers-types";
import { SNAPSHOT_KEY, snapshotStorage } from "./storage";

function bucket(overrides = {}) {
  return { get: vi.fn(async () => null), put: vi.fn(async () => ({ httpEtag: '"new"' })), ...overrides };
}
const request = (headers: Record<string, string> = {}) => new Request("http://state.internal/state", {
  method: "PUT", headers: { "Content-Length": "3", ...headers }, body: "zip",
});
describe("private snapshot storage", () => {
  it("uses 404 only for a genuinely missing first snapshot", async () => {
    expect((await snapshotStorage(new Request("http://state.internal/state"), bucket() as unknown as R2Bucket)).status).toBe(404);
    const broken = bucket({ get: vi.fn(async () => { throw Error("R2 unavailable"); }) });
    await expect(snapshotStorage(new Request("http://state.internal/state"), broken as unknown as R2Bucket)).rejects.toThrow("R2 unavailable");
  });
  it("fences initial and stale writers with conditional R2 writes", async () => {
    const state = bucket();
    expect((await snapshotStorage(request({ "If-None-Match": "*" }), state as unknown as R2Bucket)).status).toBe(204);
    expect(state.put).toHaveBeenLastCalledWith(SNAPSHOT_KEY, expect.anything(), expect.objectContaining({ onlyIf: { etagDoesNotMatch: "*" } }));
    await snapshotStorage(request({ "If-Match": '"old"' }), state as unknown as R2Bucket);
    expect(state.put).toHaveBeenLastCalledWith(SNAPSHOT_KEY, expect.anything(), expect.objectContaining({ onlyIf: { etagMatches: "old" } }));
    const stale = bucket({ put: vi.fn(async () => null) });
    expect((await snapshotStorage(request({ "If-Match": '"old"' }), stale as unknown as R2Bucket)).status).toBe(412);
  });
  it("rejects unversioned, oversized and unexpected writes", async () => {
    const state = bucket();
    expect((await snapshotStorage(request(), state as unknown as R2Bucket)).status).toBe(428);
    expect((await snapshotStorage(request({ "Content-Length": String(100 * 1024 * 1024) }), state as unknown as R2Bucket)).status).toBe(413);
    expect((await snapshotStorage(new Request("http://state.internal/other"), state as unknown as R2Bucket)).status).toBe(404);
    expect(state.put).not.toHaveBeenCalled();
  });
});
