export type StudioModelEnv = {
  OPENAI_API_KEY?: string;
  OPENAI_MODEL?: string;
  OPENAI_MODEL_IMPORT?: string;
  OPENAI_MODEL_DECISION?: string;
};

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
  if (!response.ok) throw Error("The model could not complete the analysis. Retry shortly.");
  const body = await response.json() as {
    status?: string;
    output?: { type: string; content?: { type: string; text?: string }[] }[];
  };
  if (body.status && body.status !== "completed") throw Error("Model analysis was incomplete.");
  const text = (body.output ?? []).filter(o => o.type === "message").flatMap(o => o.content ?? [])
    .filter(c => c.type === "output_text").map(c => c.text ?? "").join("");
  return JSON.parse(text);
}
