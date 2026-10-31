import * as T from "three";
import type { District } from "./world";
export type Landmark = "river" | "bitexco" | "landmark81" | "benthanh";
export type MapPlace = District | Landmark;
const PLACES: {
  id: MapPlace;
  title: string;
  position: [number, number, number];
}[] = [
  { id: "hq", title: "MegaDay Twin", position: [0, 7, -3] },
  { id: "river", title: "Saigon River", position: [56.5, 6, 30] },
  { id: "landmark81", title: "Landmark 81", position: [77, 40, -9] },
  { id: "bitexco", title: "Bitexco", position: [40, 23, -19] },
  { id: "benthanh", title: "Ben Thanh Market", position: [20, 11, 20] },
  { id: "warehouse", title: "Warehouse", position: [-20, 9, 18] },
  { id: "ads", title: "Ad platforms", position: [20, 12, -18] },
  { id: "homes", title: "Neighborhood", position: [-19, 12, 2] },
];
/** Screen-space buttons stay legible while their anchors follow the 3D scene. */
export function createMapLabels(
  host: HTMLElement,
  activate: (place: MapPlace) => void,
) {
  const layer = document.createElement("div");
  layer.className = "city-map-labels";
  layer.setAttribute("role", "group");
  layer.setAttribute("aria-label", "Map locations");
  const entries = PLACES.map((place) => {
    const button = document.createElement("button");
    button.className = "city-map-pin";
    button.setAttribute("aria-label", `Visit ${place.title}`);
    button.dataset.place = place.id;
    const text = document.createElement("span");
    text.textContent = place.title;
    button.append(text);
    button.addEventListener("click", () => activate(place.id));
    layer.append(button);
    return { ...place, button, anchor: new T.Vector3(...place.position) };
  });
  host.append(layer);
  const projected = new T.Vector3();
  return {
    update(camera: T.Camera, selected: MapPlace | null) {
      const width = host.clientWidth,
        height = host.clientHeight;
      const occupied: { x: number; y: number }[] = [];
      // Selected labels win collisions; the rest retain their stable priority order.
      const ordered = selected
        ? [
            ...entries.filter((e) => e.id === selected),
            ...entries.filter((e) => e.id !== selected),
          ]
        : entries;
      for (const entry of ordered) {
        projected.copy(entry.anchor).project(camera);
        let x = ((projected.x + 1) * width) / 2,
          y = ((1 - projected.y) * height) / 2;
        if (
          entry.id === selected &&
          Math.abs(projected.x) < 1.2 &&
          Math.abs(projected.y) < 1.2
        ) {
          x = T.MathUtils.clamp(x, 81, width - 81);
          y = T.MathUtils.clamp(y, x < 360 ? 151 : 61, height - 131);
        }
        const focused = document.activeElement === entry.button;
        const visible =
          projected.z > -1 &&
          projected.z < 1 &&
          x > 80 &&
          x < width - 80 &&
          y > 60 &&
          y < height - 130 &&
          !(x < 360 && y < 150) &&
          (focused ||
            (occupied.length < (width < 600 ? 3 : PLACES.length) &&
              !occupied.some(
                (p) => Math.abs(p.x - x) < 155 && Math.abs(p.y - y) < 52,
              )));
        entry.button.hidden = !visible;
        entry.button.setAttribute(
          "aria-current",
          String(entry.id === selected),
        );
        if (!visible) continue;
        occupied.push({ x, y });
        entry.button.style.transform = `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,0) translate(-50%,-100%)`;
      }
    },
    dispose: () => layer.remove(),
  };
}
