import * as T from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";

export function disposeObject(root: T.Object3D) {
  const geometries = new Set<T.BufferGeometry>(),
    materials = new Set<T.Material>(),
    textures = new Set<T.Texture>();
  root.traverse((o) => {
    if (!(o instanceof T.Mesh)) return;
    geometries.add(o.geometry);
    for (const material of Array.isArray(o.material)
      ? o.material
      : [o.material]) {
      materials.add(material);
      for (const value of Object.values(material))
        if (value instanceof T.Texture) textures.add(value);
    }
  });
  textures.forEach((t) => t.dispose());
  materials.forEach((m) => m.dispose());
  geometries.forEach((g) => g.dispose());
}
export function loadArchitecture(scene: T.Scene, host: HTMLElement) {
  let disposed = false,
    loaded: T.Group | null = null;
  const fallback = new T.Group();
  const material = new T.MeshStandardMaterial({
    color: "#b8c7ae",
    roughness: 0.85,
  });
  for (const [x, y, z, w, h, d] of [
    [0, 9, -3, 6, 18, 5],
    [-20, 3, 16, 17, 6, 8],
    [-20, 3, -21, 17, 6, 7],
    [20, 3, 17, 22, 6, 8],
  ]) {
    const mesh = new T.Mesh(new T.BoxGeometry(w, h, d), material);
    mesh.position.set(x!, y!, z!);
    fallback.add(mesh);
  }
  scene.add(fallback);
  host.dataset.assets = "loading";
  void new GLTFLoader()
    .setMeshoptDecoder(MeshoptDecoder)
    .loadAsync("/assets/saigon/district1.glb")
    .then((gltf) => {
      if (disposed) {
        disposeObject(gltf.scene);
        return;
      }
      loaded = gltf.scene;
      loaded.traverse((o) => {
        if (o instanceof T.Mesh) {
          o.castShadow = true;
          o.receiveShadow = true;
        }
      });
      scene.add(loaded);
      scene.remove(fallback);
      disposeObject(fallback);
      host.dataset.assets = "ready";
    })
    .catch(() => {
      if (!disposed) host.dataset.assets = "fallback";
    });
  return () => {
    disposed = true;
    if (loaded) {
      scene.remove(loaded);
      disposeObject(loaded);
    } else {
      scene.remove(fallback);
      disposeObject(fallback);
    }
  };
}
/** Original seeded surface texture, no external images or runtime network requests. */
export function surfaceTexture(kind: "asphalt" | "paving", anisotropy: number) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 512;
  const ctx = canvas.getContext("2d")!;
  const pixels = ctx.createImageData(512, 512);
  let seed = 71;
  for (let i = 0; i < pixels.data.length; i += 4) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    const n = (seed / 4294967296 - 0.5) * 18;
    const base = kind === "asphalt" ? [100, 110, 107] : [203, 205, 186];
    for (let c = 0; c < 3; c++) pixels.data[i + c] = base[c]! + n;
    pixels.data[i + 3] = 255;
  }
  ctx.putImageData(pixels, 0, 0);
  if (kind === "paving") {
    ctx.strokeStyle = "#a8b1a0";
    ctx.lineWidth = 2;
    for (let y = 0; y < 512; y += 64) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(512, y);
      ctx.stroke();
      for (let x = y % 128 ? 32 : 0; x < 512; x += 64) {
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x, y + 64);
        ctx.stroke();
      }
    }
  }
  const texture = new T.CanvasTexture(canvas);
  texture.colorSpace = T.SRGBColorSpace;
  texture.wrapS = texture.wrapT = T.RepeatWrapping;
  texture.repeat.set(12, 12);
  texture.anisotropy = anisotropy;
  return texture;
}
