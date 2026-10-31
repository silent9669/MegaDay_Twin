# Saigon asset sources

Original architectural study for MegaDay Twin, authored in Blender 5.2.1 with the MCP for Blender plugin. `saigon.py` builds a dedicated scene and leaves the existing Blender scene intact. The checked-in `district1.blend` contains that scene and its packed 2048px baked ambient-occlusion atlas.

The city is inspired by District 1; buildings and the river layout are illustrative, not survey-accurate. Bitexco is a separate landmark. The small signed office is MegaDay Twin. Landmark 81, Ben Thanh Market, Central Post Office, modern infill apartments, café shophouses, river quays, benches, and streetlights are original mesh constructions. No downloaded third-party models or texture licenses are involved. Existing ad platform logos retain the repository's original attribution.

To regenerate, set `MEGADAY_ROOT` to the repository path and execute `saigon.py` in Blender. Optimize the intermediate GLB with:

```sh
npx @gltf-transform/cli optimize tools/assets/district1.raw.glb web/public/assets/saigon/district1.glb --compress meshopt --texture-compress webp --texture-size 2048 --simplify false --palette false
npx @gltf-transform/cli validate web/public/assets/saigon/district1.glb
```

The runtime uses GLTFLoader + MeshoptDecoder, an immediate low-detail fallback, and explicit GPU disposal. Roads and paving use original deterministic canvas textures. Palms, vehicles, pedestrians, stock cartons and wakes use instancing. Vehicle wheels and lean, pedestrian gait, river ripples, ship movement and foliage sway follow delta time. Small vehicle hubs and citizen face, collar, strap and bag details are omitted at a distant camera position. Motion is illustrative and respects reduced motion and the app's motion setting.

## Milo

PixelLab generated these assets using `web/public/pm.svg` as the supplied identity reference. Generation date: 2026-10-31. Original palette and clothing were retained. Idle is a static 64×96 PNG; the four action sheets each contain five 64×96 frames (input plus four generated frames). Reduced motion uses a single static frame. Credentials and signed download URLs are not included.

- Idle edit: `4c8c9d68-3ddb-4da0-abc5-5eabce370341`
- Greeting: `f7408fd4-031a-4aac-ba73-a2a68d6aa294`
- Thinking: `b7ac1ff6-a011-40a1-be40-f1e2491ee79c`
- Notification: `c87001f1-8d8a-4eef-8d93-65b8a9cadb13`
- Error: `01ade669-4985-46c5-952f-f590c8b30be6`

The original SVG remains in the repository as the authoring reference. Assets are bundled locally; the running app never calls PixelLab.

Keep material palettes disabled: named glass and lamp materials receive runtime night emission. The original arch bridge, two-storey office terrace, and shared AO atlas are included in the same kit.

### Refined Milo sprites

The refined 64×96 character uses PixelLab Pixflux with the previous Milo sprite as an identity reference (2026-10-31, seed 1735). Four matching gesture sheets use `animate_image`, four generated frames plus the first frame. `avatar.png` is a local nearest-neighbor portrait crop. All five generation jobs completed; total cost: five generations.

- idle: `66c16dec-a2a1-4f4b-b66c-ef2d9b82c0b8`
- greeting: `fabfb56b-8556-415f-862a-57350317d324`
- thinking: `dd31921e-f61c-4c42-b29e-63c31f92047e`
- notification: `46722f9a-1b55-425b-b3f0-dce31d7e93f0`
- error: `8092c471-2cc8-491f-8960-a67361cf549f`

## Surface and street detail

`web/src/city/detail-textures.ts` creates original 128px tiling plaster, terracotta, timber, cloth, tire and vehicle-paint textures locally. Architectural detail uses a separate metre-scaled UV channel; the Blender AO atlas remains on its original coordinates. Color maps use sRGB and bump maps remain linear. Shared textures are disposed with the scene. No external asset services or licensed textures are required.

Citizens retain instancing and now have separate sleeve/forearm and thigh/shin motion, collars, faces, bags and a mix of conical hats. Scooters have seated limbs; delivery vehicles have glazing, mirrors, lights, bumpers and original MegaDay livery. Fine details are hidden at distant camera positions; the existing reduced-motion setting freezes the scene.
