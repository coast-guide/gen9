import { agentFetch } from "@/lib/agent";
import { isSameOrigin } from "@/lib/auth/origin";
import { getSession } from "@/lib/auth/session";
import { isId } from "@/lib/runs";

export const dynamic = "force-dynamic";

/**
 * One of a chat's files (gen9-agent's chat_files.py), as a download. Code in the chat's environment
 * made it, so it is never shown inline: an HTML or SVG file must not run as this app.
 */
export async function GET(_request: Request, { params }: RouteContext<"/api/threads/[id]/files/[fileId]">) {
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });

  const { id, fileId } = await params;
  if (!isId(id) || !isId(fileId)) return Response.json({ detail: "Not found" }, { status: 404 });
  const upstream = await agentFetch(session, `/v1/threads/${id}/files/${fileId}`, { headers: { Accept: "*/*" } });
  if (!upstream.ok) return Response.json({ detail: upstream.status === 404 ? "Not found" : "Couldn’t get the file." }, { status: upstream.status === 404 ? 404 : 502 });
  return new Response(upstream.body, {
    headers: {
      "Content-Type": upstream.headers.get("Content-Type") ?? "application/octet-stream",
      "Content-Disposition": upstream.headers.get("Content-Disposition") ?? "attachment",
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": "sandbox",
      "Cache-Control": "private, no-store",
    },
  });
}

/** Remove one of the chat's files (an attachment taken back before sending). */
export async function DELETE(request: Request, { params }: RouteContext<"/api/threads/[id]/files/[fileId]">) {
  if (!isSameOrigin(request)) return Response.json({ detail: "Forbidden" }, { status: 403 });
  const session = await getSession();
  if (!session) return Response.json({ detail: "Your session has ended. Sign in again." }, { status: 401 });

  const { id, fileId } = await params;
  if (!isId(id) || !isId(fileId)) return Response.json({ detail: "Not found" }, { status: 404 });
  const upstream = await agentFetch(session, `/v1/threads/${id}/files/${fileId}`, { method: "DELETE" });
  return new Response(null, { status: upstream.status === 404 ? 404 : upstream.ok ? 204 : 502 });
}
