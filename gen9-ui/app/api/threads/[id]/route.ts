import { agentFetch } from "@/lib/agent";
import { isSameOrigin } from "@/lib/auth/origin";
import { getSession } from "@/lib/auth/session";
import { isId } from "@/lib/runs";

export const dynamic = "force-dynamic";

/** The chat with its messages and its active run, for an open chat to pick up a run it didn't start (a background task's notice, gen9-agent's background.py). */
export async function GET(_request: Request, { params }: RouteContext<"/api/threads/[id]">) {
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });

  const { id } = await params;
  if (!isId(id)) return Response.json({ detail: "Not found" }, { status: 404 });
  const upstream = await agentFetch(session, `/v1/threads/${id}`);
  return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": "application/json" } });
}

/** Rename the chat (Chat options, Rename): gen9-agent keeps one line of 1 to 80 characters. */
export async function PATCH(request: Request, { params }: RouteContext<"/api/threads/[id]">) {
  if (!isSameOrigin(request)) return Response.json({ detail: "Forbidden" }, { status: 403 });
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });

  const { id } = await params;
  if (!isId(id)) return Response.json({ detail: "Not found" }, { status: 404 });
  const upstream = await agentFetch(session, `/v1/threads/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: await request.text(),
  });
  return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": "application/json" } });
}
