import * as T from "three";
export type Surface = "plaster" | "roof" | "wood" | "fabric" | "rubber" | "paint" | "livery";
/** Original repeatable detail; no remote textures, and scalar maps stay linear. */
export function detailTexture(surface: Surface, scalar = false) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 128;
  const ctx = canvas.getContext("2d")!;
  const pixels = ctx.createImageData(128, 128);
  let seed = 317;
  for (let y = 0; y < 128; y++) for (let x = 0; x < 128; x++) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const noise = seed / 4294967296;
    let value = 238 + noise * 16;
    if (surface === "roof") value = y % 32 < 2 || (x + (Math.floor(y / 32) % 2) * 8) % 16 < 2 ? 155 : 210 + Math.sin(x * Math.PI / 8) * 20 + noise * 12;
    if (surface === "wood") value = 205 + Math.sin(x * .7 + Math.sin(y * .07) * 2) * 20 + noise * 14;
    if (surface === "fabric") value = 222 + (x % 3 === 0 || y % 3 === 0 ? 0 : 24) + noise * 8;
    if (surface === "rubber") value = (x + Math.floor(y / 8) * 4) % 16 < 4 ? 150 : 220 + noise * 25;
    const offset = (y * 128 + x) * 4;
    pixels.data.set([value, value, value, 255], offset);
  }
  ctx.putImageData(pixels, 0, 0);
  if (surface === "livery") {
    ctx.fillStyle = "#edf0e4"; ctx.fillRect(0, 0, 128, 128);
    ctx.fillStyle = "#326752"; ctx.fillRect(0, 80, 128, 30);
    ctx.font = "bold 19px sans-serif"; ctx.textAlign = "center";
    ctx.fillText("MEGADAY", 64, 61);
  }
  const texture = new T.CanvasTexture(canvas);
  texture.colorSpace = scalar ? T.NoColorSpace : T.SRGBColorSpace;
  texture.wrapS = texture.wrapT = T.RepeatWrapping;
  texture.anisotropy = 4;
  return texture;
}
/** Metre-scaled planar detail UVs preserve the existing baked AO atlas on UV0. */
export function architectureDetailUV(geometry: T.BufferGeometry) {
  if (geometry.hasAttribute("uv1")) return;
  const position = geometry.getAttribute("position"), normal = geometry.getAttribute("normal");
  if (!position || !normal) return;
  const uv = new Float32Array(position.count * 2);
  for (let i = 0; i < position.count; i++) {
    const x = Math.abs(normal.getX(i)), y = Math.abs(normal.getY(i)), z = Math.abs(normal.getZ(i));
    uv[i * 2] = (x > y && x > z ? position.getZ(i) : position.getX(i)) / 2;
    uv[i * 2 + 1] = (y >= x && y >= z ? position.getZ(i) : position.getY(i)) / 2;
  }
  geometry.setAttribute("uv1", new T.BufferAttribute(uv, 2));
}
export function textureArchitecture(root: T.Object3D) {
  const maps = new Map<Surface, { color: T.Texture; relief: T.Texture }>();
  root.traverse(object => {
    if (!(object instanceof T.Mesh)) return;
    architectureDetailUV(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      if (!(material instanceof T.MeshStandardMaterial)) continue;
      const name = material.name;
      const surface: Surface | null = /terracotta/.test(name) ? "roof" : /wood/.test(name) ? "wood" : /ivory|plaster|ochre|stone|cream/.test(name) ? "plaster" : null;
      if (!surface) continue;
      if (!maps.has(surface)) {
        const color = detailTexture(surface), relief = detailTexture(surface, true);
        color.channel = relief.channel = 1;
        maps.set(surface, { color, relief });
      }
      const textures = maps.get(surface)!;
      material.map = textures.color;
      material.bumpMap = textures.relief;
      material.bumpScale = surface === "roof" ? .09 : surface === "wood" ? .025 : .015;
      material.roughness = surface === "wood" ? .76 : .88;
      material.needsUpdate = true;
    }
  });
}
