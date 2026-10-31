import { z } from "zod";
// Raster data URLs only: no remote requests or executable SVG attachments.
export const imageAttachmentSchema = z.object({
  name: z.string().min(1).max(150),
  dataUrl: z
    .string()
    .max(700000)
    .refine((value) => {
      const match =
        /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$/.exec(
          value,
        );
      if (!match || match[2]!.length % 4) return false;
      try {
        const bytes = atob(match[2]!);
        if (bytes.length > 512 * 1024) return false;
        if (match[1] === "png") return bytes.startsWith("\x89PNG\r\n\x1a\n");
        if (match[1] === "jpeg") return bytes.startsWith("\xff\xd8\xff");
        return bytes.startsWith("RIFF") && bytes.slice(8, 12) === "WEBP";
      } catch {
        return false;
      }
    }, "Use a PNG, JPEG or WebP image under 512 KB."),
});
export type ImageAttachment = z.infer<typeof imageAttachmentSchema>;
export async function prepareChatImage(file: File): Promise<ImageAttachment> {
  if (
    !["image/png", "image/jpeg", "image/webp"].includes(file.type) ||
    file.size > 10 * 1024 * 1024
  )
    throw Error("Choose a PNG, JPEG or WebP image under 10 MB.");
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, 1280 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const ctx = canvas.getContext("2d");
    if (!ctx) throw Error("Image preview unavailable.");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return imageAttachmentSchema.parse({
      name: file.name.slice(0, 150),
      dataUrl: canvas.toDataURL("image/jpeg", 0.8),
    });
  } finally {
    bitmap.close();
  }
}
