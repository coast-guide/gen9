// A run's events as the web app reads them: gen9-agent's server-sent events, through the BFF

/** Parse a text/event-stream body into (event, data, id) triples. */
export async function* readEvents(body: ReadableStream<Uint8Array>) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = "message";
      let id: string | null = null;
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
        else if (line.startsWith("id:")) id = line.slice(3).trim();
      }
      if (data.length) yield { event, id, data: JSON.parse(data.join("\n")) as Record<string, unknown> };
    }
  }
}

/** A body's events until it ends or its connection breaks (the API restarted, the network went): both mean
 * reconnecting. Leaving the page (an abort) still throws. */
export async function* untilBroken(body: ReadableStream<Uint8Array>) {
  try {
    yield* readEvents(body);
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
  }
}

/** What a message's `message.completed` adds to the text its deltas showed: all of it for a message the model didn't
 * stream (a step budget's stop, an answer cut off at its length limit), the rest when it ends with more, else nothing. */
export function completedRest(shown: string, text: string): string {
  return text.startsWith(shown) ? text.slice(shown.length) : "";
}
