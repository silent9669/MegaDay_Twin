import * as T from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
export function landscape(scene: T.Scene) {
  const trunks = new T.InstancedMesh(
    new T.CylinderGeometry(0.12, 0.24, 4.5, 7),
    new T.MeshStandardMaterial({ color: "#9d8e6e", roughness: 1 }),
    38,
  );
  const leaves: T.BufferGeometry[] = [];
  for (let n = 0; n < 8; n++) {
    const vertices: number[] = [],
      indices: number[] = [];
    for (let i = 0; i <= 12; i++) {
      const t = i / 12,
        angle = (n * Math.PI) / 4,
        reach = t * 2.8,
        width = Math.sin(t * Math.PI) * 0.43,
        y = Math.sin(t * Math.PI) * 0.85 - t * 0.5;
      for (const side of [-1, 1])
        vertices.push(
          Math.sin(angle) * reach + Math.cos(angle) * width * side,
          y,
          Math.cos(angle) * reach - Math.sin(angle) * width * side,
        );
      if (i < 12) {
        const k = i * 2;
        indices.push(k, k + 1, k + 2, k + 1, k + 3, k + 2);
      }
    }
    const g = new T.BufferGeometry();
    g.setAttribute("position", new T.Float32BufferAttribute(vertices, 3));
    g.setIndex(indices);
    g.computeVertexNormals();
    leaves.push(g);
  }
  const palmMaterial = new T.MeshStandardMaterial({
    color: "#64845d",
    roughness: 0.85,
    side: T.DoubleSide,
  });
  const fronds = new T.InstancedMesh(
    mergeGeometries(leaves)!,
    palmMaterial,
    38,
  );
  leaves.forEach((g) => g.dispose());
  const o = new T.Object3D();
  for (let i = 0; i < 38; i++) {
    const x = i < 24 ? (i % 2 ? 45.7 : 67.2) : i % 2 ? -48 : -40;
    const z =
      i < 24
        ? -43 + Math.floor(i / 2) * 7.4
        : -30 + Math.floor((i - 24) / 2) * 10;
    const scale = 0.8 + (i % 4) * 0.09;
    o.position.set(x, 2.35 * scale, z);
    o.scale.set(scale, scale, scale);
    o.rotation.set(0, i * 2.4, 0);
    o.updateMatrix();
    trunks.setMatrixAt(i, o.matrix);
    o.position.y = 4.6 * scale;
    o.updateMatrix();
    fronds.setMatrixAt(i, o.matrix);
  }
  trunks.castShadow = true;
  fronds.castShadow = true;
  scene.add(trunks, fronds);
  const wind = { value: 0 };
  palmMaterial.onBeforeCompile = (shader) => {
    shader.uniforms.cityWind = wind;
    shader.vertexShader =
      "uniform float cityWind;\n" +
      shader.vertexShader.replace(
        "#include <begin_vertex>",
        "#include <begin_vertex>\ntransformed.y += sin(cityWind + position.x * 1.7 + position.z) * length(position.xz) * 0.022;",
      );
  };
  return {
    update: (time: number) => {
      wind.value = time * 0.7;
    },
  };
}
