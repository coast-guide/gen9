import "server-only";

import { isSecureOrigin } from "@/lib/env";

/**
 * Session cookie: only a random id (the session itself lives server-side, RFC 10017 BFF).
 * HttpOnly, SameSite=Lax (sent on top-level navigation back from Keycloak, not on cross-site
 * POSTs). Over https the __Host- prefix also pins it to this origin, path / and Secure.
 */
export function sessionCookieName(): string {
  return isSecureOrigin() ? "__Host-gen9_session" : "gen9_session";
}

/**
 * The cookie is only a pointer: the server-side session decides when it ends (its TTL slides with
 * every token refresh). With "Remember me" it gets a fixed upper bound matching Keycloak's longest
 * session (30 days); a cookie that outlives its session simply finds nothing. Without it, the
 * cookie has no Max-Age and ends with the browser, as Keycloak's own does: someone who didn't ask
 * to be remembered, on a shared computer, isn't signed in by the next person to open it (OWASP's
 * Session Management Cheat Sheet: non-persistent cookies for sessions).
 */
const SESSION_COOKIE_MAX_AGE = 30 * 24 * 60 * 60;

export function sessionCookieOptions(remembered: boolean) {
  return {
    httpOnly: true,
    secure: isSecureOrigin(),
    sameSite: "lax" as const,
    path: "/",
    ...(remembered ? { maxAge: SESSION_COOKIE_MAX_AGE } : {}),
  };
}

/**
 * The session cookie's end: the same name and attributes, empty and expired. A browser ignores a
 * `__Host-` cookie set without `Secure`, the expired one sign-out sends included (RFC 6265bis,
 * cookie prefixes), so over https a bare delete left the cookie behind (manual-e2e.md, P3-C9).
 */
export function endedSessionCookie() {
  return { name: sessionCookieName(), value: "", ...sessionCookieOptions(false), maxAge: 0 };
}

/** Binds an in-flight login to this browser (login CSRF protection): holds the OAuth `state`. */
export const TRANSACTION_COOKIE = "gen9_auth_txn";

export function transactionCookieOptions() {
  return { httpOnly: true, secure: isSecureOrigin(), sameSite: "lax" as const, path: "/auth", maxAge: 600 };
}

/** The transaction cookie's end, with its attributes, as for the session's. */
export function endedTransactionCookie() {
  return { name: TRANSACTION_COOKIE, value: "", ...transactionCookieOptions(), maxAge: 0 };
}
