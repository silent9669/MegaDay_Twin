import * as T from "three";

function roundedRoute(x: number, z: number, radius: number) {
  const path = new T.CurvePath<T.Vector3>();
  const p = (a: number, b: number) => new T.Vector3(a, 0.23, b);
  const line = (a: T.Vector3, b: T.Vector3) => path.add(new T.LineCurve3(a, b));
  const corner = (a: T.Vector3, b: T.Vector3, c: T.Vector3) =>
    path.add(new T.QuadraticBezierCurve3(a, b, c));
  line(p(-x + radius, z), p(x - radius, z));
  corner(p(x - radius, z), p(x, z), p(x, z - radius));
  line(p(x, z - radius), p(x, -z + radius));
  corner(p(x, -z + radius), p(x, -z), p(x - radius, -z));
  line(p(x - radius, -z), p(-x + radius, -z));
  corner(p(-x + radius, -z), p(-x, -z), p(-x, -z + radius));
  line(p(-x, -z + radius), p(-x, z - radius));
  corner(p(-x, z - radius), p(-x, z), p(-x + radius, z));
  return path;
}
export function cityMotion(scene: T.Scene) {
  const root = new T.Group();
  scene.add(root);
  const object = new T.Object3D(),
    position = new T.Vector3(),
    direction = new T.Vector3();
  const materials = new Map<string, T.MeshStandardMaterial>();
  const mat = (color: string) => {
    if (!materials.has(color))
      materials.set(
        color,
        new T.MeshStandardMaterial({ color, roughness: 0.7 }),
      );
    return materials.get(color)!;
  };
  const batch = (geometry: T.BufferGeometry, color: string, count: number) => {
    const m = new T.InstancedMesh(geometry, mat(color), count);
    m.instanceMatrix.setUsage(T.DynamicDrawUsage);
    m.frustumCulled = false;
    m.castShadow = true;
    root.add(m);
    return m;
  };
  const body = batch(new T.BoxGeometry(0.55, 0.65, 1.35), "#9baf9a", 7);
  const seats = batch(new T.BoxGeometry(0.48, 0.14, 0.75), "#394a43", 7);
  const fronts = batch(new T.BoxGeometry(0.65, 0.8, 0.17), "#b69b6e", 7);
  const riders = batch(new T.CapsuleGeometry(0.2, 0.45, 3, 6), "#476e62", 7);
  const helmets = batch(new T.SphereGeometry(0.25, 8, 6), "#ebd5a8", 7);
  const wheels = batch(
    new T.CylinderGeometry(0.28, 0.28, 0.13, 10).rotateZ(Math.PI / 2),
    "#354941",
    26,
  );
  const hubs = batch(new T.BoxGeometry(0.15, 0.08, 0.42), "#bbc2ae", 26);
  const vans = batch(new T.BoxGeometry(1.4, 1.3, 2.6), "#d2b98c", 3);
  const glass = batch(new T.BoxGeometry(1.42, 0.48, 0.7), "#527b78", 3);
  const pedestrianBodies = batch(
    new T.CapsuleGeometry(0.19, 0.45, 3, 6),
    "#ffffff",
    24,
  );
  const heads = batch(new T.SphereGeometry(0.22, 8, 6), "#d4b48c", 24);
  const legs = batch(new T.CapsuleGeometry(0.075, 0.46, 2, 5), "#47645c", 48);
  const arms = batch(new T.CapsuleGeometry(0.065, 0.36, 2, 5), "#c79e84", 48);
  const hair = batch(new T.SphereGeometry(0.225, 8, 5, 0, Math.PI * 2, 0, Math.PI * 0.55), "#332e2a", 24);
  const shoes = batch(new T.BoxGeometry(0.16, 0.12, 0.29), "#eee1cb", 48);
  const bags = batch(new T.BoxGeometry(0.26, 0.35, 0.13), "#a58861", 24);
  const shirts = ["#548477", "#d6b76e", "#e4d9c2", "#b77760", "#68869a", "#8e8c71"];
  for (let i = 0; i < 24; i++) {
    pedestrianBodies.setColorAt(i, new T.Color(shirts[i % shirts.length]));
    heads.setColorAt(i, new T.Color(i % 3 === 0 ? "#c39c80" : "#f1d8b5"));
  }
  const route = roundedRoute(31, 28, 4),
    walk = roundedRoute(28.3, 25.4, 3),
    length = route.getLength();
  const walkLength = walk.getLength();
  const place = (
    mesh: T.InstancedMesh,
    index: number,
    x: number,
    y: number,
    z: number,
    heading: number,
    lean = 0,
    roll = 0,
  ) => {
    object.position.copy(position);
    object.rotation.set(0, heading, lean);
    object.translateX(x);
    object.translateY(y);
    object.translateZ(z);
    object.rotateX(roll);
    object.updateMatrix();
    mesh.setMatrixAt(index, object.matrix);
  };
  function update(time: number, motion: boolean, near: boolean) {
    for (let i = 0; i < 10; i++) {
      const speed = i < 7 ? 2.2 : 1.5,
        progress = ((time * speed + (i * length) / 10) % length) / length;
      route.getPointAt(progress, position);
      route.getTangentAt(progress, direction);
      const heading = Math.atan2(direction.x, direction.z);
      const next = route.getTangentAt((progress + 0.002) % 1);
      const turn = T.MathUtils.clamp(
        direction.x * next.z - direction.z * next.x,
        -0.14,
        0.14,
      );
      if (i < 7) {
        place(body, i, 0, 0.55, 0, heading, turn);
        place(seats, i, 0, 0.95, -0.15, heading, turn);
        place(fronts, i, 0, 0.8, 0.6, heading, turn);
        place(riders, i, 0, 1.3, -0.12, heading, turn);
        place(helmets, i, 0, 1.85, -0.08, heading, turn);
      } else {
        place(vans, i - 7, 0, 0.95, 0, heading);
        place(glass, i - 7, 0, 1.35, 0.77, heading);
      }
      const n = i < 7 ? 2 : 4,
        start = i < 7 ? i * 2 : 14 + (i - 7) * 4;
      for (let w = 0; w < n; w++) {
        const x = i < 7 ? 0 : w % 2 ? -0.72 : 0.72,
          z = (w < n / 2 ? -0.85 : 0.85) * (i < 7 ? 0.72 : 1);
        place(wheels, start + w, x, 0.3, z, heading, 0, (time * speed) / 0.28);
        place(hubs, start + w, x, 0.3, z, heading, 0, (time * speed) / 0.28);
      }
    }
    for (let i = 0; i < 24; i++) {
      const t =
        ((time * 0.5 + (i * walkLength) / 14) % walkLength) /
        walkLength;
      walk.getPointAt(t, position);
      walk.getTangentAt(t, direction);
      if (i >= 14) {
        const travel = (time * .6 + (i - 14) * 12.6) % 126;
        position.set(i % 2 ? 67.1 : 46, .23, -38 + (travel < 63 ? travel : 126 - travel));
        direction.set(0, 0, travel < 63 ? 1 : -1);
      }
      const heading = Math.atan2(direction.x, direction.z),
        stride = motion ? Math.sin(time * 4 + i) * 0.45 : 0;
      const bounce = motion ? Math.abs(Math.sin(time * 4 + i)) * 0.035 : 0;
      place(pedestrianBodies, i, 0, 1.02 + bounce, 0, heading);
      place(heads, i, 0, 1.6 + bounce, 0, heading);
      place(hair, i, 0, 1.61 + bounce, 0, heading);
      place(bags, i, 0.16, 0.95 + bounce, -0.22, heading);
      for (let n = 0; n < 2; n++) {
        const swing = stride * (n ? 1 : -1);
        place(
          legs,
          i * 2 + n,
          n ? 0.12 : -0.12,
          0.44,
          Math.sin(swing) * 0.18,
          heading,
          0,
          swing,
        );
        place(shoes, i * 2 + n, n ? 0.12 : -0.12, 0.12 + Math.max(0, Math.sin(swing)) * 0.12, Math.sin(swing) * 0.4 + 0.05, heading);
        place(
          arms,
          i * 2 + n,
          n ? 0.27 : -0.27,
          1.05,
          -Math.sin(swing) * 0.17,
          heading,
          0,
          -swing,
        );
      }
    }
    hubs.visible = near;
    arms.visible = near;
    bags.visible = near;
    root.children.forEach((m) => {
      if (m instanceof T.InstancedMesh) m.instanceMatrix.needsUpdate = true;
    });
  }
  return { update };
}
