export type StudioModelEnv = {
  OPENAI_API_KEY?: string;
  OPENAI_MODEL?: string;
  OPENAI_MODEL_IMPORT?: string;
  OPENAI_MODEL_DECISION?: string;
};

export class ModelRequestError extends Error {
  constructor(status: number) {
    super(status === 401 || status === 403 ? "The configured AI connection was rejected. Check the Worker's OpenAI secret and model access."
      : status === 404 ? "The configured AI model is unavailable for this account. Check the import/decision model setting."
      : status === 429 ? "The AI service is rate-limited or out of quota. Retry shortly or check the account quota."
      : status === 400 ? "The AI service rejected the structured-output request. Check that the configured model supports this format."
      : "The AI service is temporarily unavailable. Retry shortly.");
  }
}

/** Server-only structured output. Uploaded content stays in the untrusted user input. */
export async function modelJSON(env: StudioModelEnv, model: string, instructions: string,
  input: unknown, schema: Record<string, unknown>, signal: AbortSignal): Promise<unknown> {
  const response = await fetch("https://api.openai.com/v1/responses", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.OPENAI_API_KEY}`, "Content-Type": "application/json" },
    signal,
    body: JSON.stringify({ model, store: false, max_output_tokens: 10000, instructions,
      input: JSON.stringify({ untrusted_evidence: input }),
      text: { format: { type: "json_schema", name: "studio_result", strict: true, schema } },
    }),
  });
  if (!response.ok) throw new ModelRequestError(response.status);
  const body = await response.json() as {
    status?: string;
    output?: { type: string; content?: { type: string; text?: string }[] }[];
  };
  if (body.status && body.status !== "completed") throw Error("Model analysis was incomplete.");
  const text = (body.output ?? []).filter(o => o.type === "message").flatMap(o => o.content ?? [])
    .filter(c => c.type === "output_text").map(c => c.text ?? "").join("");
  return JSON.parse(text);
}
