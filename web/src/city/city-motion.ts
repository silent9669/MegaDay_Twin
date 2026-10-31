import * as T from "three";
import { detailTexture, type Surface } from "./detail-textures";

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
  const textures = new Map<Surface, T.Texture>();
  const mat = (color: string, surface?: Surface) => {
    const key = `${color}:${surface ?? ""}`;
    if (surface && !textures.has(surface)) textures.set(surface, detailTexture(surface));
    if (!materials.has(key))
      materials.set(
        key,
        new T.MeshStandardMaterial({ color, roughness: surface === "paint" ? .36 : .8, map: surface ? textures.get(surface)! : null }),
      );
    return materials.get(key)!;
  };
  const batch = (geometry: T.BufferGeometry, color: string, count: number, surface?: Surface) => {
    const m = new T.InstancedMesh(geometry, mat(color, surface), count);
    m.instanceMatrix.setUsage(T.DynamicDrawUsage);
    m.frustumCulled = false;
    m.castShadow = true;
    root.add(m);
    return m;
  };
  const body = batch(new T.BoxGeometry(0.55, 0.65, 1.35), "#ffffff", 7, "paint");
  const seats = batch(new T.BoxGeometry(0.48, 0.14, 0.75), "#394a43", 7);
  const fronts = batch(new T.BoxGeometry(0.65, 0.8, 0.17), "#ffffff", 7, "paint");
  const riders = batch(new T.CapsuleGeometry(0.2, 0.45, 3, 6), "#476e62", 7);
  const helmets = batch(new T.SphereGeometry(0.25, 8, 6), "#ebd5a8", 7);
  const riderArms = batch(new T.CapsuleGeometry(.07,.3,2,6), "#476e62", 14, "fabric");
  const riderLegs = batch(new T.CapsuleGeometry(.08,.32,2,6), "#46556b", 14, "fabric");
  const wheels = batch(
    new T.CylinderGeometry(0.28, 0.28, 0.13, 10).rotateZ(Math.PI / 2),
    "#354941",
    26, "rubber",
  );
  const hubs = batch(new T.BoxGeometry(0.15, 0.08, 0.42), "#bbc2ae", 26);
  const vans = batch(new T.BoxGeometry(1.4, 1.3, 2.6), "#ffffff", 3, "paint");
  const glass = batch(new T.BoxGeometry(1.42, 0.48, 0.7), "#527b78", 3);
  const windshields = batch(new T.BoxGeometry(1.12,.44,.03), "#527b78", 3);
  const graphics = batch(new T.BoxGeometry(1.415,.55,1.5), "#ffffff", 3, "livery");
  const pedestrianBodies = batch(
    new T.CylinderGeometry(0.19, 0.25, 0.58, 8),
    "#ffffff",
    24, "fabric",
  );
  const heads = batch(new T.SphereGeometry(0.22, 8, 6), "#ffffff", 24);
  const legs = batch(new T.CapsuleGeometry(0.085, 0.2, 2, 6), "#ffffff", 48, "fabric");
  const shins = batch(new T.CapsuleGeometry(0.07, 0.2, 2, 6), "#ffffff", 48, "fabric");
  const arms = batch(new T.CapsuleGeometry(0.085, 0.15, 2, 6), "#ffffff", 48, "fabric");
  const forearms = batch(new T.CapsuleGeometry(0.06, 0.16, 2, 6), "#ffffff", 48);
  const eyes = batch(new T.SphereGeometry(.026, 5, 4), "#302c29", 48);
  const noses = batch(new T.SphereGeometry(.055, 5, 4), "#ffffff", 24);
  const collars = batch(new T.TorusGeometry(.105, .022, 4, 8).rotateX(Math.PI/2), "#eee5d5", 24);
  const hats = batch(new T.ConeGeometry(.34, .16, 10), "#d9c394", 6, "fabric");
  const straps = batch(new T.BoxGeometry(.045, .44, .045), "#5f4d3e", 48);
  const hair = batch(new T.SphereGeometry(0.225, 8, 5, 0, Math.PI * 2, 0, Math.PI * 0.55), "#332e2a", 24);
  const shoes = batch(new T.BoxGeometry(0.16, 0.12, 0.29), "#eee1cb", 48);
  const bags = batch(new T.BoxGeometry(0.28, 0.36, 0.17), "#a58861", 24, "fabric");
  const bumpers = batch(new T.BoxGeometry(1.42,.15,.16), "#b4bbb7", 6);
  const lights = batch(new T.BoxGeometry(.28,.16,.05), "#f9ecc6", 6);
  const tailLights = batch(new T.BoxGeometry(.18,.25,.05), "#b75b46", 6);
  const panels = batch(new T.BoxGeometry(1.43,.65,.018), "#587d70", 3);
  const mirrors = batch(new T.BoxGeometry(.18,.12,.2), "#465c55", 6);
  const vanRoof = batch(new T.BoxGeometry(1.36,.12,2.4), "#ebdfc5", 3);
  const shirts = ["#548477", "#d6b76e", "#e4d9c2", "#b77760", "#68869a", "#8e8c71"];
  for (let i = 0; i < 24; i++) {
    pedestrianBodies.setColorAt(i, new T.Color(shirts[i % shirts.length]));
    const skin = new T.Color(["#c39c80", "#dfb597", "#eacaaa"][i % 3]);
    heads.setColorAt(i, skin); noses.setColorAt(i, skin);
    for (let side=0; side<2; side++) {
      forearms.setColorAt(i*2+side, skin);
      arms.setColorAt(i*2+side, new T.Color(shirts[i % shirts.length]));
      const trousers = new T.Color(["#405a56", "#6f716b", "#384e61", "#8b775e"][i%4]);
      legs.setColorAt(i*2+side, trousers); shins.setColorAt(i*2+side, trousers);
    }
  }
  for (let i=0; i<7; i++) { const color=new T.Color(shirts[i%shirts.length]); body.setColorAt(i,color); fronts.setColorAt(i,color); }
  for (let i=0; i<3; i++) vans.setColorAt(i,new T.Color(["#d3bd96", "#8ca99f", "#ccddd6"][i]));
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
        for (let side=0;side<2;side++) {
          place(riderArms,i*2+side,side ? -.22 : .22,1.35,.2,heading,turn,-.9);
          place(riderLegs,i*2+side,side ? -.21 : .21,.86,.14,heading,turn,.5);
        }
      } else {
        place(vans, i - 7, 0, 0.95, 0, heading);
        place(glass, i - 7, 0, 1.35, 0.77, heading);
        place(windshields,i-7,0,1.35,1.31,heading);
        place(graphics,i-7,0,1.0,-.45,heading);
        place(vanRoof, i-7, 0, 1.65, 0, heading);
        place(panels, i-7, 0, .95, -1.31, heading);
        for (let side=0;side<2;side++) {
          const n=(i-7)*2+side;
          place(bumpers,n,0,.48,side ? -1.32 : 1.32,heading);
          place(lights,n,side ? -.48 : .48,.91,1.32,heading);
          place(tailLights,n,side ? -.55 : .55,.95,-1.33,heading);
          place(mirrors,n,side ? -.82 : .82,1.35,.7,heading);
        }
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
      place(heads, i, 0, 1.55 + bounce, 0, heading);
      place(noses,i,0,1.54+bounce,.205,heading);
      place(collars,i,0,1.32+bounce,0,heading);
      if(i%4===0) place(hats,i/4,0,1.82+bounce,0,heading);
      place(hair, i, 0, 1.56 + bounce, 0, heading);
      place(bags, i, 0.16, 0.95 + bounce, -0.22, heading);
      for (let n = 0; n < 2; n++) {
        const swing = stride * (n ? 1 : -1);
        const side = n ? .12 : -.12;
        const kneeY = .73 - Math.cos(swing)*.3;
        const kneeZ = Math.sin(swing)*.3;
        const bend = motion ? swing - Math.max(0, -Math.sin(time*4+i+(n?0:Math.PI)))*.45 : 0;
        const ankleY = kneeY - Math.cos(bend)*.3;
        const ankleZ = kneeZ + Math.sin(bend)*.3;
        place(legs,i*2+n,side,.73-Math.cos(swing)*.15,Math.sin(swing)*.15,heading,0,-swing);
        place(shins,i*2+n,side,kneeY-Math.cos(bend)*.15,kneeZ+Math.sin(bend)*.15,heading,0,-bend);
        place(shoes,i*2+n,side,Math.max(.08,ankleY),ankleZ+.07,heading);
        place(arms,i*2+n,n ? .26 : -.26,1.15+bounce,-Math.sin(swing)*.11,heading,0,-swing);
        place(forearms,i*2+n,n ? .27 : -.27,.91+bounce,-Math.sin(swing)*.27+.035,heading,0,-swing-.12);
        place(eyes,i*2+n,n ? .075 : -.075,1.59+bounce,.193,heading);
        place(straps,i*2+n,n ? .13 : -.13,1.06+bounce,.18,heading);

      }
    }
    hubs.visible = near;
    eyes.visible = noses.visible = collars.visible = straps.visible = near;
    bags.visible = near;
    root.children.forEach((m) => {
      if (m instanceof T.InstancedMesh) m.instanceMatrix.needsUpdate = true;
    });
  }
  return { update };
}
