import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { makeSource, SOURCE_DEFS, type SourcePack } from "../../src/studio/sources";
// Local preparation keeps raw customer exports out of the repository and the browser upload.
const folder = process.env.SHOP_DATA_DIR;
const output = process.env.SHOP_PACK_PATH;
if (!folder || !output) throw Error("Set SHOP_DATA_DIR and SHOP_PACK_PATH.");
const pack: SourcePack = { schema: "hackathon.sources.v1", files: [] };
for (const definition of SOURCE_DEFS) {
  pack.files.push(await makeSource(definition.file, await readFile(join(folder, definition.file), "utf8"), definition.file));
}
await writeFile(output, JSON.stringify(pack));
console.log(JSON.stringify({ tables: pack.files.length, uploadMiB: Buffer.byteLength(JSON.stringify(pack)) / 1048576 }));
