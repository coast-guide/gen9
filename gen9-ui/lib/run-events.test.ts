import { describe, expect, it } from "vitest";

import { completedRest, readEvents, untilBroken } from "@/lib/run-events";

const encoder = new TextEncoder();

/** A body that sends these chunks as they're read, then ends, or breaks with `error` (erroring a stream drops the
 * chunks still queued, so only once they're read). */
function body(chunks: string[], error?: Error) {
  const left = [...chunks];
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      const chunk = left.shift();
      if (chunk !== undefined) controller.enqueue(encoder.encode(chunk));
      else if (error) controller.error(error);
      else controller.close();
    },
  });
}

async function all(events: AsyncIterable<{ event: string; id: string | null }>) {
  const out: string[] = [];
  for await (const { event, id } of events) out.push(`${id}:${event}`);
  return out;
}

const DELTA = 'event: message.delta\ndata: {"text": "Hel"}\nid: 2\n\n';

describe("untilBroken", () => {
  it("reads events with their ids, across chunks", async () => {
    expect(await all(readEvents(body(['event: run.queued\ndata: {"run_id": "r1"}\nid: 1\n', "\n", DELTA])))).toEqual([
      "1:run.queued",
      "2:message.delta",
    ]);
  });

  it("ends at a broken connection, as at a closed one, so the page reconnects (found by hand: P2-I4)", async () => {
    const broken = body([DELTA], new TypeError("network error"));
    expect(await all(untilBroken(broken))).toEqual(["2:message.delta"]);
    await expect(all(readEvents(body([DELTA], new TypeError("network error"))))).rejects.toThrow("network error");
  });

  it("still throws when the page is left (an abort)", async () => {
    const left = body([DELTA], new DOMException("The user aborted a request.", "AbortError"));
    await expect(all(untilBroken(left))).rejects.toThrow("aborted");
  });
});

describe("completedRest", () => {
  // P6-Z1: a step budget's stop and an answer cut off at its length limit come only as message.completed
  it("is the whole text of a message the model didn't stream", () => {
    expect(completedRest("", "I stopped here.")).toBe("I stopped here.");
  });
  it("is what a streamed message ends with beyond its deltas", () => {
    expect(completedRest("Here: 1, 2", "Here: 1, 2\n\nI stopped here.")).toBe("\n\nI stopped here.");
  });
  it("is nothing when the deltas showed it all, or showed something else", () => {
    expect(completedRest("Hello", "Hello")).toBe("");
    expect(completedRest("Hello", "Goodbye")).toBe("");
  });
});
