import "server-only";

// Helpers for the BFF routes that proxy gen9-agent's runs (app/api/threads/[id]/runs/...).

const UUID = /^[0-9a-f-]{36}$/i;

export const isId = (value: string) => UUID.test(value);

/** Stream gen9-agent's Server-Sent Events back to the browser as they arrive. */
export function sseResponse(upstream: Response): Response {
  if (!upstream.ok || !upstream.body) {
    return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": "application/json" } });
  }
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
