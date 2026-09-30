import { agentFetch } from "@/lib/agent";
import { sandboxOrigin } from "@/lib/apps";
import { isSameOrigin } from "@/lib/auth/origin";
import { getSession } from "@/lib/auth/session";
import { isId } from "@/lib/runs";

export const dynamic = "force-dynamic";

/**
 * A connector View's resource (MCP Apps): gen9-agent reads it from the server (a `ui://` View's
 * HTML with its CSP); this adds the sandbox origin the View runs on.
 */
export async function POST(request: Request, { params }: RouteContext<"/api/connectors/[id]/app/resource">) {
  if (!isSameOrigin(request)) return Response.json({ detail: "Forbidden" }, { status: 403 });
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });

  const { id } = await params;
  const sandbox = sandboxOrigin(id);
  if (!isId(id) || !sandbox) return Response.json({ detail: "Not found" }, { status: 404 });
  const upstream = await agentFetch(session, `/v1/me/connectors/${id}/app/resource`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await request.text(),
  });
  const body = await upstream.json().catch(() => ({}));
  return Response.json(upstream.ok ? { ...body, sandbox } : body, { status: upstream.status });
}
