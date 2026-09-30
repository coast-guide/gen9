import { type NextRequest, NextResponse } from "next/server";

import { kcAction, removingKind } from "@/lib/auth/account-actions";
import { TRANSACTION_COOKIE, transactionCookieOptions } from "@/lib/auth/cookies";
import { client, keycloakAnswers, oidc, redirectUri } from "@/lib/auth/oidc";
import { safeReturnTo } from "@/lib/auth/return-to";
import { saveTransaction } from "@/lib/auth/store";
import { env } from "@/lib/env";

/**
 * Starts an Authorization Code flow with PKCE (S256), state and nonce.
 *   /auth/login?returnTo=/chat          sign in
 *   /auth/login?intent=signup           open Keycloak's registration page (OIDC prompt=create)
 *   /auth/login?action=UPDATE_PASSWORD  run an account action, then return
 *   /auth/login?action=delete_credential:<id>&removing=passkey  remove a passkey, authenticator app
 *                                       or recovery codes, then return (removing: what, for the toast)
 *   /auth/login?reauth=1                sign in again even with a Keycloak session (OIDC max_age=0)
 */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const action = params.get("action");
  const returnTo = safeReturnTo(params.get("returnTo"), action ? "/settings" : "/chat");

  const accountAction = kcAction(action);
  // Keycloak down: say so in Gen9's words, rather than send the browser to "refused to connect"
  if (!(await keycloakAnswers())) {
    return NextResponse.redirect(new URL("/auth/error?reason=temporarily_unavailable", env().APP_URL), 303);
  }

  const codeVerifier = client.randomPKCECodeVerifier();
  const state = client.randomState();
  const nonce = client.randomNonce();
  const removing = accountAction?.startsWith("delete_credential:") ? removingKind(params.get("removing")) : undefined;
  try {
    await saveTransaction(state, { codeVerifier, nonce, returnTo, action: accountAction, removing, createdAt: Date.now() });
  } catch (error) {
    // The session store (Valkey) not answering, restarting say: Gen9's words too, not the
    // browser's bare "HTTP ERROR 500" (docs/plans/manual-e2e.md, P4-E4)
    console.error("[auth/login] session store unavailable", error);
    return NextResponse.redirect(new URL("/auth/error?reason=temporarily_unavailable", env().APP_URL), 303);
  }

  const authorizationParams: Record<string, string> = {
    redirect_uri: redirectUri(),
    scope: "openid profile email",
    code_challenge: await client.calculatePKCECodeChallenge(codeVerifier),
    code_challenge_method: "S256",
    state,
    nonce,
  };
  if (params.get("intent") === "signup") authorizationParams.prompt = "create";
  // Before sensitive actions: Keycloak asks for the password (or passkey) and updates auth_time
  if (params.get("reauth") === "1") authorizationParams.max_age = "0";
  if (accountAction) authorizationParams.kc_action = accountAction;
  const loginHint = params.get("login_hint");
  if (loginHint && loginHint.length <= 320) authorizationParams.login_hint = loginHint;

  const response = NextResponse.redirect(client.buildAuthorizationUrl(await oidc(), authorizationParams));
  response.cookies.set(TRANSACTION_COOKIE, state, transactionCookieOptions());
  response.headers.set("Cache-Control", "no-store");
  return response;
}
