import { agentFetch } from "@/lib/agent";
import { getSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

/**
 * The person's chats, for the sidebar to refresh itself while a run goes on (a router refresh
 * would re-render the chat page, and a new chat's page mounts anew, losing what the person has
 * typed into a card).
 */
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });

  const upstream = await agentFetch(session, "/v1/threads");
  return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": "application/json" } });
}
