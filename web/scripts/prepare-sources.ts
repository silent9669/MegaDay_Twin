import { readFile, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { makeSource, SOURCE_DEFS } from "../src/studio/sources.ts";
const commit = execFileSync("git", ["rev-parse", "HEAD"], {
  encoding: "utf8",
}).trim();
const files = await Promise.all(
  SOURCE_DEFS.map(async (d) => {
    const path = `mock-data/out/demo/${d.path}`;
    return makeSource(
      d.file,
      await readFile(new URL(`../../${path}`, import.meta.url), "utf8"),
      path,
      commit,
    );
  }),
);
await writeFile(
  new URL("../server/repository-sources.json", import.meta.url),
  JSON.stringify({ schema: "hackathon.sources.v1", files }),
);
console.log(
  "Prepared six sanitized repository sources. Original files unchanged.",
);
