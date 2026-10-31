import * as T from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
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
  ads: [19, 0, -13],
  homes: [-19, 0, -18],
};
export function createCity(
  host: HTMLElement,
  state: WorldState,
  pick: (district: District) => void,
) {
  const scene = new T.Scene();
  scene.background = new T.Color("#eaf0e8");
  scene.fog = new T.Fog("#eaf0e8", 95, 190);
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
  const camera = new T.PerspectiveCamera(36, 1, 0.1, 240);
  camera.position.set(69, 62, 76);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.46;
  controls.minDistance = 8;
  controls.maxDistance = 130;
  controls.target.set(0, 1, -2);
  const ambient = new T.HemisphereLight("#fff9e5", "#9eacaf", 2.4);
  scene.add(ambient);
  const sun = new T.DirectionalLight("#fff4d7", 3.5);
  sun.position.set(-25, 48, 35);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, {
    left: -52,
    right: 52,
    top: 52,
    bottom: -52,
    far: 140,
  });
  sun.shadow.normalBias = 0.05;
  scene.add(sun);
  const materials = new Map<string, T.MeshStandardMaterial>();
  const mat = (color: string) => {
    let m = materials.get(color);
    if (!m) {
      m = new T.MeshStandardMaterial({ color, roughness: 0.8 });
      materials.set(color, m);
    }
    return m;
  };
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
  box(scene, 0, -0.8, 0, 84, 1.6, 74, "#becfc4");
  box(scene, 0, 0.06, 0, 79, 0.12, 69, "#d8dfd1");
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
  box(scene, 0, 9, -3, 6.3, 17, 5.5, "#8ebbb3");
  box(scene, 0, 18, -3, 4.8, 1.2, 4.5, "#d6e9dd");
  box(scene, 0, 19, -3, 2, 0.8, 2, "#afcfbe");
  for (let floor = 0; floor < 8; floor++) {
    box(scene, 0, 1.5 + floor * 2, -0.19, 6.4, 0.18, 0.15, "#d9ece3");
    for (const x of [-2, -0.7, 0.7, 2])
      box(
        scene,
        x,
        2 + floor * 2,
        -0.18,
        0.85,
        1.1,
        0.1,
        floor % 3 === 0 ? "#e9ce87" : "#527a7b",
      );
    for (const z of [-4.8, -3.4, -2, -0.7])
      box(scene, 3.2, 2 + floor * 2, z, 0.08, 1.1, 0.85, "#527a7b");
  }
  label("MEGADAY TWIN", 0, 17, -0.05, 6.5);
  for (const [x, z] of [
    [-3.7, 2.4],
    [3.7, 2.4],
  ])
    box(scene, x!, 0.9, z!, 1.2, 1, 1.2, "#e8dab3");
  // Open-front warehouse: racks and stock are visible from the default camera.
  box(scene, -20, 0.65, 18, 17, 0.6, 10, "#e9e4d4");
  box(scene, -20, 4, 13.3, 17, 6, 0.4, "#d7bfa5");
  box(scene, -28.3, 4, 18, 0.4, 6, 10, "#d7bfa5");
  box(scene, -20, 7.4, 17, 18.2, 0.6, 8, "#9baba0");
  label("FULFILLMENT", -20, 6.2, 21.4, 10);
  const stockCubes: T.Mesh[] = [];
  for (let rack = 0; rack < 4; rack++) {
    const x = -26 + rack * 4;
    for (const dx of [-1.5, 1.5])
      box(scene, x + dx, 2.7, 17.6, 0.18, 4.8, 2.8, "#687e7c");
    for (let level = 0; level < 3; level++) {
      box(scene, x, 1.05 + level * 1.6, 18, 3.4, 0.15, 3, "#c5b37d");
      for (let item = 0; item < 4; item++)
        stockCubes.push(
          box(
            scene,
            x - 1.1 + (item % 2) * 1.4,
            1.6 + level * 1.6,
            17.4 + Math.floor(item / 2) * 1.3,
            1,
            1,
            1,
            ["#d3ab79", "#b0cdb9", "#c7b1bf"][rack % 3]!,
            false,
          )!,
        );
    }
  }
  const platformGroups = new T.Group();
  scene.add(platformGroups);
  const platformColors: Record<string, string> = {
    shopee: "#ec886c",
    tiktok: "#6f8d91",
    meta: "#91b1d6",
    google: "#dcb975",
  };
  const platformNames: Record<string, string> = {
    shopee: "Shopee Ads",
    tiktok: "TikTok Ads",
    meta: "Meta Ads",
    google: "Google Ads",
  };
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
            o.material.map?.dispose();
            o.material.dispose();
          }
        }
      });
      platformGroups.remove(child);
    }
    state.platforms.forEach((id, index) => {
      const x = 15 + (index % 2) * 8,
        z = -23 + Math.floor(index / 2) * 8,
        h = id === "shopee" ? 7 : 5;
      box(
        platformGroups,
        x,
        0.6 + h / 2,
        z,
        5.5,
        h,
        5.2,
        platformColors[id] ?? "#c8c6b8",
      );
      box(platformGroups, x, h + 1, z, 6, 0.35, 5.8, "#f3e7d2");
      for (let i = 0; i < 3; i++)
        box(
          platformGroups,
          x - 1.7 + i * 1.7,
          h / 2,
          z + 2.65,
          0.9,
          h - 1.5,
          0.08,
          "#dcebe5",
        );
      const sign = label(platformNames[id] ?? id, x, h + 1.7, z + 2.75, 6);
      scene.remove(sign);
      platformGroups.add(sign);
    });
  };
  updatePlatforms();
  label("ADS PLATFORM", 20, 0.8, -8.5, 13);
  const houses = [
    "#e4b9ac",
    "#b4c5d7",
    "#cebea2",
    "#bad0b9",
    "#c4b5cf",
    "#eccfae",
  ];
  for (let i = 0; i < 18; i++) {
    const x = i < 9 ? -27 + (i % 3) * 6.5 : 15 + (i % 3) * 6.5,
      z =
        i < 9
          ? -23 + Math.floor(i / 3) * 5.6
          : 12 + Math.floor((i - 9) / 3) * 5.5,
      h = 2.5 + (i % 3);
    box(scene, x, 0.6 + h / 2, z, 4.2, h, 3.5, houses[i % houses.length]!);
    box(scene, x, h + 0.9, z, 4.6, 0.55, 3.9, "#8a9f99");
    box(scene, x, 1.4, z + 1.8, 0.9, 1.6, 0.1, "#567c76");
    for (const dx of [-1.3, 1.3])
      box(scene, x + dx, h, z + 1.8, 0.9, 0.8, 0.1, "#f9df9b");
  }
  // Trees use shared instanced geometry; all assets are authored in this repo.
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
  const vehicles: { group: T.Group; offset: number; speed: number }[] = [];
  for (let i = 0; i < 9; i++) {
    const group = new T.Group();
    const van = i < 3;
    box(
      group,
      0,
      0.75,
      0,
      van ? 1.5 : 1.2,
      1,
      van ? 2.8 : 2.2,
      ["#efcd9b", "#b3c8d5", "#d5b4a9"][i % 3]!,
      false,
    );
    box(group, 0, 1.3, 0.5, 1.15, 0.55, 1.1, "#719a9d", false);
    for (const x of [-0.7, 0.7])
      for (const z of [-0.8, 0.8]) {
        const wheel = new T.Mesh(
          new T.CylinderGeometry(0.28, 0.28, 0.2, 10),
          mat("#4a5c5b"),
        );
        wheel.rotation.z = Math.PI / 2;
        wheel.position.set(x, 0.36, z);
        group.add(wheel);
      }
    scene.add(group);
    vehicles.push({ group, offset: i * 14, speed: van ? 1.7 : 2.4 });
  }
  const people: {
    group: T.Group;
    legs: T.Mesh[];
    phase: number;
    offset: number;
  }[] = [];
  for (let i = 0; i < 14; i++) {
    const group = new T.Group();
    box(
      group,
      0,
      1.1,
      0,
      0.48,
      0.65,
      0.3,
      i % 2 ? "#d1a397" : "#7aa899",
      false,
    );
    const head = new T.Mesh(new T.SphereGeometry(0.24, 8, 8), mat("#dfb78f"));
    head.position.y = 1.65;
    group.add(head);
    const legs = [-0.13, 0.13].map((x) =>
      box(group, x, 0.48, 0, 0.17, 0.65, 0.2, "#577477", false)!,
    );
    scene.add(group);
    people.push({ group, legs, phase: i * 0.8, offset: i * 4 });
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
    stockCubes.forEach((cube, i) => {
      cube.visible = i / stockCubes.length < state.occupancy;
    });
    const route = [
      new T.Vector3(-30.8, 0, 28),
      new T.Vector3(30.8, 0, 28),
      new T.Vector3(30.8, 0, -29),
      new T.Vector3(-30.8, 0, -29),
    ];
    vehicles.forEach(({ group, offset, speed }) => {
      let distance = (time * speed + offset) % 237.2;
      for (let i = 0; i < 4; i++) {
        const a = route[i]!,
          b = route[(i + 1) % 4]!,
          length = a.distanceTo(b);
        if (distance < length) {
          group.position.lerpVectors(a, b, distance / length);
          group.rotation.y = Math.atan2(b.x - a.x, b.z - a.z);
          break;
        }
        distance -= length;
      }
    });
    people.forEach(({ group, legs, phase, offset }, i) => {
      const travel = (time * 0.55 + offset) % 34;
      group.position.set(
        i < 7 ? -29 : 10.6,
        0.22,
        i < 7 ? travel - 4 : -travel + 28,
      );
      group.rotation.y = i < 7 ? 0 : Math.PI;
      legs.forEach((leg, n) => {
        leg.rotation.x = state.motion
          ? Math.sin(time * 5 + phase + n * Math.PI) * 0.38
          : 0;
      });
    });
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
    if (frame++ === 10)
      host.dataset.drawCalls = String(renderer.info.render.calls);
  });
  const focus = (district: District | null) => {
    const point = district ? LOCATIONS[district] : [0, 0, -2];
    destination = new T.Vector3(...(point as [number, number, number]));
    eye = destination
      .clone()
      .add(district ? new T.Vector3(19, 20, 25) : new T.Vector3(69, 62, 78));
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
      scene.traverse((o) => {
        if (o instanceof T.Mesh) {
          o.geometry.dispose();
          if (o.material instanceof T.MeshBasicMaterial) o.material.dispose();
        }
      });
      materials.forEach((m) => m.dispose());
      labelTextures.forEach((t) => t.dispose());
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    },
  };
}
