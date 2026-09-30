import { agentFetch } from "@/lib/agent";
import { getSession } from "@/lib/auth/session";
import { isId } from "@/lib/runs";

export const dynamic = "force-dynamic";

/** The chat's background tasks and their status (gen9-agent's background.py), for its list to refresh itself while one works. */
export async function GET(_request: Request, { params }: RouteContext<"/api/threads/[id]/tasks">) {
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });

  const { id } = await params;
  if (!isId(id)) return Response.json({ detail: "Not found" }, { status: 404 });
  const upstream = await agentFetch(session, `/v1/threads/${id}/tasks`);
  return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": "application/json" } });
}
