import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import { agentFetch } from "@/lib/agent";
import { doneOf } from "@/lib/auth/account-actions";
import { randomId } from "@/lib/auth/crypto";
import { endedTransactionCookie, sessionCookieName, sessionCookieOptions, TRANSACTION_COOKIE } from "@/lib/auth/cookies";
import { client, oidc, redirectUri } from "@/lib/auth/oidc";
import { rememberedSession } from "@/lib/auth/remember-me";
import { recordFromTokens } from "@/lib/auth/session";
import { deleteSession, saveSession, takeTransaction } from "@/lib/auth/store";
import { env } from "@/lib/env";

function redirectTo(path: string): NextResponse {
  const response = NextResponse.redirect(new URL(path, env().APP_URL), 303);
  response.headers.set("Cache-Control", "no-store");
  response.cookies.set(endedTransactionCookie());
  return response;
}

/** Redirect target registered in Keycloak: exchanges the code and starts a server-side session. */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const cookieStore = await cookies();
  const state = cookieStore.get(TRANSACTION_COOKIE)?.value;

  // The state must come back to the same browser that started the login (login CSRF)
  if (!state || params.get("state") !== state) return redirectTo("/auth/error?reason=state");
  let txn;
  try {
    txn = await takeTransaction(state);
  } catch (error) {
    // The session store (Valkey) not answering: Gen9's words, not a bare 500 (P4-E4)
    console.error("[auth/callback] session store unavailable", error);
    return redirectTo("/auth/error?reason=temporarily_unavailable");
  }
  if (!txn) return redirectTo("/auth/error?reason=expired");

  // Keycloak reports errors here too (e.g. the user cancelled an account action)
  const error = params.get("error");
  if (error) {
    const cancelled = params.get("kc_action_status") === "cancelled" || error === "access_denied";
    if (cancelled) return redirectTo(txn.returnTo);
    return redirectTo(`/auth/error?reason=${encodeURIComponent(error)}`);
  }

  let tokens;
  try {
    // redirect_uri must match exactly, so rebuild the callback URL on the public origin
    const callbackUrl = new URL(redirectUri());
    callbackUrl.search = request.nextUrl.search;
    tokens = await client.authorizationCodeGrant(await oidc(), callbackUrl, {
      pkceCodeVerifier: txn.codeVerifier,
      expectedState: state,
      expectedNonce: txn.nonce,
      idTokenExpected: true,
    });
  } catch (exchangeError) {
    console.error("[auth/callback] code exchange failed", exchangeError);
    return redirectTo("/auth/error?reason=exchange");
  }

  // New session id on every sign-in (no session fixation); drop the previous one
  const previous = cookieStore.get(sessionCookieName())?.value;
  const id = randomId();
  const record = recordFromTokens(tokens);
  try {
    if (previous) await deleteSession(previous);
    await saveSession(id, record);
  } catch (error) {
    console.error("[auth/callback] session store unavailable", error);
    return redirectTo("/auth/error?reason=temporarily_unavailable");
  }

  const status = params.get("kc_action_status");
  // Recovery codes back up an authenticator app; once the last app is removed they go too, or
  // Keycloak would ask for one at every sign-in
  let prunedCodes = false;
  if (status === "success" && txn.action?.startsWith("delete_credential:")) {
    const pruned = await agentFetch(record, "/v1/me/recovery-codes/prune", { method: "POST" }).catch((e: unknown) => e);
    if (!(pruned instanceof Response) || !pruned.ok) console.error("[auth/callback] recovery codes prune failed", pruned);
    else prunedCodes = ((await pruned.json().catch(() => null)) as { removed?: boolean } | null)?.removed === true;
  }
  const destination = new URL(txn.returnTo, env().APP_URL);
  // What was done, so Settings can name it; appended, as a first app's set-up then saves its codes
  const done = status === "success" ? doneOf(txn.action, txn.removing) : undefined;
  if (done) destination.searchParams.append("updated", done);
  if (prunedCodes) destination.searchParams.append("updated", "pruned-recovery-codes");
  const response = redirectTo(`${destination.pathname}${destination.search}`);
  // "Remember me" keeps the cookie past closing the browser; without it, it ends with the browser
  const refreshExpiresIn = Number((tokens as Record<string, unknown>).refresh_expires_in ?? 0);
  response.cookies.set(sessionCookieName(), id, sessionCookieOptions(rememberedSession(refreshExpiresIn)));
  return response;
}
