import { describe, expect, it } from "vitest";
import { WorkspaceMonitor, snapshot } from "./changes";
import type { SourcePack } from "../../studio/sources";
import type { Doc, Sandbox } from "../../studio/workspace-types";
function data(stock: string, revision = 1, hour = 1) {
  const pack: SourcePack = {
    schema: "hackathon.sources.v1",
    files: [
      {
        kind: "inventory",
        name: "inventory.csv",
        hash: `hash-${stock}`,
        origin: "upload",
        text: `Mã nội bộ,Tồn thực tế,Tên hàng,Thời điểm cập nhật\nSKU1,${stock},Kettle,2026-11-11 00:00\n`,
      },
    ],
  };
  return snapshot({ revision, updatedAt: "", data: pack }, {
    revision,
    updatedAt: "",
    data: { hour, event: "observe" },
  } as Doc<Sandbox>);
}
describe("workspace monitor", () => {
  it("establishes a silent baseline and ignores duplicate/old revisions", () => {
    const m = new WorkspaceMonitor();
    expect(m.observe(data("10"))).toEqual([]);
    expect(m.observe(data("0"))).toEqual([]);
    expect(m.observe(data("0", 0))).toEqual([]);
    expect(m.observe(data("0", 2)).some((n) => n.kind === "stock-zero")).toBe(
      true,
    );
    expect(m.observe(data("0", 2))).toEqual([]);
  });
  it("reports zero, unknown and recovered inventory with exact evidence", () => {
    const m = new WorkspaceMonitor();
    m.observe(data("10"));
    const zero = m.observe(data("0", 2)).find((n) => n.kind === "stock-zero")!;
    expect(zero.before).toBe(10);
    expect(zero.after).toBe(0);
    expect(zero.reference.entity).toBe("SKU1");
    expect(m.observe(data("", 3)).some((n) => n.kind === "stock-unknown")).toBe(
      true,
    );
    expect(
      m.observe(data("7", 4)).some((n) => n.kind === "stock-recovered"),
    ).toBe(true);
  });
  it("resets comparison on sandbox rewind and labels forward playback", () => {
    const m = new WorkspaceMonitor();
    m.observe(data("10", 1, 8));
    const rewind = m.observe(data("0", 2, 2));
    expect(rewind.map((n) => n.kind)).toEqual(["clock"]);
    expect(rewind[0]!.important).toBe(false);
    expect(m.observe(data("7", 3, 3)).every((n) => n.simulation)).toBe(true);
  });
  it("reports source replacement and diagnostic resolution", () => {
    const m = new WorkspaceMonitor();
    m.observe(data("-1"));
    const notices = m.observe(data("5", 2));
    expect(notices.some((n) => n.kind === "source")).toBe(true);
    expect(notices.some((n) => n.kind === "diagnostics-resolved")).toBe(true);
  });
  it("labels reconnect differences without replaying intermediate states", () => {
    const m = new WorkspaceMonitor();
    m.observe(data("10"));
    const notices = m.observe(data("0", 8), true);
    expect(notices.every((n) => n.reconnected)).toBe(true);
    expect(notices.filter((n) => n.kind === "stock-zero")).toHaveLength(1);
  });
  it("rejects mixed snapshots with a regressing document revision", () => {
    const m = new WorkspaceMonitor();
    const first = data("10", 4);
    m.observe(first);
    expect(m.observe({ ...data("0", 5), sandboxRevision: 3 })).toEqual([]);
    expect(m.observe(data("0", 5)).some((n) => n.kind === "stock-zero")).toBe(
      true,
    );
  });
});
