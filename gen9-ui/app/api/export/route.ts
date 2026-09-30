import { agentFetch } from "@/lib/agent";
import { getSession } from "@/lib/auth/session";

export const dynamic = "force-dynamic";

/**
 * Your data as one ZIP (gen9-agent's api/export.py; GDPR Art. 20): Settings links here, and the
 * ZIP streams through from gen9-agent, never held by this server.
 */
export async function GET() {
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });
  const upstream = await agentFetch(session, "/v1/me/export", { headers: { Accept: "application/zip" } });
  if (!upstream.ok || !upstream.body) {
    return Response.json({ detail: "Your data couldn’t be gathered. Try again." }, { status: upstream.status === 401 ? 401 : 502 });
  }
  const headers = new Headers({ "Content-Type": "application/zip", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
  headers.set("Content-Disposition", upstream.headers.get("Content-Disposition") ?? 'attachment; filename="gen9-export.zip"');
  return new Response(upstream.body, { headers });
}
