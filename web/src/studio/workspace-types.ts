import { z } from "zod";
export const settingsSchema = z.object({
  appearance: z.enum(["morning", "night"]).default("morning"),
  signals: z.boolean(),
  motion: z.boolean(),
  quality: z.enum(["balanced", "high"]),
  platforms: z
    .array(z.enum(["shopee", "tiktok", "meta", "google"]))
    .min(1)
    .max(4),
});
export const sandboxSchema = z.object({
  hour: z.number().int().min(0).max(24),
  event: z.enum(["observe", "stock", "ads", "orders"]),
});
export type ViewSettings = z.infer<typeof settingsSchema>;
export type Sandbox = z.infer<typeof sandboxSchema>;
export const defaultSettings: ViewSettings = {
  appearance: "morning",
  signals: true,
  motion: true,
  quality: "balanced",
  platforms: ["shopee"],
};
export const defaultSandbox: Sandbox = { hour: 1, event: "observe" };
export type Doc<T> = { revision: number; updatedAt: string; data: T };
export const chatMessageSchema = z.object({
  id: z.string().uuid(),
  role: z.enum(["user", "assistant"]),
  content: z.string().trim().min(1).max(16000),
});
export const chatSessionSchema = z.object({
  id: z.string().uuid(),
  title: z.string().min(1).max(100),
  model: z.string().max(150),
  updatedAt: z.string().datetime(),
  messages: z.array(chatMessageSchema).max(200),
});
export const chatArchiveSchema = z
  .object({
    preferredModel: z.string().max(150),
    sessions: z.array(chatSessionSchema).max(100),
  })
  .superRefine((archive, ctx) => {
    if (
      new Set(archive.sessions.map((s) => s.id)).size !==
      archive.sessions.length
    )
      ctx.addIssue({ code: "custom", message: "Duplicate chat sessions." });
    for (const session of archive.sessions)
      if (
        new Set(session.messages.map((m) => m.id)).size !==
        session.messages.length
      )
        ctx.addIssue({ code: "custom", message: "Duplicate chat messages." });
  });
export type ChatMessage = z.infer<typeof chatMessageSchema>;
export type ChatSession = z.infer<typeof chatSessionSchema>;
export type ChatArchive = z.infer<typeof chatArchiveSchema>;
export const defaultChatArchive: ChatArchive = {
  preferredModel: "",
  sessions: [],
};
export type WorkspaceKey = "sources" | "settings" | "sandbox" | "chats";
