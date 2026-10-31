import * as T from "three";
import { expect, it } from "vitest";
import { architectureDetailUV } from "./detail-textures";
it("adds metre-scaled detail UVs without replacing the baked occlusion coordinates", () => {
  const geometry = new T.BoxGeometry(4, 6, 2);
  const original = Array.from(geometry.getAttribute("uv").array);
  architectureDetailUV(geometry);
  expect(Array.from(geometry.getAttribute("uv").array)).toEqual(original);
  const detail = geometry.getAttribute("uv1");
  expect(detail.count).toBe(geometry.getAttribute("position").count);
  expect(Array.from(detail.array).every(Number.isFinite)).toBe(true);
  expect(Math.max(...Array.from(detail.array))).toBeGreaterThan(1);
  architectureDetailUV(geometry);
  expect(geometry.getAttribute("uv1")).toBe(detail);
  geometry.dispose();
});
