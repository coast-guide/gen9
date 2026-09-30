import "server-only";

import type { Session } from "@/lib/auth/session";
import { env } from "@/lib/env";

/**
 * Keycloak's Account REST API (what its own account console uses), called with the user's access
 * token: it only ever shows or ends that user's own sessions. Docs:
 * https://www.keycloak.org/docs-api/latest/rest-api/ (account endpoints: SessionResource).
 */
function accountUrl(path: string): URL {
  const { KEYCLOAK_ISSUER, KEYCLOAK_INTERNAL_URL } = env();
  const issuer = new URL(KEYCLOAK_ISSUER);
  // Same back-channel origin as OIDC discovery (gen9-keycloak:8080 in containers)
  const base = KEYCLOAK_INTERNAL_URL ? new URL(issuer.pathname, KEYCLOAK_INTERNAL_URL) : issuer;
  return new URL(`${base.pathname}/account${path}`, base);
}

async function account(session: Pick<Session, "accessToken">, path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(accountUrl(path), {
    ...init,
    headers: { Authorization: `Bearer ${session.accessToken}`, Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Keycloak account API ${init.method ?? "GET"} ${path}: HTTP ${response.status}`);
  return response;
}

/**
 * One Keycloak session: a browser, plus any terminal (Gen9 CLI) whose code was approved in it.
 * Keycloak reports times in seconds; these are epoch ms.
 */
export type SignedInBrowser = {
  id: string;
  browser: string;
  os: string;
  mobile: boolean;
  ipAddress: string;
  startedMs: number;
  lastAccessMs: number;
  current: boolean;
  web: boolean; // the Gen9 web app uses this session
  cli: boolean; // Gen9 CLI signed in through it (device flow)
};

type DeviceRepresentation = {
  os: string;
  mobile: boolean;
  sessions: {
    id: string;
    browser: string;
    ipAddress: string;
    started: number;
    lastAccess: number;
    current?: boolean;
    clients?: { clientId: string }[];
  }[];
};

// Keycloak names come from its user-agent parser ("Mac OS X", "Chrome/153.0.8010.53")
const OS_NAMES: Record<string, string> = { "Mac OS X": "macOS", "Other": "an unknown system" };

export async function signedInBrowsers(session: Pick<Session, "accessToken">): Promise<SignedInBrowser[]> {
  const devices = (await (await account(session, "/sessions/devices")).json()) as DeviceRepresentation[];
  return devices
    .flatMap((device) =>
      device.sessions.map((s) => ({
        id: s.id,
        browser: s.browser.split("/")[0] || "A browser",
        os: OS_NAMES[device.os] ?? device.os,
        mobile: device.mobile,
        ipAddress: s.ipAddress,
        startedMs: s.started * 1000,
        lastAccessMs: s.lastAccess * 1000,
        current: s.current === true,
        web: s.clients?.some((c) => c.clientId === "gen9-ui") ?? true,
        cli: s.clients?.some((c) => c.clientId === "gen9-cli") ?? false,
      })),
    )
    .sort((a, b) => Number(b.current) - Number(a.current) || b.lastAccessMs - a.lastAccessMs);
}

/** Ends every session of the user except the one this token belongs to. */
export async function endOtherBrowserSessions(session: Pick<Session, "accessToken">): Promise<void> {
  await account(session, "/sessions", { method: "DELETE" });
}

/** Ends one of the user's sessions; Keycloak then notifies apps by back-channel logout. */
export async function endBrowserSession(session: Pick<Session, "accessToken">, id: string): Promise<void> {
  await account(session, `/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** An app the person allowed to use their account: Keycloak keeps the consent. Times in epoch ms. */
export type AccountApp = { clientId: string; name: string; consentTexts: string[]; allowedMs: number | null; changedMs: number | null };

type ClientRepresentation = {
  clientId: string;
  clientName?: string;
  consent?: { grantedScopes?: { name: string }[]; createdDate?: number; lastUpdatedDate?: number } | null;
};

/**
 * The apps the person allowed (Gen9 CLI, MCP clients and A2A agents, clients registered by their
 * metadata document): the account API's applications that hold a consent, last changed first.
 * The web app itself holds none: it's the person's own sign-in.
 */
export async function appsWithAccess(session: Pick<Session, "accessToken">): Promise<AccountApp[]> {
  const clients = (await (await account(session, "/applications")).json()) as ClientRepresentation[];
  return clients
    .filter((client) => client.consent)
    .map((client) => ({
      clientId: client.clientId,
      name: client.clientName || client.clientId,
      // Each scope's consent text, or its name when it has none (Keycloak's AccountRestService)
      consentTexts: (client.consent?.grantedScopes ?? []).map((scope) => scope.name),
      allowedMs: client.consent?.createdDate ?? null,
      changedMs: client.consent?.lastUpdatedDate ?? null,
    }))
    .sort((a, b) => (b.changedMs ?? 0) - (a.changedMs ?? 0));
}

/**
 * Takes back an app's access: Keycloak removes the consent, ends the app's offline tokens and
 * signs it out of the person's sessions, so its refresh token is refused (probed:
 * docs/plans/gen9-learn.md, M9, F12). An access token it holds lasts out its 5 minutes.
 */
export async function removeAppAccess(session: Pick<Session, "accessToken">, clientId: string): Promise<void> {
  await account(session, `/applications/${encodeURIComponent(clientId)}/consent`, { method: "DELETE" });
}
