import { agentFetch } from "@/lib/agent";
import { isSameOrigin } from "@/lib/auth/origin";
import { getSession } from "@/lib/auth/session";
import { isId } from "@/lib/runs";

export const dynamic = "force-dynamic";

/**
 * Attach a file to a chat (gen9-agent's api/files.py): the request's body, named by ?name=. The
 * next message that names it puts it in the chat's environment.
 */
export async function POST(request: Request, { params }: RouteContext<"/api/threads/[id]/files">) {
  if (!isSameOrigin(request)) return Response.json({ detail: "Forbidden" }, { status: 403 });
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });

  const { id } = await params;
  const name = new URL(request.url).searchParams.get("name") ?? "";
  if (!isId(id) || !name) return Response.json({ detail: "Not found" }, { status: 404 });
  const upstream = await agentFetch(session, `/v1/threads/${id}/files?${new URLSearchParams({ name })}`, {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: request.body,
    // @ts-expect-error: Node's fetch streams a request body only with duplex "half"
    duplex: "half",
  });
  return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": "application/json" } });
}
