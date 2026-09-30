import { agentFetch } from "@/lib/agent";
import { isSameOrigin } from "@/lib/auth/origin";
import { getSession } from "@/lib/auth/session";
import { isId, sseResponse } from "@/lib/runs";

export const dynamic = "force-dynamic";

/**
 * BFF proxy for sending a message: the browser posts here (cookie session); this server adds the
 * user's access token, and gen9-agent queues a run and streams its events back. The run is
 * executed by a worker, so it keeps going if the browser leaves (the stream closes, the run doesn't).
 */
export async function POST(request: Request, { params }: RouteContext<"/api/threads/[id]/runs">) {
  if (!isSameOrigin(request)) return Response.json({ detail: "Forbidden" }, { status: 403 });
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });

  const { id } = await params;
  if (!isId(id)) return Response.json({ detail: "Not found" }, { status: 404 });
  const upstream = await agentFetch(session, `/v1/threads/${id}/runs/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: await request.text(),
    signal: request.signal, // the browser stopped listening: close the upstream stream too
  });
  return sseResponse(upstream);
}
