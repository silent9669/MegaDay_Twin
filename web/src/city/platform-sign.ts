import * as T from "three";
import { PLATFORMS, type PlatformId } from "./platforms";

/** One shared texture per brand, kept across building add/remove operations. */
export function createPlatformSign(id: PlatformId, anisotropy: number) {
  const brand = PLATFORMS[id];
  const canvas = document.createElement("canvas");
  canvas.width = 1024;
  canvas.height = 256;
  const ctx = canvas.getContext("2d")!;
  const texture = new T.CanvasTexture(canvas);
  texture.colorSpace = T.SRGBColorSpace;
  texture.anisotropy = anisotropy;
  const image = new Image();
  let disposed = false;
  const draw = (logo: boolean) => {
    if (disposed) return;
    ctx.fillStyle = brand.sign;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (logo) {
      const scale = Math.min(152 / image.naturalWidth, 152 / image.naturalHeight);
      const w = image.naturalWidth * scale, h = image.naturalHeight * scale;
      ctx.drawImage(image, 120 - w / 2, 120 - h / 2, w, h);
    }
    ctx.fillStyle = brand.ink;
    ctx.font = "600 85px Arial, sans-serif";
    ctx.textBaseline = "middle";
    ctx.fillText(brand.name, 238, 125, 740);
    brand.accents.forEach((color, index) => {
      ctx.fillStyle = color;
      ctx.fillRect(index * 1024 / brand.accents.length, 238, 1024 / brand.accents.length, 18);
    });
    texture.needsUpdate = true;
  };
  draw(false); // A readable name remains even if an image cannot load.
  image.onload = () => draw(true);
  image.src = brand.logo;
  return {
    texture,
    dispose() {
      disposed = true;
      image.onload = null;
      texture.dispose();
    },
  };
}
