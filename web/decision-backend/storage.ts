import type { R2Bucket } from "@cloudflare/workers-types";

export const SNAPSHOT_KEY = "admin/state-v1.zip";
const MAX_SNAPSHOT = 96 * 1024 * 1024;

/** Called only by ContainerProxy, never exposed through the Worker's fetch handler. */
export async function snapshotStorage(request: Request, bucket: R2Bucket): Promise<Response> {
  if (new URL(request.url).pathname !== "/state") return new Response(null, { status: 404 });
  if (request.method === "GET") {
    const object = await bucket.get(SNAPSHOT_KEY);
    if (!object) return new Response(null, { status: 404 });
    return new Response(object.body as unknown as ReadableStream, {
      headers: { ETag: object.httpEtag, "Content-Type": "application/zip" },
    });
  }
  if (request.method !== "PUT") return new Response(null, { status: 405 });
  const length = Number(request.headers.get("content-length"));
  if (!Number.isSafeInteger(length) || length <= 0 || length > MAX_SNAPSHOT)
    return new Response("Invalid snapshot size", { status: 413 });
  const previous = request.headers.get("if-match");
  if (!previous && request.headers.get("if-none-match") !== "*")
    return new Response("Snapshot revision required", { status: 428 });
  // Conditional writes fence off an old container after a rollout/restart.
  const saved = await bucket.put(SNAPSHOT_KEY, request.body as never, {
    onlyIf: previous ? { etagMatches: previous.replace(/^"|"$/g, "") } : { etagDoesNotMatch: "*" },
    httpMetadata: { contentType: "application/zip" },
  });
  if (!saved) return new Response("Snapshot revision changed", { status: 412 });
  return new Response(null, { status: 204, headers: { ETag: saved.httpEtag } });
}
