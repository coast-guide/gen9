import { NextResponse } from "next/server";

import { agentFetch } from "@/lib/agent";
import { getSession } from "@/lib/auth/session";
import { env } from "@/lib/env";

export const dynamic = "force-dynamic";

function back(query: Record<string, string>): NextResponse {
  const url = new URL("/settings", env().APP_URL);
  for (const [key, value] of Object.entries(query)) url.searchParams.set(key, value);
  url.hash = "connectors";
  return NextResponse.redirect(url, 303);
}

/**
 * Where a connector's sign-in returns (MCP authorization, gen9-agent's connector_auth.py): the
 * authorization server sends the person's browser here with `code`, `state` and `iss`. gen9-agent
 * checks them (the state is this person's, used once, under ten minutes; `iss` is the server it
 * was sent to), exchanges the code and keeps the tokens sealed. Then back to Settings.
 */
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const session = await getSession();
  if (!session) return NextResponse.redirect(new URL("/auth/login?returnTo=/settings", env().APP_URL), 303);
  const error = params.get("error");
  const code = params.get("code");
  const state = params.get("state");
  if (error || !code || !state) {
    // Denied or cancelled at the server: nothing to exchange
    return back({ sign_in: error === "access_denied" ? "denied" : "failed" });
  }
  const response = await agentFetch(session, "/v1/me/connectors/sign-in/callback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, state, ...(params.get("iss") ? { iss: params.get("iss") } : {}) }),
  });
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({})))?.detail;
    return back({ sign_in: "failed", ...(typeof detail === "string" ? { reason: detail.slice(0, 200) } : {}) });
  }
  const connector = await response.json();
  return back({ sign_in: "done", connector: String(connector.name ?? "") });
}
