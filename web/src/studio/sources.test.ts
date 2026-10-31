import { describe, expect, it } from "vitest";
import rawPack from "../../server/repository-sources.json";
import {
  inspect,
  makeSource,
  selectUploads,
  validatePack,
  type SourcePack,
} from "./sources";
import { sourceView } from "./source-view";
const pack = rawPack as SourcePack;
describe("six-source workspace contract", () => {
  it("accepts all six repository files and exposes their real shapes", async () => {
    const validated = await validatePack(pack);
    expect(validated.files).toHaveLength(6);
    expect(
      validated.files.map((f) => inspect(f.name, f.text).rows.length),
    ).toEqual([4156, 1176, 7176, 282, 49, 1]);
    expect(validated.files.every((f) => /^[a-f0-9]{64}$/.test(f.hash))).toBe(
      true,
    );
  });
  it("round-trips without adding records or silently fixing economic data", async () => {
    const a = await validatePack(pack),
      b = await validatePack(JSON.parse(JSON.stringify(a)));
    expect(b.files.map((f) => f.hash)).toEqual(a.files.map((f) => f.hash));
    const cost = inspect(a.files[3]!.name, a.files[3]!.text);
    expect(cost.issues.some((i) => i.code === "MONEY_FORMAT")).toBe(true);
  });
  it("strips contact columns and masks contacts in notes before persistence", async () => {
    const source = await makeSource(
      "export.csv",
      "Mã đơn hàng,SKU phân loại hàng,Số lượng,Ngày đặt hàng,Người mua,Số điện thoại,Ghi chú của người mua\nA,SKU1,1,2026-11-11 01:00,Private Person,0900123456,contact x@example.com 0900123456",
      "upload",
    );
    expect(source.text).not.toContain("Private Person");
    expect(source.text).not.toContain("0900123456");
    expect(source.text).not.toContain("x@example.com");
    expect(source.text).toContain("***");
  });
  it("keeps missing and negative stock unknown in city projections", () => {
    const testPack: SourcePack = {
      schema: "hackathon.sources.v1",
      files: [
        {
          ...pack.files[2]!,
          text: "Mã nội bộ,Tồn thực tế,Tên hàng,Thời điểm cập nhật\nA,-2,Apple,2026-11-11 01:00\nB,,Banana,2026-11-11 01:00\nA,5,Apple,2026-11-11 02:00",
        },
      ],
    };
    expect(sourceView(testPack, 1).stock.map((r) => r.stock)).toEqual([
      null,
      null,
    ]);
    expect(
      sourceView(testPack, 2).stock.find((r) => r.code === "A")?.stock,
    ).toBe(5);
    expect(
      inspect("stock.csv", testPack.files[0]!.text).issues.some(
        (i) => i.code === "NEG_STOCK",
      ),
    ).toBe(true);
  });
  it("rejects unknown, malformed, incomplete and duplicate source imports", async () => {
    expect(() => inspect("bad.csv", "a,b\n1,2")).toThrow(
      "unknown source format",
    );
    expect(() => inspect("bad.json", "{")).toThrow("JSON syntax");
    await expect(
      validatePack({ ...pack, files: pack.files.slice(0, 5) }),
    ).rejects.toThrow("six");
    await expect(
      validatePack({
        ...pack,
        files: pack.files.map((f, i) => (i === 5 ? pack.files[0] : f)),
      }),
    ).rejects.toThrow("Duplicate");
  });
  it("chooses live observations and excludes replay/history/oracle files", () => {
    const names = [
      "don_hang_seller_center.csv",
      "bao_cao_ads.csv",
      "ton_kho_google_sheet.csv",
    ];
    const files = [
      ...names.map((name) => ({ name, webkitRelativePath: `shop/${name}` })),
      ...pack.files.map((f) => ({
        name: f.name,
        webkitRelativePath: `shop/${f.origin}`,
      })),
      {
        name: "_replay.json",
        webkitRelativePath: "shop/live_11_11/_replay.json",
      },
    ];
    const selected = selectUploads(files).map(
      (i) => files[i]!.webkitRelativePath,
    );
    expect(selected).toHaveLength(6);
    expect(selected.filter((p) => p.includes("/live_11_11/"))).toHaveLength(3);
    expect(selected.some((p) => /replay|ground_truth/.test(p))).toBe(false);
  });
  it("rejects multiple shop folders instead of guessing", () => {
    const files = pack.files.flatMap((f) =>
      ["one", "two"].map((shop) => ({
        name: f.name,
        webkitRelativePath: `${shop}/${f.name}`,
      })),
    );
    expect(() => selectUploads(files)).toThrow("Multiple");
  });
});
