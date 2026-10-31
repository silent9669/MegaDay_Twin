import { describe, expect, it } from "vitest";
import { makeSource, validatePack } from "./sources";
import { readSource } from "./read-source";
import { mergeReplaySources, playbackHour, visibleSources } from "./replay";
import { sourceView } from "./source-view";
import { sourceText } from "./source-text";

describe("shop baseline and event playback", () => {
  it("preserves history, gates exact replay hours and does not duplicate a reimport", async () => {
    const baseline = await makeSource("stock.csv", "Ngày,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-10,A,Cup,10", "baseline");
    const event = await makeSource("stock.csv", "Thời điểm cập nhật,Mã nội bộ,Tên hàng,Tồn thực tế\n2026-11-11 00:00,A,Cup,9\n2026-11-11 02:00,A,Cup,6", "event");
    event.replay = { date: "2026-11-11", hours: [0, 2], lagHours: 0 };
    const files = await mergeReplaySources([baseline], [event]);
    const pack = await validatePack({ schema: "hackathon.sources.v1", files });
    expect(sourceView(visibleSources(pack, 0), 0).stock[0]?.stock).toBe(9);
    expect(sourceView(visibleSources(pack, 1), 1).stock[0]?.stock).toBe(9);
    expect(sourceView(visibleSources(pack, 2), 2).stock[0]?.stock).toBe(6);
    expect(readSource((await mergeReplaySources(files, [event]))[0]!).rows).toHaveLength(3);
  });
  it("delays ads by the replay lag and uses a shared persisted wall clock", async () => {
    const ads = await makeSource("ads.csv", "Thời gian,Mã chiến dịch,Chi phí,Lượt click\n2026-11-11 00:00,A,100,1", "event");
    ads.replay = { date: "2026-11-11", hours: [0], lagHours: 2 };
    const pack = { schema: "hackathon.sources.v1" as const, files: [ads] };
    expect(sourceView(visibleSources(pack, 1), 1).adRows).toBe(0);
    expect(sourceView(visibleSources(pack, 2), 2).adRows).toBe(1);
    const start = Date.parse("2026-10-31T00:00:00Z");
    expect(playbackHour({ hour: 0, event: "observe", startedAt: new Date(start).toISOString() }, start + 12000)).toBe(2);
    expect(playbackHour({ hour: 4, event: "observe" }, start)).toBe(4);
  });
  it("losslessly compresses large tables, strips contacts and validates decoded size and replay cardinality", async () => {
    const source = await makeSource("stock.csv", "Mã nội bộ,Tên hàng,Tồn thực tế,Người mua\n" + Array.from({ length: 20000 }, (_, i) => `${i},Large product name,10,Private person`).join("\n"), "baseline");
    expect(source.encoding).toBe("gzip-base64");
    const pack = await validatePack({ schema: "hackathon.sources.v1", files: [source] });
    expect(readSource(pack.files[0]!).rows).toHaveLength(20000);
    expect(sourceText(pack.files[0]!)).not.toContain("Private person");
    await expect(validatePack({ ...pack, files: [{ ...source, replay: { date: "2026-11-11", hours: [0], lagHours: 0 } }] })).rejects.toThrow("Replay must match");
  });
});
