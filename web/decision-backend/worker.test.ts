// @vitest-environment node
import { expect, it, vi } from "vitest";

const registry = vi.hoisted(() => new Map());
vi.mock("@cloudflare/containers", () => ({
  Container: class {
    static set outboundByHost(handlers: unknown) { registry.set(this.name, handlers); }
    static get outboundByHost() { return registry.get(this.name); }
  },
  ContainerProxy: class {},
}));

it("registers the private storage route through the Container SDK setter", async () => {
  const { DecisionBackend } = await import("./worker");
  const handlers = registry.get(DecisionBackend.name);
  expect(handlers).toHaveProperty("state.internal");
  const state = { get: vi.fn(async () => null) };
  const response = await handlers["state.internal"](new Request("http://state.internal/state"), { STATE: state });
  expect(response.status).toBe(404);
  expect(state.get).toHaveBeenCalledWith("admin/state-v1.zip");
});
