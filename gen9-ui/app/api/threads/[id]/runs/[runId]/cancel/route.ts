import { agentFetch } from "@/lib/agent";
import { isSameOrigin } from "@/lib/auth/origin";
import { getSession } from "@/lib/auth/session";
import { isId } from "@/lib/runs";

export const dynamic = "force-dynamic";

/** Stop a run (the Stop button): gen9-agent stops a running one within about two seconds. */
export async function POST(request: Request, { params }: RouteContext<"/api/threads/[id]/runs/[runId]/cancel">) {
  if (!isSameOrigin(request)) return Response.json({ detail: "Forbidden" }, { status: 403 });
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });

  const { id, runId } = await params;
  if (!isId(id) || !isId(runId)) return Response.json({ detail: "Not found" }, { status: 404 });
  const upstream = await agentFetch(session, `/v1/threads/${id}/runs/${runId}/cancel`, { method: "POST" });
  return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": "application/json" } });
}
