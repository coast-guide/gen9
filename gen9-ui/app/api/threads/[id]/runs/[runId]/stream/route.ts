import { agentFetch } from "@/lib/agent";
import { getSession } from "@/lib/auth/session";
import { isId, sseResponse } from "@/lib/runs";

export const dynamic = "force-dynamic";

/**
 * Follow a run again (after a reload, or when a connection dropped): gen9-agent replays its events
 * after `Last-Event-ID` (or `?after=`) and then streams it live until it completes.
 */
export async function GET(request: Request, { params }: RouteContext<"/api/threads/[id]/runs/[runId]/stream">) {
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });

  const { id, runId } = await params;
  if (!isId(id) || !isId(runId)) return Response.json({ detail: "Not found" }, { status: 404 });
  const after = new URL(request.url).searchParams.get("after");
  const lastEventId = request.headers.get("Last-Event-ID");
  const upstream = await agentFetch(
    session,
    `/v1/threads/${id}/runs/${runId}/stream${after && /^\d+$/.test(after) ? `?after=${after}` : ""}`,
    {
      headers: { Accept: "text/event-stream", ...(lastEventId && /^\d+$/.test(lastEventId) ? { "Last-Event-ID": lastEventId } : {}) },
      signal: request.signal,
    },
  );
  return sseResponse(upstream);
}
