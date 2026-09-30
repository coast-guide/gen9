import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { endedSessionCookie, sessionCookieName } from "@/lib/auth/cookies";
import { client, oidc } from "@/lib/auth/oidc";
import { isSameOrigin } from "@/lib/auth/origin";
import { deleteSession, readSession, type SessionRecord } from "@/lib/auth/store";
import { env } from "@/lib/env";

/**
 * Sign out: end the app session, then RP-initiated logout at Keycloak (ends the SSO session and
 * notifies other clients), returning to /signed-out. POST only, same origin only.
 */
export async function POST(request: Request) {
  if (!isSameOrigin(request)) return new NextResponse("Forbidden", { status: 403 });

  const name = sessionCookieName();
  const id = (await cookies()).get(name)?.value;
  let record: SessionRecord | null = null;
  if (id) {
    try {
      record = await readSession(id);
      await deleteSession(id);
    } catch (error) {
      // The session store (Valkey) not answering: signed out all the same, here and at Keycloak
      // (which then asks to confirm, having no ID token); the record expires with its refresh
      // token. It was a bare 500, and the person stayed signed in (P4-E4)
      console.error("[auth/logout] session store unavailable", error);
    }
  }

  // Always end the Keycloak session too, even if this app's session already expired: otherwise the
  // next "Sign in" on a shared computer would get straight back in. With the ID token Keycloak
  // logs out at once; without it, Keycloak asks the user to confirm (Gen9 theme), then returns.
  const signedOut = new URL("/signed-out", env().APP_URL).href;
  const destination = client.buildEndSessionUrl(await oidc(), {
    post_logout_redirect_uri: signedOut,
    ...(record?.idToken ? { id_token_hint: record.idToken } : {}),
  }).href;
  const response = NextResponse.redirect(destination, 303);
  response.cookies.set(endedSessionCookie());
  response.headers.set("Cache-Control", "no-store");
  return response;
}
