import * as T from "three";
/** Saigon River study: illustrated vessel traffic, independent of commerce data. */
export function createRiver(scene: T.Scene) {
  const river = new T.Group();
  scene.add(river);
  const water = new T.Mesh(
    new T.PlaneGeometry(18, 100, 16, 64),
    new T.MeshStandardMaterial({
      color: "#799e8d",
      roughness: 0.35,
      metalness: 0.25,
      transparent: true,
      opacity: 0.94,
    }),
  );
  water.rotation.x = -Math.PI / 2;
  water.position.set(56.5, -0.18, 0);
  river.add(water);
  const positions = water.geometry.attributes.position!;
  const base = Float32Array.from(positions.array);
  const boatMaterial = new T.MeshStandardMaterial({
    color: "#405f58",
    roughness: 0.65,
  });
  const ivory = new T.MeshStandardMaterial({
    color: "#e9dfbd",
    roughness: 0.7,
  });
  const cargo = new T.MeshStandardMaterial({
    color: "#ad7e5b",
    roughness: 0.8,
  });
  const boats: T.Group[] = [];
  for (let i = 0; i < 3; i++) {
    const boat = new T.Group();
    const shape = new T.Shape();
    shape.moveTo(-1.3, -4);
    shape.lineTo(1.3, -4);
    shape.lineTo(1.3, 2.6);
    shape.quadraticCurveTo(1.1, 4, 0, 5);
    shape.quadraticCurveTo(-1.1, 4, -1.3, 2.6);
    shape.closePath();
    const hull = new T.Mesh(
      new T.ExtrudeGeometry(shape, {
        depth: 0.85,
        bevelEnabled: true,
        bevelSegments: 1,
        steps: 1,
        bevelSize: 0.12,
        bevelThickness: 0.1,
      }),
      boatMaterial,
    );
    hull.rotation.x = -Math.PI / 2;
    boat.add(hull);
    const deck = new T.Mesh(new T.BoxGeometry(2.2, 0.15, 6.4), ivory);
    deck.position.y = 0.95;
    boat.add(deck);
    const cabin = new T.Mesh(new T.BoxGeometry(1.8, 1.2, 1.5), ivory);
    cabin.position.set(0, 1.6, -2);
    boat.add(cabin);
    const windows = new T.Mesh(
      new T.BoxGeometry(1.85, 0.42, 1.55),
      boatMaterial,
    );
    windows.position.copy(cabin.position).y += 0.1;
    boat.add(windows);
    for (let n = 0; n < (i === 1 ? 1 : 3); n++) {
      const box = new T.Mesh(new T.BoxGeometry(1.85, 0.9, 1.2), cargo);
      box.position.set(0, 1.5, n * 1.4 - 0.5);
      boat.add(box);
    }
    if (i === 1) boat.scale.setScalar(0.7);
    river.add(boat);
    boats.push(boat);
  }
  const wakes = new T.InstancedMesh(
    new T.PlaneGeometry(0.1, 3),
    new T.MeshBasicMaterial({
      color: "#d4e0c7",
      transparent: true,
      opacity: 0.35,
      depthWrite: false,
    }),
    12,
  );
  river.add(wakes);
  wakes.frustumCulled = false;
  const o = new T.Object3D();
  function update(time: number, motion: boolean) {
    for (let i = 0; i < positions.count; i++) {
      const x = base[i * 3]!,
        y = base[i * 3 + 1]!;
      positions.setZ(
        i,
        Math.sin(y * 0.8 + time * 0.7) * Math.cos(x * 0.7 + time * 0.3) * 0.035,
      );
    }
    positions.needsUpdate = true;
    boats.forEach((boat, i) => {
      const direction = i === 1 ? -1 : 1;
      boat.position.set(
        i === 1 ? 60 : 52.5,
        Math.sin(time * 0.8 + i) * 0.025,
        (((time * (0.8 + i * 0.16) + i * 35) % 112) - 56) * direction,
      );
      boat.rotation.set(
        0,
        i === 1 ? Math.PI : 0,
        Math.sin(time * 0.6 + i) * 0.012,
      );
      for (let n = 0; n < 4; n++) {
        o.position.copy(boat.position);
        o.position.x += (n % 2 ? 1 : -1) * (1.5 + Math.floor(n / 2) * 0.35);
        o.position.z -= (5 + Math.floor(n / 2) * 2) * direction;
        o.position.y = 0.02;
        o.rotation.set(-Math.PI / 2, 0, (n % 2 ? -0.15 : 0.15) * direction);
        o.updateMatrix();
        wakes.setMatrixAt(i * 4 + n, o.matrix);
      }
    });
    wakes.visible = motion;
    wakes.instanceMatrix.needsUpdate = true;
  }
  return { update };
}
