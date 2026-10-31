import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import * as T from "three";
import { expect, it } from "vitest";
import { defaultSettings, settingsSchema } from "../studio/workspace-types";
import { setArchitectureNight, disposeObject } from "./scene-assets";

it("loads older account settings as morning and preserves a saved night preference", () => {
  const { appearance: _appearance, ...legacy } = defaultSettings;
  expect(_appearance).toBe("morning");
  expect(settingsSchema.parse(legacy).appearance).toBe("morning");
  expect(
    settingsSchema.parse({ ...legacy, appearance: "night" }).appearance,
  ).toBe("night");
  expect(
    settingsSchema.safeParse({ ...legacy, appearance: "sunset" }).success,
  ).toBe(false);
});
it("lights architectural glazing and lamps without changing masonry or material identity", () => {
  const materials = ["Saigon glass", "Saigon lamp", "Saigon ivory"].map(
    (name) => {
      const material = new T.MeshStandardMaterial();
      material.name = name;
      return material;
    },
  );
  const mesh = new T.Mesh(new T.BoxGeometry(), materials);
  setArchitectureNight(mesh, true);
  expect(materials.map((m) => m.emissiveIntensity)).toEqual([0.2, 2, 1]);
  expect(materials[2]!.emissive.getHex()).toBe(0);
  setArchitectureNight(mesh, false);
  expect(materials.map((m) => m.emissiveIntensity)).toEqual([0, 0, 1]);
  expect(mesh.material).toBe(materials);
  disposeObject(mesh);
});

it("ships named glass and lamp materials after GLB compression", () => {
  const glb = readFileSync(resolve("public/assets/saigon/district1.glb"));
  const length = glb.readUInt32LE(12);
  const json = JSON.parse(glb.subarray(20, 20 + length).toString("utf8"));
  const names = json.materials.map((m: { name: string }) => m.name);
  expect(names.some((name: string) => /Saigon glass/.test(name))).toBe(true);
  expect(names.some((name: string) => /Saigon lamp/.test(name))).toBe(true);
});
