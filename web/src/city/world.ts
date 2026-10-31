import * as T from "three";
import { loadArchitecture, surfaceTexture, disposeObject } from "./scene-assets";
import { landscape } from "./landscape";
import { createRiver } from "./river";
import { cityMotion } from "./city-motion";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { PLATFORM_IDS, PLATFORMS } from "./platforms";
import { createPlatformSign } from "./platform-sign";
export type District = "hq" | "warehouse" | "ads" | "homes";
export type WorldState = {
  signals: boolean;
  motion: boolean;
  quality: "balanced" | "high";
  platforms: string[];
  orders: number;
  adRows: number;
  occupancy: number;
};
const LOCATIONS: Record<District, [number, number, number]> = {
  hq: [0, 0, -3],
  warehouse: [-18, 0, 13],
  ads: [19, 0, -19],
  homes: [-19, 0, -18],
};
export function createCity(
  host: HTMLElement,
  state: WorldState,
  pick: (district: District) => void,
) {
  const scene = new T.Scene();
  scene.background = new T.Color("#eaf0e8");
  scene.fog = new T.Fog("#eaf0e8", 150, 280);
  const renderer = new T.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(
    Math.min(devicePixelRatio, state.quality === "high" ? 2 : 1.5),
  );
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFShadowMap;
  renderer.toneMapping = T.ACESFilmicToneMapping;
  host.append(renderer.domElement);
  renderer.domElement.setAttribute(
    "aria-label",
    "3D commerce city. Use district buttons to navigate.",
  );
  const camera = new T.PerspectiveCamera(36, 1, 0.1, 340);
  camera.position.set(106, 91, 118);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.46;
  controls.minDistance = 8;
  controls.maxDistance = 190;
  controls.target.set(12, 1, -2);
  const ambient = new T.HemisphereLight("#fff9e5", "#9eacaf", 1.8);
  scene.add(ambient);
  const sun = new T.DirectionalLight("#fff4d7", 2.6);
  sun.position.set(-25, 48, 35);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, {
    left: -85,
    right: 85,
    top: 65,
    bottom: -65,
    far: 220,
  });
  sun.shadow.normalBias = 0.05;
  scene.add(sun);
  const architecture = loadArchitecture(scene, host);
  const movers = cityMotion(scene);
  const river = createRiver(scene);
  const palms = landscape(scene);
  const materials = new Map<string, T.MeshStandardMaterial>();
  const mat = (color: string) => {
    let m = materials.get(color);
    if (!m) {
      m = new T.MeshStandardMaterial({ color, roughness: 0.8 });
      materials.set(color, m);
    }
    return m;
  };
  const roadTexture = surfaceTexture('asphalt', Math.min(4, renderer.capabilities.getMaxAnisotropy()));
  const pavingTexture = surfaceTexture('paving', Math.min(4, renderer.capabilities.getMaxAnisotropy()));
  mat('#788b91').map = roadTexture;
  mat('#d8dfd1').map = pavingTexture;
  const buckets = new Map<string, T.BufferGeometry[]>();
  const box = (
    group: T.Group | T.Scene,
    x: number,
    y: number,
    z: number,
    w: number,
    h: number,
    d: number,
    color: string,
    merge = true,
  ) => {
    const geo = new T.BoxGeometry(w, h, d);
    if (group === scene && merge) {
      geo.translate(x, y, z);
      const list = buckets.get(color) ?? [];
      list.push(geo);
      buckets.set(color, list);
      return null;
    }
    const mesh = new T.Mesh(geo, mat(color));
    mesh.position.set(x, y, z);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    group.add(mesh);
    return mesh;
  };
  box(scene, 16, -1.1, 0, 140, 1.6, 100, "#becfc4");
  box(scene, -3, 0.06, 0, 100, .12, 98, "#d8dfd1");
  box(scene, 76, 0.06, 0, 20, .12, 98, "#d8dfd1");
  for(const x of [-38,43,69]) box(scene,x,.14,0,3.8,.12,94,"#788b91");
  for(const z of [-35,35]) box(scene,-3,.14,z,96,.12,3.8,"#788b91");
  // Riverfront promenade, kept clear of the traffic lanes.
  box(scene,46,.18,0,2,.2,96,"#e3dbc1");
  box(scene,67,.18,0,2,.2,96,"#e3dbc1");
  for (const z of [-29, -7, 7, 28])
    box(scene, 0, 0.14, z, 78, 0.12, 3.8, "#788b91");
  for (const x of [-32, -7, 8, 31])
    box(scene, x, 0.14, 0, 3.8, 0.12, 68, "#788b91");
  for (const z of [-29, -7, 7, 28])
    for (let x = -36; x <= 36; x += 4)
      box(scene, x, 0.22, z, 1.6, 0.035, 0.1, "#e5e5ce");
  for (const x of [-32, -7, 8, 31])
    for (let z = -32; z < 34; z += 4)
      box(scene, x, 0.22, z, 0.1, 0.035, 1.6, "#e5e5ce");
  for (const x of [-9.5, 10.5])
    for (const z of [-29, -7, 7, 28])
      for (let i = -1.5; i < 2; i += 0.6)
        box(scene, x, 0.23, z + i, 1.4, 0.03, 0.28, "#f9f4dc");
  const targetMeshes: T.Object3D[] = [];
  const region = (
    id: District,
    x: number,
    z: number,
    w: number,
    d: number,
    color: string,
  ) => {
    const mesh = box(scene, x, 0.33, z, w, 0.25, d, color, false)!;
    mesh.userData.district = id;
    targetMeshes.push(mesh);
  };
  region("hq", 0, -2, 9.5, 9, "#d2e6df");
  region("warehouse", -19, 17, 20, 15, "#d9dec4");
  region("ads", 20, -18, 18, 17, "#e9d9c7");
  region("homes", -20, -19, 20, 15, "#dfdddf");
  const labelTextures: T.Texture[] = [];
  const label = (
    text: string,
    x: number,
    y: number,
    z: number,
    width: number,
    color = "#214b43",
  ) => {
    const canvas = document.createElement("canvas");
    canvas.width = 768;
    canvas.height = 128;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#fffdf4";
    ctx.fillRect(0, 0, 768, 128);
    ctx.fillStyle = color;
    ctx.font = "bold 58px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(text, 384, 85);
    const texture = new T.CanvasTexture(canvas);
    texture.colorSpace = T.SRGBColorSpace;
    texture.anisotropy = Math.min(4, renderer.capabilities.getMaxAnisotropy());
    labelTextures.push(texture);
    const mesh = new T.Mesh(
      new T.PlaneGeometry(width, width / 6),
      new T.MeshBasicMaterial({ map: texture, side: T.DoubleSide }),
    );
    mesh.position.set(x, y, z);
    scene.add(mesh);
    return mesh;
  };
  label("MEGADAY TWIN", 0, 5.8, .25, 6.8);
  label("BITEXCO",40,21,-16,5);
  label("SAIGON RIVER",56.5,.7,38,11);
  label("FULFILLMENT", -20, 6.2, 21.6, 10);
  const stockCubes = new T.InstancedMesh(new T.BoxGeometry(1, 1, 1), mat('#d3b88d'), 48);
  const stockTransform = new T.Object3D();
  for (let i = 0; i < 48; i++) {
    const rack = Math.floor(i / 12), level = Math.floor(i % 12 / 4), item = i % 4;
    stockTransform.position.set(-27.1 + rack * 4 + item % 2 * 1.4, 1.6 + level * 1.6, 17.4 + Math.floor(item / 2) * 1.3);
    stockTransform.updateMatrix(); stockCubes.setMatrixAt(i, stockTransform.matrix);
    stockCubes.setColorAt(i, new T.Color(['#e5bd80','#b5c7a4','#cca99a'][rack % 3]));
  }
  stockCubes.castShadow = true; scene.add(stockCubes);
  const platformGroups = new T.Group();
  scene.add(platformGroups);
  const platformSigns = new Map(PLATFORM_IDS.map((id) => [
    id, createPlatformSign(id, Math.min(4, renderer.capabilities.getMaxAnisotropy())),
  ]));
  let platformSignature = "";
  const updatePlatforms = () => {
    const signature = state.platforms.join();
    if (signature === platformSignature) return;
    platformSignature = signature;
    while (platformGroups.children.length) {
      const child = platformGroups.children[0]!;
      child.traverse((o) => {
        if (o instanceof T.Mesh) {
          o.geometry.dispose();
          if (o.material instanceof T.MeshBasicMaterial) {
            o.material.dispose();
          }
        }
      });
      platformGroups.remove(child);
    }
    // Fixed plots: adding/removing a platform never moves its neighbours.
    PLATFORM_IDS.forEach((id, index) => {
      if (!state.platforms.includes(id)) return;
      const brand = PLATFORMS[id];
      const x = 15 + (index % 2) * 8,
        z = -23 + Math.floor(index / 2) * 8,
        h = brand.height;
      box(platformGroups, x, 0.6, z, 6.2, 0.35, 5.9, brand.primary);
      box(
        platformGroups,
        x,
        0.6 + h / 2,
        z,
        5.5,
        h,
        5.2,
        brand.body,
      );
      box(platformGroups, x, h + 0.9, z, 6, 0.5, 5.8, brand.primary);
      for (let floor = 0; floor < 3; floor++) {
        const y = 1.8 + floor * (h - 1.5) / 3;
        for (let column = 0; column < 3; column++) {
          box(platformGroups, x - 1.7 + column * 1.7, y, z + 2.65, 1.05, 0.9, 0.1, brand.glass);
          box(platformGroups, x + 2.8, y, z - 1.65 + column * 1.65, 0.1, 0.9, 1.05, brand.glass);
        }
      }
      // Brand-coloured roof rails are legible even from the city overview.
      brand.accents.forEach((color, i) => {
        const segment = 6 / brand.accents.length;
        box(platformGroups, x - 3 + segment * (i + 0.5), h + 1.22, z + 2.7, segment, 0.2, 0.32, color);
        box(platformGroups, x + 2.8, 0.9 + h / 2, z - 2.1 + i * 0.38, 0.16, h, 0.2, color);
      });
      box(platformGroups, x, 1.25, z + 2.72, 1.1, 1.3, 0.16, brand.primary);
      box(platformGroups, x, h + 1.4, z, id === "meta" ? 4.5 : 3.5, 0.5, 3, brand.primary);
      const signMaterial = new T.MeshBasicMaterial({
        map: platformSigns.get(id)!.texture, toneMapped: false,
      });
      const front = new T.Mesh(new T.PlaneGeometry(6, 1.5), signMaterial);
      front.position.set(x, h + 2.1, z + 2.76);
      platformGroups.add(front);
      const side = new T.Mesh(new T.PlaneGeometry(4.8, 1.2), signMaterial);
      side.rotation.y = Math.PI / 2;
      side.position.set(x + 2.81, h - 0.25, z);
      platformGroups.add(side);
    });
    const grouped = new Map<T.Material, T.Mesh[]>();
    platformGroups.children.forEach(o => {
      if (o instanceof T.Mesh && o.material instanceof T.MeshStandardMaterial) {
        const meshes = grouped.get(o.material) ?? []; meshes.push(o); grouped.set(o.material, meshes);
      }
    });
    for (const [material, meshes] of grouped) {
      const geometries = meshes.map(mesh => { mesh.updateMatrix(); return mesh.geometry.clone().applyMatrix4(mesh.matrix); });
      const geometry = mergeGeometries(geometries);
      geometries.forEach(g => g.dispose());
      meshes.forEach(mesh => { platformGroups.remove(mesh); mesh.geometry.dispose(); });
      if (geometry) { const mesh = new T.Mesh(geometry, material); mesh.castShadow = true; mesh.receiveShadow = true; platformGroups.add(mesh); }
    }
  };
  updatePlatforms();
  label("ADS PLATFORM", 20, 0.8, -8.5, 13);
  // Trees use shared instanced geometry.
  const trunks = new T.InstancedMesh(
    new T.CylinderGeometry(0.15, 0.2, 1.6, 6),
    mat("#9e8c6f"),
    82,
  );
  const crowns = new T.InstancedMesh(
    new T.IcosahedronGeometry(1.1, 1),
    mat("#93b49a"),
    82,
  );
  const matrix = new T.Matrix4();
  for (let i = 0; i < 82; i++) {
    const edge = i % 4,
      t = (Math.floor(i / 4) / 20) * 66 - 33;
    const x = edge === 0 ? -37 : edge === 1 ? 37 : t,
      z = edge === 2 ? -33 : edge === 3 ? 33 : t;
    matrix.makeTranslation(x, 1.1, z);
    trunks.setMatrixAt(i, matrix);
    matrix.makeTranslation(x, 2.9, z);
    crowns.setMatrixAt(i, matrix);
  }
  crowns.castShadow = true;
  scene.add(trunks, crowns);
  const fountain = new T.Mesh(
    new T.CylinderGeometry(3.2, 3.5, 0.4, 32),
    mat("#c5d6d2"),
  );
  fountain.position.set(0, 0.5, 17);
  scene.add(fountain);
  const water = new T.Mesh(
    new T.CylinderGeometry(2.9, 2.9, 0.1, 32),
    mat("#94cbd0"),
  );
  water.position.set(0, 0.75, 17);
  scene.add(water);
  for (let i = 0; i < 12; i++) {
    const angle = (i / 12) * Math.PI * 2;
    box(
      scene,
      Math.sin(angle) * 4.5,
      0.8,
      17 + Math.cos(angle) * 4.5,
      0.6,
      0.7,
      0.6,
      "#e0b992",
    );
  }
  const signals = new T.Group();
  scene.add(signals);
  const tokens = Array.from({ length: 5 }, (_, i) => {
    const mesh = new T.Mesh(
      new T.OctahedronGeometry(0.45),
      new T.MeshStandardMaterial({
        color: ["#dfb65b", "#91bbac", "#e69c81"][i % 3],
        emissive: "#7c7950",
        emissiveIntensity: 0.1,
      }),
    );
    signals.add(mesh);
    return mesh;
  });
  for (const [color, list] of buckets) {
    const merged = mergeGeometries(list);
    if (merged) {
      const mesh = new T.Mesh(merged, mat(color));
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      scene.add(mesh);
    }
    list.forEach((g) => g.dispose());
  }
  const ray = new T.Raycaster(),
    mouse = new T.Vector2();
  let down = [0, 0];
  const onDown = (e: PointerEvent) => {
    down = [e.clientX, e.clientY];
  };
  const onClick = (e: PointerEvent) => {
    if (Math.hypot(e.clientX - down[0]!, e.clientY - down[1]!) > 6) return;
    const rect = renderer.domElement.getBoundingClientRect();
    mouse.set(
      ((e.clientX - rect.left) / rect.width) * 2 - 1,
      (-(e.clientY - rect.top) / rect.height) * 2 + 1,
    );
    ray.setFromCamera(mouse, camera);
    const hit = ray.intersectObjects(targetMeshes, false)[0];
    if (hit) pick(hit.object.userData.district);
  };
  renderer.domElement.addEventListener("pointerdown", onDown);
  renderer.domElement.addEventListener("pointerup", onClick);
  const observer = new ResizeObserver(() => {
    const w = host.clientWidth,
      h = host.clientHeight;
    renderer.setSize(w, h);
    camera.aspect = w / Math.max(h, 1);
    camera.updateProjectionMatrix();
  });
  observer.observe(host);
  let destination: T.Vector3 | null = null,
    eye: T.Vector3 | null = null;
  const timer = new T.Timer();
  timer.connect(document);
  let time = 0,
    frame = 0;
  renderer.setAnimationLoop(() => {
    timer.update();
    const dt = Math.min(timer.getDelta(), 0.05);
    if (document.hidden) return;
    if (state.motion) time += dt;
    if (destination && eye) {
      const speed = state.motion ? 1 - Math.exp(-dt * 3.5) : 1;
      camera.position.lerp(eye, speed);
      controls.target.lerp(destination, speed);
      if (camera.position.distanceTo(eye) < 0.05) {
        eye = null;
        destination = null;
      }
    }
    controls.update(dt);
    updatePlatforms();
    stockCubes.count = Math.ceil(48 * state.occupancy);
    river.update(time, state.motion);
    palms.update(time);
    movers.update(time, state.motion, camera.position.distanceTo(controls.target) < 65);
    water.scale.setScalar(1 + Math.sin(time * .7) * .008);
    signals.visible = state.signals && state.orders + state.adRows > 0;
    tokens.forEach((token, i) => {
      const period = 9 + i * 4,
        elapsed = (time + i * 2.7) % period;
      token.visible = elapsed < 3 && state.motion;
      const a = i % 2 ? LOCATIONS.ads : LOCATIONS.homes,
        b = i % 2 ? LOCATIONS.hq : LOCATIONS.warehouse,
        progress = Math.min(1, elapsed / 3);
      token.position.set(
        T.MathUtils.lerp(a[0], b[0], progress),
        3 + Math.sin(progress * Math.PI) * 5,
        T.MathUtils.lerp(a[2], b[2], progress),
      );
      token.rotation.y = time;
    });
    renderer.render(scene, camera);
    if (frame++ % 60 === 0) {
      host.dataset.drawCalls = String(renderer.info.render.calls);
      host.dataset.triangles = String(renderer.info.render.triangles);
      host.dataset.geometries = String(renderer.info.memory.geometries);
      host.dataset.textures = String(renderer.info.memory.textures);
    }
  });
  const focus = (district: District | null) => {
    const point = district ? LOCATIONS[district] : [12, 0, -2];
    destination = new T.Vector3(...(point as [number, number, number]));
    if (district === "ads") destination.y = 3;
    eye = destination
      .clone()
      .add(district === "ads"
        ? new T.Vector3(23, 22, 30)
        : district ? new T.Vector3(19, 20, 25) : new T.Vector3(94, 91, 120));
  };
  return {
    focus,
    zoom: (amount: number) => {
      camera.position
        .sub(controls.target)
        .multiplyScalar(amount)
        .add(controls.target);
    },
    update: (next: WorldState) => {
      state = next;
      renderer.setPixelRatio(
        Math.min(devicePixelRatio, state.quality === "high" ? 2 : 1.5),
      );
    },
    dispose: () => {
      renderer.setAnimationLoop(null);
      timer.dispose();
      controls.dispose();
      observer.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onClick);
      architecture();
      disposeObject(scene);
      materials.forEach(m => m.dispose());
      labelTextures.forEach(t => t.dispose());
      platformSigns.forEach(sign => sign.dispose());
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    },
  };
}
