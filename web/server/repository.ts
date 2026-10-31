import {
  makeSource,
  SOURCE_DEFS,
  type SourcePack,
} from "../src/studio/sources";
import bundled from "./repository-sources.json";
const REPO = "AIVIETNAM-AIO-QP09/sea-x-openai-hackathon";
export async function repositorySources(): Promise<{
  pack: SourcePack;
  notice: string;
}> {
  try {
    const commitResponse = await fetch(
      `https://api.github.com/repos/${REPO}/commits/main`,
      {
        headers: {
          "User-Agent": "MegaDay-Hackathon",
          Accept: "application/vnd.github+json",
        },
        signal: AbortSignal.timeout(7000),
      },
    );
    if (!commitResponse.ok) throw Error("GitHub unavailable");
    const commit = (await commitResponse.json()) as { sha?: unknown };
    if (typeof commit.sha !== "string" || !/^[a-f0-9]{40}$/.test(commit.sha))
      throw Error("Invalid GitHub revision");
    const sha = commit.sha;
    const files = await Promise.all(
      SOURCE_DEFS.map(async (d) => {
        const path = `mock-data/out/demo/${d.path}`;
        const response = await fetch(
          `https://raw.githubusercontent.com/${REPO}/${sha}/${path}`,
          { signal: AbortSignal.timeout(10000) },
        );
        if (!response.ok) throw Error("Missing source");
        const text = await response.text();
        if (text.length > 20 * 1024 * 1024) throw Error("Source too large");
        return makeSource(d.file, text, path, sha);
      }),
    );
    return {
      pack: { schema: "hackathon.sources.v1", files },
      notice: `GitHub · ${sha.slice(0, 7)}`,
    };
  } catch {
    return {
      pack: bundled as SourcePack,
      notice: `Repository build copy · ${bundled.files[0]?.commit?.slice(0, 7) ?? "local"}. GitHub could not be reached.`,
    };
  }
}
