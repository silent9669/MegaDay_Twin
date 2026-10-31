import * as T from "three";
import { expect, it, vi } from "vitest";
import { createMapLabels } from "./map-labels";

it("provides named map destinations and removes its controls on scene disposal", () => {
  const host = document.createElement("div"),
    activate = vi.fn();
  const labels = createMapLabels(host, activate);
  const river = host.querySelector<HTMLButtonElement>(
    '[aria-label="Visit Saigon River"]',
  )!;
  river.click();
  expect(activate).toHaveBeenCalledWith("river");
  host
    .querySelector<HTMLButtonElement>('[aria-label="Visit MegaDay Twin"]')!
    .click();
  expect(activate).toHaveBeenLastCalledWith("hq");
  expect(host.querySelectorAll("button")).toHaveLength(7);
  labels.dispose();
  expect(host.childElementCount).toBe(0);
});
it("hides offscreen labels and prioritizes the selected destination", () => {
  const host = document.createElement("div");
  Object.defineProperties(host, {
    clientWidth: { value: 1000 },
    clientHeight: { value: 800 },
  });
  const labels = createMapLabels(host, vi.fn());
  const camera = new T.PerspectiveCamera(36, 1.25, 0.1, 340);
  camera.position.set(0, 28, 50);
  camera.lookAt(0, 7, -3);
  camera.updateMatrixWorld();
  labels.update(camera, "hq");
  const hq = host.querySelector<HTMLButtonElement>('[data-place="hq"]')!;
  expect(hq.hidden).toBe(false);
  expect(hq.getAttribute("aria-current")).toBe("true");
  camera.lookAt(0, 28, 100);
  camera.updateMatrixWorld();
  labels.update(camera, null);
  expect(hq.hidden).toBe(true);
  labels.dispose();
});
it("keeps a selected landmark label reachable below the title on narrow screens", () => {
  const host = document.createElement("div");
  Object.defineProperties(host, {
    clientWidth: { value: 334 },
    clientHeight: { value: 763 },
  });
  const labels = createMapLabels(host, vi.fn());
  const camera = new T.PerspectiveCamera(36, 334 / 763, 0.1, 340);
  camera.position.set(132, 52, 56);
  camera.lookAt(77, 17, -9);
  camera.updateMatrixWorld();
  labels.update(camera, "landmark81");
  expect(
    host.querySelector<HTMLButtonElement>('[data-place="landmark81"]')!.hidden,
  ).toBe(false);
  labels.dispose();
});
