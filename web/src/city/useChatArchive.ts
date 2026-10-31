import { useCallback, useRef } from "react";
import { requestJSON, useAccountDocument } from "../studio/account";
import type {
  ChatArchive,
  ChatSession,
  ChatMessage,
  Doc,
} from "../studio/workspace-types";

export function useChatArchive() {
  const account = useAccountDocument<ChatArchive>("chats");
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const save = account.save;
  const mutate = useCallback(
    (change: (data: ChatArchive) => ChatArchive) => {
      const run = async () => {
        for (let attempt = 0; attempt < 3; attempt++) {
          const current = await requestJSON<Doc<ChatArchive>>(
            "/api/workspace/chats",
          );
          const next = change(current.data);
          try {
            return await save(next, current.revision);
          } catch (error) {
            if (attempt === 2 || (error as { status?: number }).status !== 409)
              throw error;
          }
        }
        throw Error("Another device is saving. Try again.");
      };
      const result = queue.current.then(run, run);
      queue.current = result.catch(() => {});
      return result;
    },
    [save],
  );
  const append = useCallback(
    (session: ChatSession, message: ChatMessage) =>
      mutate((archive) => {
        const stored = archive.sessions.find((s) => s.id === session.id);
        if (
          stored &&
          (stored.messages.length !== session.messages.length ||
            stored.messages.at(-1)?.id !== session.messages.at(-1)?.id)
        )
          throw Error(
            "This chat changed on another device. Reopen it from History. Your draft is kept.",
          );
        if (!stored && archive.sessions.length >= 100)
          throw Error("Your account has reached the 100-chat limit.");
        if (session.messages.length >= 198)
          throw Error("This conversation is full. Start a new chat.");
        const updated = {
          ...session,
          updatedAt: new Date().toISOString(),
          messages: [...session.messages, message],
        };
        return {
          preferredModel: session.model,
          sessions: [
            updated,
            ...archive.sessions.filter((s) => s.id !== updated.id),
          ],
        };
      }),
    [mutate],
  );
  return { ...account, append, mutate };
}
