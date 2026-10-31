# Saigon asset sources

Original architectural study for MegaDay Twin, authored in Blender 5.2.1 with the MCP for Blender plugin. `saigon.py` builds a dedicated scene and leaves the existing Blender scene intact. The checked-in `district1.blend` contains that scene and its packed 2048px baked ambient-occlusion atlas.

The city is inspired by District 1; buildings and the river layout are illustrative, not survey-accurate. Bitexco is a separate landmark. The small signed office is MegaDay Twin. Ben Thanh Market, Central Post Office, café shophouses, river quays, benches, and streetlights are original mesh constructions. No downloaded third-party models or texture licenses are involved. Existing ad platform logos retain the repository's original attribution.

To regenerate, set `MEGADAY_ROOT` to the repository path and execute `saigon.py` in Blender. Optimize the intermediate GLB with:

```sh
npx @gltf-transform/cli optimize tools/assets/district1.raw.glb web/public/assets/saigon/district1.glb --compress meshopt --texture-compress webp --texture-size 2048 --simplify false
npx @gltf-transform/cli validate web/public/assets/saigon/district1.glb
```

The runtime uses GLTFLoader + MeshoptDecoder, an immediate low-detail fallback, and explicit GPU disposal. Roads and paving use original deterministic canvas textures. Palms, vehicles, pedestrians, stock cartons and wakes use instancing. Vehicle wheels and lean, pedestrian gait, river ripples, ship movement and foliage sway follow delta time. Small vehicle hubs/arms are omitted at a distant camera position. Motion is illustrative and respects reduced motion and the app's motion setting.

## Milo

PixelLab generated these assets using `web/public/pm.svg` as the supplied identity reference. Generation date: 2026-10-31. Original palette and clothing were retained. Idle is a static 64×96 PNG; the four action sheets each contain five 64×96 frames (input plus four generated frames). Reduced motion uses a single static frame. Credentials and signed download URLs are not included.

- Idle edit: `4c8c9d68-3ddb-4da0-abc5-5eabce370341`
- Greeting: `f7408fd4-031a-4aac-ba73-a2a68d6aa294`
- Thinking: `b7ac1ff6-a011-40a1-be40-f1e2491ee79c`
- Notification: `c87001f1-8d8a-4eef-8d93-65b8a9cadb13`
- Error: `01ade669-4985-46c5-952f-f590c8b30be6`

The original SVG remains in the repository as the authoring reference. Assets are bundled locally; the running app never calls PixelLab.
