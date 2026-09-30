import { agentFetch } from "@/lib/agent";
import { isSameOrigin } from "@/lib/auth/origin";
import { getSession } from "@/lib/auth/session";
import { isId } from "@/lib/runs";

export const dynamic = "force-dynamic";

/** Answer what a waiting run asks (the question card): gen9-agent stores the answers, first one
 * wins, and the run goes on. 409 when already answered or no longer waiting. */
export async function POST(request: Request, { params }: RouteContext<"/api/threads/[id]/runs/[runId]/inputs/[inputId]">) {
  if (!isSameOrigin(request)) return Response.json({ detail: "Forbidden" }, { status: 403 });
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });

  const { id, runId, inputId } = await params;
  // A request's id: an interrupt's (32 hex) or a Retry's ("retry-2")
  if (!isId(id) || !isId(runId) || !/^[0-9a-z-]{1,64}$/.test(inputId)) return Response.json({ detail: "Not found" }, { status: 404 });
  const upstream = await agentFetch(session, `/v1/threads/${id}/runs/${runId}/inputs/${inputId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await request.text(),
  });
  return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": "application/json" } });
}
