import { Gunzip, gzipSync, strToU8, strFromU8 } from "fflate";

export const MAX_TABLE_BYTES = 32 * 1024 * 1024;
export const MAX_SOURCE_BYTES = 48 * 1024 * 1024;
export function encodeSource(text: string): { text: string; encoding?: "gzip-base64" } {
  if (strToU8(text).length < 512 * 1024) return { text };
  const bytes = gzipSync(strToU8(text), { level: 1 });
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return { text: btoa(binary), encoding: "gzip-base64" };
}
export function sourceText(source: { text: string; encoding?: "gzip-base64" }): string {
  if (!source.encoding) return source.text;
  const bytes = Uint8Array.from(atob(source.text), character => character.charCodeAt(0));
  const chunks: Uint8Array[] = [];
  let size = 0;
  const inflater = new Gunzip(chunk => {
    size += chunk.length;
    if (size > MAX_TABLE_BYTES) throw Error("Table exceeds 32 MiB after decompression.");
    chunks.push(chunk);
  });
  for (let i = 0; i < bytes.length; i += 1024) inflater.push(bytes.subarray(i, i + 1024), i + 1024 >= bytes.length);
  const output = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.length; }
  return strFromU8(output);
}
