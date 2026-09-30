import "server-only";

import { decodeJwt } from "jose";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { cache } from "react";

import { sessionCookieName } from "@/lib/auth/cookies";
import { PAGE_HEADER } from "@/lib/auth/page-header";
import { client, oidc } from "@/lib/auth/oidc";
import {
  deleteSession,
  readSession,
  saveSession,
  type SessionRecord,
  withSessionLock,
} from "@/lib/auth/store";

export type Session = {
  user: { sub: string; email: string | null; name: string | null; givenName: string | null };
  roles: string[];
  isAdmin: boolean;
  accessToken: string;
  sessionId: string;
  authTime: number | null; // epoch s of the last real sign-in (password, passkey...), not refreshes
};

const REFRESH_MARGIN_MS = 30_000;

/** Build a session record from a token endpoint response (login or refresh). */
export function recordFromTokens(
  tokens: client.TokenEndpointResponse & client.TokenEndpointResponseHelpers,
  previous?: SessionRecord,
): SessionRecord {
  const idClaims = tokens.claims();
  const access = decodeJwt(tokens.access_token); // from the token endpoint over the back-channel
  const now = Date.now();
  const refreshExpiresIn = Number((tokens as Record<string, unknown>).refresh_expires_in ?? 0);
  return {
    sub: String(access.sub),
    sid: (access.sid as string | undefined) ?? previous?.sid ?? null,
    email: (idClaims?.email as string | undefined) ?? previous?.email ?? null,
    name: (idClaims?.name as string | undefined) ?? previous?.name ?? null,
    givenName: (idClaims?.given_name as string | undefined) ?? previous?.givenName ?? null,
    roles: ((access.realm_access as { roles?: string[] } | undefined)?.roles ?? []).filter((r) => r.startsWith("gen9-")),
    accessToken: tokens.access_token,
    accessTokenExpiresAt: now + (tokens.expires_in ?? 300) * 1000,
    refreshToken: tokens.refresh_token ?? previous?.refreshToken ?? null,
    refreshTokenExpiresAt: refreshExpiresIn ? now + refreshExpiresIn * 1000 : now + (tokens.expires_in ?? 300) * 1000,
    idToken: tokens.id_token ?? previous?.idToken ?? "",
    authTime: (idClaims?.auth_time as number | undefined) ?? previous?.authTime ?? null,
    createdAt: previous?.createdAt ?? now,
  };
}

async function refresh(id: string, record: SessionRecord): Promise<SessionRecord | null> {
  if (!record.refreshToken) return null;
  const refreshed = await withSessionLock(id, async () => {
    try {
      const tokens = await client.refreshTokenGrant(await oidc(), record.refreshToken!);
      const next = recordFromTokens(tokens, record);
      await saveSession(id, next);
      return next;
    } catch (error) {
      // invalid_grant: Keycloak ended the session (logout, admin, idle/max timeout, disabled user)
      if (error instanceof client.ResponseBodyError && error.error === "invalid_grant") {
        await deleteSession(id);
        return null;
      }
      throw error;
    }
  });
  if (refreshed !== undefined) return refreshed;
  // Another request is refreshing this session: wait for its result
  for (let i = 0; i < 20; i++) {
    await new Promise((resolve) => setTimeout(resolve, 150));
    const latest = await readSession(id);
    if (!latest) return null;
    if (latest.accessTokenExpiresAt - Date.now() > REFRESH_MARGIN_MS) return latest;
  }
  return null;
}

function toSession(id: string, record: SessionRecord): Session {
  return {
    user: { sub: record.sub, email: record.email, name: record.name, givenName: record.givenName },
    roles: record.roles,
    isAdmin: record.roles.includes("gen9-admin"),
    accessToken: record.accessToken,
    sessionId: id,
    authTime: record.authTime,
  };
}

/**
 * The data access layer's auth check (Next.js 16 guidance: verify close to the data, not in proxy).
 * Reads the server-side session, refreshing the access token shortly before it expires.
 * Memoized per request.
 */
export const getSession = cache(async (): Promise<Session | null> => {
  const id = (await cookies()).get(sessionCookieName())?.value;
  if (!id) return null;
  let record = await readSession(id);
  if (!record) return null;
  if (record.accessTokenExpiresAt - Date.now() < REFRESH_MARGIN_MS) {
    record = await refresh(id, record);
    if (!record) return null;
  }
  return toSession(id, record);
});

/**
 * Refreshes the session's tokens now, however long they have left. A session's roles come from its
 * token, so an admin whose access was removed kept the admin links until it expired, while
 * gen9-agent (which asks Keycloak) already refused them (manual-e2e.md, P3-D7); Keycloak issues the
 * refreshed token with the roles they have now. For that refusal only, not every request.
 */
export async function refreshRoles(): Promise<void> {
  const id = (await cookies()).get(sessionCookieName())?.value;
  if (!id) return;
  const record = await readSession(id);
  if (record) await refresh(id, record);
}

/**
 * For pages: signed-out users go to Keycloak and come back to the page as it was asked for, query
 * included (the proxy names it), or else to `returnTo`.
 */
export async function requireSession(returnTo: string): Promise<Session> {
  const session = await getSession();
  if (!session) {
    const page = (await headers()).get(PAGE_HEADER) ?? returnTo;
    redirect(`/auth/login?returnTo=${encodeURIComponent(page)}`);
  }
  return session;
}
